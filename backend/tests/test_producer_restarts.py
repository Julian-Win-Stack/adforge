"""A producer whose worker stopped, started again. A worker can stop at any moment, and
the producer started again carries on from what was written down, without anything being
produced or paid for twice. A test stops the worker at one moment with WorkerStopped, which
nothing catches, as nothing runs after a killed worker."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.db import connection
from django.db.models import Model
from django.db.models.signals import ModelSignal, post_save
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    READABLE,
    a_producer_last_beat,
    chat,
    given_to_the_producer,
    producer_turns,
)

pytestmark = pytest.mark.django_db(transaction=True)


class WorkerStopped(BaseException):
    """The worker running the producer was killed."""


@contextmanager
def the_worker_stops(
    signal: ModelSignal, sender: type[Model], *, when: Callable[[Any], bool]
) -> Iterator[None]:
    """Kill the worker the first time `signal` is sent for a `sender` row that `when` picks:
    pre_save to stop it just before the row is written, post_save just after."""

    def stop(instance: Any, **_: Any) -> None:
        if when(instance):
            signal.disconnect(stop, sender=sender)
            raise WorkerStopped

    signal.connect(stop, sender=sender, weak=False)
    try:
        yield
    finally:
        signal.disconnect(stop, sender=sender)


def the_producer_died(session_id: str) -> None:
    """The producer's heartbeat has stopped long enough for it to count as dead."""
    a_producer_last_beat(session_id, seconds_before_it_counts_as_dead=-1)


def times_paid_for(purpose: str) -> int:
    """How many calls for `purpose` were paid for, over every producer that ran."""
    return ModelCall.objects.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED).count()


def test_a_producer_that_replied_then_died_is_started_again_takes_no_turn_and_stops(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    say("Hi")
    # It died after replying, before it turned its flag off.
    the_producer_died(session_id)
    # What it would say again, were it wrongly given a turn.
    fake_model.respond("produce", turn(says="What would you like an ad for?"))

    restart_dead_producers()

    assert producer_turns() == 1
    assert chat(api, session_id) == [("user", "Hi"), ("agent", "What would you like an ad for?")]
    assert not Session.objects.get(pk=session_id).producer_running


def test_a_message_sent_as_a_producer_started_again_finds_nothing_to_do_is_answered(
    api: APIClient, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    say("Hi")
    the_producer_died(session_id)
    sent = False

    def the_user_sends_a_message_as_it_decides(
        execute: Callable[..., Any], sql: str, *args: Any
    ) -> Any:
        # Just after the producer started again looks up its last turn, to see whether
        # anything has come since: it has read the conversation without this message.
        nonlocal sent
        done = execute(sql, *args)
        if not sent and sql.startswith("SELECT") and '"gateway_modelcall"' in sql:
            sent = True
            say("A mug")
        return done

    fake_model.respond("produce", turn(says="A mug: send me its page."))

    with connection.execute_wrapper(the_user_sends_a_message_as_it_decides):
        restart_dead_producers()

    assert sent
    assert producer_turns() == 2
    assert chat(api, session_id)[-2:] == [
        ("user", "A mug"),
        ("agent", "A mug: send me its page."),
    ]
    assert not Session.objects.get(pk=session_id).producer_running


def test_a_producer_that_died_after_its_tools_finished_takes_the_turn_it_had_left(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        WorkerStopped(),
    )
    fake_model.respond("check_page", READABLE)
    with pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I read your mug's page."))

    restart_dead_producers()

    assert producer_turns() == 2
    assert times_paid_for("check_page") == 1
    assert chat(api, session_id)[-1] == ("agent", "I read your mug's page.")
    assert not Session.objects.get(pk=session_id).producer_running


def test_a_turn_paid_for_when_the_worker_stopped_is_taken_again_without_paying(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    read_page = ("read_page", {"link": product_page_url, "target_seconds": None})
    fake_model.respond("produce", turn(says="I'll read your mug's page.", calls=[read_page]))
    producer_turn_recorded = the_worker_stops(
        post_save, ModelCall, when=lambda call: call.purpose == "produce"
    )
    with producer_turn_recorded, pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    the_producer_died(session_id)
    fake_model.respond("check_page", READABLE)
    # Only the turn after it is scripted: paying for the stopped turn again would take this.
    fake_model.respond("produce", turn(says="I read your mug's page."))

    restart_dead_producers()

    # The turn paid for before the worker stopped, and the one after it: paid for as usual.
    assert producer_turns() == 2
    assert times_paid_for("check_page") == 1
    given = given_to_the_producer(2)[-1]
    assert (given["kind"], given["tool"]) == ("tool_use", "read_page")
    assert ToolCall.objects.get().tool == "read_page"
    assert chat(api, session_id) == [
        ("user", f"Make an ad for {product_page_url}"),
        ("agent", "I'll read your mug's page."),
        ("agent", "I read your mug's page."),
    ]


def test_a_tool_the_worker_stopped_halfway_through_is_finished_without_paying_twice(
    api: APIClient,
    fake_model: FakeModel,
    planned: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(calls=[("create_person", {})]))
    # The portrait is drawn, then the worker stops while the voice is being designed.
    fake_model.respond("design_voice", WorkerStopped())
    with pytest.raises(WorkerStopped):
        say("Make the person")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Meet your presenter!"))

    restart_dead_producers()

    assert [times_paid_for(each) for each in ("draw_person", "design_voice", "measure_voice")] == [
        1,
        1,
        1,
    ]
    # Two turns each to read the page and plan, the one asking for the person, and its reply.
    assert producer_turns() == 6
    assert ToolCall.objects.get(tool="create_person").finished
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert len([message for message in messages if message["attachments"]]) == 1
    assert chat(api, session_id)[-1] == ("agent", "Meet your presenter!")


def test_a_page_the_worker_stopped_keeping_the_photos_of_is_read_again_without_paying_twice(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})])
    )
    # A second check, were the page wrongly paid for again.
    fake_model.respond("check_page", READABLE, READABLE)
    first_photo_kept = the_worker_stops(post_save, ProductPhoto, when=lambda photo: True)
    with first_photo_kept, pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    assert Job.objects.get().photos.count() == 1
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I read your mug's page."))

    restart_dead_producers()

    assert times_paid_for("check_page") == 1
    # The photo kept before the worker stopped isn't kept a second time.
    photos = Job.objects.get().photos.all()
    assert [(photo.position, file_store.read(photo.file)) for photo in photos] == [
        (1, MUG_FRONT),
        (2, MUG_SIDE),
    ]
    assert ToolCall.objects.get(tool="read_page").result.endswith("Kept 2 product photos.")
    assert chat(api, session_id)[-1] == ("agent", "I read your mug's page.")
