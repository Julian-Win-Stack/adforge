"""The planner owns the one pre-plan question, including worries found by page reading."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import PLAN, READABLE, chat, handoffs, results_of

pytestmark = [pytest.mark.django_db(transaction=True)]

PHOTO_QUESTION = (
    "1. I found only one product photo. Attach more, or use this one? "
    "If you don't answer: I'll plan with the photos we have."
)
PHOTO_ASK: dict[str, Any] = {
    "decision": "ask",
    "question": PHOTO_QUESTION,
    "reason": "Only one photo was found on the page.",
    "plan": None,
    "question_items": [
        {
            "kind": "photos",
            "question": "I found only one product photo. Attach more, or use this one?",
        }
    ],
}


def test_photo_doubt_joins_the_planners_single_question_in_the_chat(
    api: APIClient,
    fake_model: FakeModel,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": PHOTO_QUESTION, "photos": []})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN, PHOTO_ASK)

    say(shampoo_page_url)

    assert not Job.objects.get().scenes.exists()
    assert len(handoffs("plan_ad")) == 2
    assert "photos are in doubt" in handoffs("plan_ad")[0]["photo_worry"]
    assert "Stopped before planning" not in results_of("read_page")[0]
    assert [text for role, text in chat(api, session_id) if role == "agent"] == [PHOTO_QUESTION]

    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)
    say("Use this one")

    assert Job.objects.get().scenes.count() == 3
    assert sum(text == PHOTO_QUESTION for _, text in chat(api, session_id)) == 1
