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
from django.db.models.signals import ModelSignal, post_save, pre_save
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, ProductPhoto

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    NO_CHOICES,
    PLAN,
    READABLE,
    a_producer_last_beat,
    chat,
    facts_ok,
    given_to_the_producer,
    handoffs,
    lines,
    plan_with,
    producer_turns,
    results_of,
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
    assert list(Job.objects.get().produced.values_list("kind", "version")) == [
        ("portrait", 1),
        ("voice", 1),
    ]
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


@pytest.mark.parametrize(
    ("purpose", "kind", "field", "about_to_keep_it"),
    [
        pytest.param(
            "draw_person",
            "portrait",
            "file",
            lambda item: item.kind == "portrait",
            id="the portrait",
        ),
        pytest.param(
            "design_voice",
            "voice",
            "voice_id",
            lambda item: item.kind == "voice",
            id="the voice",
        ),
        pytest.param(
            "measure_voice",
            "voice",
            "file",
            # The voice is kept once when designed, then again with the speech it read.
            lambda item: item.kind == "voice" and bool(item.file),
            id="the voice reading the script",
        ),
    ],
)
def test_a_part_of_the_person_paid_for_but_not_kept_when_the_worker_stopped_is_reused(
    fake_model: FakeModel,
    planned: None,
    session_id: str,
    say: Callable[..., None],
    purpose: str,
    kind: str,
    field: str,
    about_to_keep_it: Callable[[ProducedItem], bool],
) -> None:
    fake_model.respond("produce", turn(calls=[("create_person", {})]))
    # The worker stops once the call is paid for and recorded, just before what it made is kept.
    not_kept = the_worker_stops(pre_save, ProducedItem, when=about_to_keep_it)
    with not_kept, pytest.raises(WorkerStopped):
        say("Make the person")
    (paid_for,) = ModelCall.objects.filter(purpose=purpose).values_list("output", flat=True)
    assert paid_for is not None
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Meet your presenter!"))

    restart_dead_producers()

    assert [times_paid_for(each) for each in ("draw_person", "design_voice", "measure_voice")] == [
        1,
        1,
        1,
    ]
    # What was paid for is kept, rather than made again.
    item = Job.objects.get().produced.get(kind=kind)
    assert getattr(item, field) == paid_for[field]
    assert ToolCall.objects.get(tool="create_person").result.endswith(
        "The voice speaks 2.0 words a second, measured on the script."
    )


def test_a_voice_being_measured_when_the_worker_stopped_is_measured_without_designing_another(
    fake_model: FakeModel, planned: None, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(calls=[("create_person", {})]))
    fake_model.respond("measure_voice", WorkerStopped())
    with pytest.raises(WorkerStopped):
        say("Make the person")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Meet your presenter!"))

    restart_dead_producers()

    assert times_paid_for("design_voice") == 1
    voice = Job.objects.get().produced.get(kind="voice")
    assert voice.voice_id == "fake-voice-1"
    assert voice.words_per_second == pytest.approx(2.0)
    assert fake_model.spoken == [" ".join(scene["line"] for scene in PLAN["plan"]["scenes"])]
    assert ToolCall.objects.get(tool="create_person").result.endswith(
        "The voice speaks 2.0 words a second, measured on the script."
    )


def test_a_line_being_rewritten_when_the_worker_stopped_is_rewritten_without_checking_it_again(
    fake_model: FakeModel, page_read: str, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
    )
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "$19.99."))
    fake_model.respond(
        "fact_check",
        {
            "decision": "checked",
            "reason": "The price in scene 2 isn't the page's.",
            "question": None,
            "lines": [
                *facts_ok(1)["lines"],
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "problem": "The line says $19.99.",
                    "page_says": "$24.00",
                },
            ],
        },
    )
    fake_model.respond("rewrite_line", WorkerStopped())
    with pytest.raises(WorkerStopped):
        say("Plan it and check it")
    the_producer_died(session_id)
    fake_model.respond("rewrite_line", {"line": "Yours for $24.00."})
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("produce", turn(says="Every line checks out."))

    restart_dead_producers()

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert lines() == ["Meet the mug.", "Yours for $24.00."]
    # The old line isn't checked again: after the restart only its rewrite is.
    assert [handed["lines"] for handed in handoffs("fact_check")] == [
        [{"scene": 1, "line": "Meet the mug."}, {"scene": 2, "line": "$19.99."}],
        [{"scene": 2, "line": "Yours for $24.00."}],
    ]
    (sent,) = ModelCall.objects.filter(
        purpose="rewrite_line", outcome=ModelCall.Outcome.SUCCEEDED
    ).values_list("handoff", flat=True)
    assert sent["problems"] == [{"problem": "The line says $19.99.", "page_says": "$24.00"}]
