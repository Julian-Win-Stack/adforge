"""The producer's owner tools (spec #247 points 5, 11, 12; ticket #260): ask_owner puts one
question to the shop owner in the chat and ends the producer's turn, and the shop owner's
next message answers it; the owner-part yes arrives in words, the producer's approve call
kept against the message that said it. Driven through the chat, with the producer's model
and every other model faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models.signals import pre_save

from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import chat, given_to_the_producer, producer_turns, results_of
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

QUESTION = "Which colour should the ad show: the blue mug or the green one?"


def test_ask_owner_shows_the_question_in_the_chat_and_ends_the_turn(
    fake_model: FakeModel, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))

    say("Make an ad for my mug")

    assert chat(api, session_id)[-1] == ("agent", QUESTION)
    # No second turn: the producer waits for the answer.
    assert producer_turns() == 1
    (asked,) = ToolCall.objects.filter(tool="ask_owner")
    assert asked.answer == "made"


def test_the_shop_owners_next_message_answers_the_question(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))
    say("Make an ad for my mug")
    fake_model.respond("produce", turn(says="The green one, then."))

    say("The green one")

    given = given_to_the_producer(2)
    asked = next(
        number
        for number, each in enumerate(given)
        if each["kind"] == "tool_use" and each["tool"] == "ask_owner"
    )
    # The question as the chat showed it, then the shop owner's answer, and nothing between.
    question, answer = given[asked + 1 :]
    assert (question["by"], question["text"]) == ("agent", QUESTION)
    assert answer["by"] == "user"
    assert "The green one" in answer["text"]
    assert producer_turns() == 2


def test_a_call_after_the_question_in_the_same_turn_is_refused(
    fake_model: FakeModel, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("ask_owner", {"question": QUESTION}), ("plan_ad", {})]),
    )

    say("Make an ad for my mug")

    (refused,) = results_of("plan_ad")
    assert refused.startswith("Refused: your ask_owner call earlier in this turn ended it")
    assert not Job.objects.exists() or not Job.objects.get().scenes.exists()
    assert chat(api, session_id)[-1] == ("agent", QUESTION)
    assert producer_turns() == 1


def test_a_blank_question_is_refused_and_the_turn_goes_on(
    fake_model: FakeModel, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("ask_owner", {"question": "  "})]),
        turn(says="What would you like an ad for?"),
    )

    say("Hi")

    (refused,) = results_of("ask_owner")
    assert refused.startswith("Refused:")
    assert chat(api, session_id)[-1] == ("agent", "What would you like an ad for?")
    assert producer_turns() == 2


def test_a_producer_that_died_while_asking_asks_once_and_takes_no_turn_on_restart(
    fake_model: FakeModel, say: Callable[..., None], api: Any, session_id: str
) -> None:
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))
    # It dies once the question is in the chat, before the call is kept as finished.
    asked = the_worker_stops(
        pre_save, ToolCall, when=lambda call: call.tool == "ask_owner" and call.finished_at
    )
    with asked, pytest.raises(WorkerStopped):
        say("Make an ad for my mug")
    the_producer_died(session_id)
    # What it would say, were it wrongly given a turn.
    fake_model.respond("produce", turn(says="Still there?"))

    restart_dead_producers()

    assert [text for role, text in chat(api, session_id) if role == "agent"] == [QUESTION]
    assert producer_turns() == 1
    assert ToolCall.objects.get(tool="ask_owner").finished_at is not None
