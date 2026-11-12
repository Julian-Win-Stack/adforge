"""An exhausted safety block on required work stops spending and gives one kind reply.

Spec #628 / ticket #631: through the chat, with outside services faked at the gateway.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job

from .conftest import READABLE, HeldSteps, chat, recorded

pytestmark = [pytest.mark.django_db(transaction=True)]

KIND_MESSAGE = (
    "The AI service's safety filter wrongly flagged this product, so I can't make this ad. "
    "It isn't your fault. Please try another product."
)


def test_a_blocked_plan_stops_before_another_tool_or_turn_and_a_message_wakes_it(
    fake_model: FakeModel,
    page_read: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    sentry_events: list[dict[str, Any]],
    settings: Settings,
) -> None:
    settings.PRODUCER_FILTER_RETRY_SECONDS = [0, 0]
    fake_model.respond("produce", turn(calls=[("plan_ad", {}), ("cost_so_far", {})]))

    def blocked(_: object) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.answer_unscripted("plan_ad", blocked)
    calls_before = ModelCall.objects.count()
    say("Plan it")

    assert chat(api, session_id)[-1] == ("notice", KIND_MESSAGE)
    # One producer turn, then planning with and without its library sheet. No retry of
    # the producer and no cost tool from the same turn once the required step failed.
    assert ModelCall.objects.count() == calls_before + 3
    session = Session.objects.get(pk=session_id)
    cost = session.tool_calls.get(tool="cost_so_far")
    assert cost.answer == "cant"
    assert cost.result.startswith("Refused:")
    assert cost.finished_at is not None
    assert not session.tool_calls.filter(finished_at__isnull=True).exists()
    assert not session.producer_running
    assert not session.system_notes.filter(kind="nudge").exists()
    assert len([text for role, text in chat(api, session_id) if text == KIND_MESSAGE]) == 1
    recorded_stop = [text for _, text in recorded(api, session_id) if "plan_ad" in text]
    assert any("content_policy_violation" in text for text in recorded_stop)
    assert "ad_blocked_by_filter" in {
        warning["kind"] for warning in api.get(f"/api/sessions/{session_id}/warnings/").json()
    }
    assert any(
        event["tags"]["kind"] == "producer_gave_up"
        and "plan_ad" in event["extra"]["text"]
        and "content_policy_violation" in event["extra"]["text"]
        for event in sentry_events
    )

    fake_model.respond("produce", turn("Send the link for the next product."))
    say("Let's try another product")
    assert chat(api, session_id)[-1] == ("agent", "Send the link for the next product.")


def test_a_blocked_fact_check_stops_without_passing_or_taking_another_turn(
    fake_model: FakeModel,
    planned: None,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    def blocked(_: object) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.answer_unscripted("fact_check", blocked)
    fake_model.respond("produce", turn(calls=[("run_planning_checks", {})]))
    turns_before = ModelCall.objects.filter(purpose="produce").count()
    say("Check the plan")

    assert chat(api, session_id)[-1] == ("notice", KIND_MESSAGE)
    assert ModelCall.objects.filter(purpose="produce").count() == turns_before + 1
    assert not Job.objects.get(session_id=session_id).scenes.filter(fact_checked=True).exists()
    assert any(
        "run_planning_checks" in text and "content_policy_violation" in text
        for _, text in recorded(api, session_id)
    )


@pytest.mark.parametrize("purpose", ["check_page", "copy_page_text"])
def test_a_blocked_page_read_stops_and_names_the_failed_step(
    purpose: str,
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    if purpose != "check_page":
        fake_model.respond("check_page", READABLE)

    def blocked(_: object) -> dict[str, Any]:
        raise BlockedBySafetyFilter("sexual: medium")

    fake_model.answer_unscripted(purpose, blocked)
    fake_model.respond(
        "produce", turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})])
    )
    say(product_page_url)

    assert chat(api, session_id) == [("user", product_page_url), ("notice", KIND_MESSAGE)]
    assert ModelCall.objects.filter(purpose="produce").count() == 1
    assert any(
        "read_page" in text and "sexual: medium" in text for _, text in recorded(api, session_id)
    )


def test_a_producer_turn_exhausts_its_existing_retries_then_uses_the_same_kind_message(
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    settings: Settings,
    sentry_events: list[dict[str, Any]],
) -> None:
    settings.PRODUCER_FILTER_RETRY_SECONDS = [0, 0]
    fake_model.respond("produce", *[BlockedBySafetyFilter("content_filter")] * 3)
    say("Make an ad")

    assert chat(api, session_id) == [("user", "Make an ad"), ("notice", KIND_MESSAGE)]
    assert ModelCall.objects.filter(purpose="produce").count() == 3
    assert any(
        event["tags"]["kind"] == "producer_gave_up" and "content_filter" in event["extra"]["text"]
        for event in sentry_events
    )

    fake_model.respond("produce", turn("What's the link for the other product?"))
    say("Try another product")
    assert chat(api, session_id)[-1] == ("agent", "What's the link for the other product?")


def test_an_optional_picture_block_is_still_given_to_the_producer_to_decide(
    fake_model: FakeModel,
    page_read: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("draw_person", BlockedBySafetyFilter("content_policy_violation"))
    fake_model.respond(
        "produce",
        turn(calls=[("make_picture", {"prompt": "A mug", "references": [], "label": "A mug"})]),
        turn("I'll carry on with the product photos."),
    )
    say("Make a picture")

    assert chat(api, session_id)[-1] == ("agent", "I'll carry on with the product photos.")
    assert not [text for role, text in chat(api, session_id) if text == KIND_MESSAGE]
    assert any(
        "make_picture" in text and "content_policy_violation" in text
        for _, text in recorded(api, session_id)
    )


def test_a_scene_finishing_after_the_filter_stop_cannot_wake_the_producer(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]},
                )
            ]
        ),
        turn("On it."),
    )
    say("Go on")
    assert len(steps.held) == 1

    def blocked(_: object) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_policy_violation")

    fake_model.answer_unscripted("fact_check", blocked)
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "edit_scene",
                    {
                        "scene": 1,
                        "line": "Meet the Clay Co mug: hand-thrown, a lovely everyday mug "
                        "for your home.",
                    },
                ),
                ("run_planning_checks", {}),
            ]
        ),
    )
    say("Change the first line and check it")
    assert chat(api, session_id)[-1] == ("notice", KIND_MESSAGE)
    turns_before = ModelCall.objects.filter(purpose="produce").count()
    # Work already in flight can finish, but its result must not restart the stopped
    # producer and spend anything on another turn.
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    steps.run_held()
    assert ModelCall.objects.filter(purpose="produce").count() == turns_before

    fake_model.respond("produce", turn("Send the next product's link."))
    say("Try another product")
    assert chat(api, session_id)[-1] == ("agent", "Send the next product's link.")
