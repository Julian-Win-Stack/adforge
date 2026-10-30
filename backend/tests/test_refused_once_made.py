"""Spec round-7-other-reds section 3: when the safety filter refuses what a model has just
made, a picture it drew or a turn it wrote, it is made again, up to 2 more times, each try
recorded and paid for; only when every try is refused does the step fail, as a safety
refusal. A refusal of what was sent is not made again. Through the chat, with the producer's
turns on our real OpenAI code where the turn is under test, and the picture model faked."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.models import SceneStep

from .conftest import HeldSteps, chat, openai_reply, openai_turn, recorded
from .test_producer_scenes import CHOICE, making

pytestmark = pytest.mark.django_db(transaction=True)

# What OpenAI's picture model answered in round 7 when its filter refused the picture it had
# drawn (docs/real-api-replies.md), as the adapter raises it.
REFUSED_ONCE_DRAWN = BlockedBySafetyFilter(
    "Error code: 400 - {'error': {'message': 'Your request was rejected by the safety system. "
    "safety_violations=[sexual].', 'type': 'image_generation_user_error', 'param': None, "
    "'code': 'moderation_blocked', 'moderation_details': {'moderation_stage': 'output', "
    "'categories': ['sexual']}}}",
    of_what_was_made=True,
)
REFUSED_AS_SENT = BlockedBySafetyFilter("content_policy_violation")


def filtered_turn() -> dict[str, Any]:
    """A turn the safety filter stopped before the end, as Azure's Responses API sent it in
    round 7: incomplete, for the content filter, billed for what was written."""
    reply = openai_reply({"type": "output_text", "text": "Here is", "annotations": []})
    return {**reply, "status": "incomplete", "incomplete_details": {"reason": "content_filter"}}


def picture_calls() -> list[str]:
    return list(
        ModelCall.objects.filter(purpose="make_starting_picture")
        .order_by("id")
        .values_list("outcome", flat=True)
    )


def test_a_producer_turn_the_filter_stops_is_taken_again_at_once_and_the_ad_goes_on(
    api: APIClient,
    openai_server: Callable[..., None],
    session_id: str,
    say: Callable[..., None],
) -> None:
    openai_server(filtered_turn(), openai_turn("Hi! Send me your product's page."))

    say("Hello")

    assert chat(api, session_id) == [
        ("user", "Hello"),
        ("agent", "Hi! Send me your product's page."),
    ]
    turns = ModelCall.objects.filter(purpose="produce").order_by("id")
    assert [(each.attempt, each.outcome) for each in turns] == [(1, "failed"), (2, "succeeded")]
    # The stopped turn was billed, so it is on the record at its cost: gpt-6.1-sol, 1,200 x
    # $2.00/M in + 300 x $10.00/M out.
    assert [each.cost_usd for each in turns] == [Decimal("0.0054"), Decimal("0.0054")]
    assert "content_filter" in turns[0].error


def test_a_starting_picture_the_filter_refuses_once_drawn_is_drawn_again_and_shown(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", REFUSED_ONCE_DRAWN, REFUSED_ONCE_DRAWN)
    fake_model.respond("produce", turn(says="Scene 1's picture is ready."))

    steps.run_held()

    assert SceneStep.objects.get().status == "finished"
    assert picture_calls() == ["failed", "failed", "succeeded"]
    assert not [text for _, text in recorded(api, session_id) if "safety filter" in text]


def test_a_starting_picture_refused_every_time_it_is_drawn_fails_as_a_safety_refusal(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", *[REFUSED_ONCE_DRAWN] * 3)
    fake_model.respond("produce", turn(says="The picture was blocked."))

    steps.run_held()

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert picture_calls() == ["failed", "failed", "failed"]
    # The producer is told a new try won't do, not that the same request is refused again.
    assert "refused what the model made, every time it was made" in step.reason
    assert recorded(api, session_id)[-1] == (
        "problem",
        "Scene 1's starting picture couldn't be made: the AI service's safety filter blocked it.",
    )


def test_a_starting_picture_refused_for_what_it_was_sent_isnt_drawn_again(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", REFUSED_AS_SENT)
    fake_model.respond("produce", turn(says="The picture was blocked."))

    steps.run_held()

    assert SceneStep.objects.get().status == "failed"
    assert picture_calls() == ["failed"]
