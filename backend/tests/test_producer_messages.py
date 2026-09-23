"""Interrupts: messages the user sends while the producer works, driven through the chat
the way the browser uses it. Only one producer works in a session at a time: an interrupt
is stored straight away, and the producer reads it when its model call returns."""

import time
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from pytest_django import Settings
from rest_framework.test import APIClient

from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from chat.models import Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall

from .conftest import READABLE, chat, given_to_the_producer

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def producer_turns() -> int:
    """How many turns the producer's model has taken, over every producer that ran."""
    return ModelCall.objects.filter(purpose="produce").count()


def a_producer_last_beat(session_id: str, *, seconds_before_it_counts_as_dead: float) -> None:
    """As if a producer is working in the session, and its last beat was this long before
    it counts as dead. Less than nothing means it already does."""
    ago = settings.PRODUCER_DEAD_AFTER_SECONDS - seconds_before_it_counts_as_dead
    Session.objects.filter(pk=session_id).update(
        producer_running=True, producer_seen_at=timezone.now() - timedelta(seconds=ago)
    )


def what_the_user_said(given: list[dict[str, object]]) -> list[object]:
    """What the user said in a turn's conversation, in order."""
    return [each["text"] for each in given if each["kind"] == "said" and each["by"] == "user"]


def test_an_interrupt_is_taken_and_read_by_the_producer_already_working(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        meanwhile(
            lambda: say("Make it 10 seconds, please."),
            turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        ),
        turn(says="Got it: a 10 second ad."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    # One producer: its two turns, and no third from a second producer.
    assert producer_turns() == 2
    read_page = ToolCall.objects.get(tool="read_page")
    assert [
        (each["kind"], each.get("text") or each.get("call_id")) for each in given_to_the_producer(2)
    ] == [
        ("said", f"Make an ad for {product_page_url}"),
        ("said", "Make it 10 seconds, please."),
        ("tool_use", read_page.call_id),
    ]
    assert chat(api, session_id)[-1] == ("agent", "Got it: a 10 second ad.")


def test_once_the_producer_has_replied_the_next_message_starts_it_again(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    say("Hi")
    assert not Session.objects.get(pk=session_id).producer_running
    fake_model.respond("produce", turn(says="A mug: send me its page."))

    say("A mug")

    assert producer_turns() == 2
    assert chat(api, session_id)[-1] == ("agent", "A mug: send me its page.")


def test_an_interrupt_sent_while_the_producer_writes_its_reply_is_answered_before_it_stops(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        meanwhile(lambda: say("It's a mug."), turn(says="What would you like an ad for?")),
        turn(says="A mug: send me its page."),
    )

    say("Hi")

    assert producer_turns() == 2
    assert what_the_user_said(given_to_the_producer(2)) == ["Hi", "It's a mug."]
    assert chat(api, session_id)[-2:] == [
        ("agent", "What would you like an ad for?"),
        ("agent", "A mug: send me its page."),
    ]


def test_two_interrupts_sent_during_one_turn_are_both_given_to_the_next_in_order(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    def the_user_sends_two_messages() -> None:
        say("Make it 10 seconds.")
        say("And cheerful, please.")

    fake_model.respond(
        "produce",
        meanwhile(
            the_user_sends_two_messages,
            turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        ),
        turn(says="A cheerful 10 second ad, coming up."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    assert producer_turns() == 2
    assert what_the_user_said(given_to_the_producer(2)) == [
        f"Make an ad for {product_page_url}",
        "Make it 10 seconds.",
        "And cheerful, please.",
    ]


def test_when_the_producer_has_to_stop_the_next_message_tries_again(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", RuntimeError("the model fell over"))
    say("Hi")
    assert chat(api, session_id)[-1][1].startswith("I had to stop: ")
    assert not Session.objects.get(pk=session_id).producer_running
    fake_model.respond("produce", turn(says="What would you like an ad for?"))

    say("Hello again")

    assert chat(api, session_id)[-1] == ("agent", "What would you like an ad for?")


def test_a_dead_producer_is_started_again_with_what_was_sent_meanwhile(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=60)
    say("Make me an ad for my mug")
    assert producer_turns() == 0  # The producer working in the session will read it.
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)
    fake_model.respond("produce", turn(says="Happy to: what's the link to your mug?"))

    restart_dead_producers()

    assert what_the_user_said(given_to_the_producer(1)) == ["Make me an ad for my mug"]
    assert chat(api, session_id)[-1] == ("agent", "Happy to: what's the link to your mug?")


def test_a_producer_that_beat_recently_is_left_to_work(
    api: APIClient, fake_model: FakeModel, session_id: str
) -> None:
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=10)
    # What a second producer would say, were one wrongly started.
    fake_model.respond("produce", turn(says="What would you like an ad for?"))

    restart_dead_producers()

    assert producer_turns() == 0
    assert chat(api, session_id) == []


def test_a_message_sent_to_a_dead_producer_starts_it_again(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)
    fake_model.respond("produce", turn(says="Happy to: what's the link to your mug?"))

    say("Make me an ad for my mug")

    assert chat(api, session_id)[-1] == ("agent", "Happy to: what's the link to your mug?")


def test_the_producer_beats_while_it_works_and_stops_beating_when_it_ends(
    fake_model: FakeModel, session_id: str, say: Callable[..., None], settings: Settings
) -> None:
    settings.PRODUCER_HEARTBEAT_SECONDS = 0.05

    def last_beat() -> datetime | None:
        return Session.objects.get(pk=session_id).producer_seen_at

    beats: list[datetime | None] = []

    def the_producer_thinks_for_a_while() -> None:
        beats.append(last_beat())
        time.sleep(0.3)
        beats.append(last_beat())

    fake_model.respond("produce", meanwhile(the_producer_thinks_for_a_while, turn(says="Hello!")))

    say("Hi")
    ended = last_beat()
    time.sleep(0.3)

    assert beats[0] is not None and beats[1] is not None
    assert beats[1] > beats[0]
    assert last_beat() == ended
