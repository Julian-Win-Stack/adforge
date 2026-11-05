"""The planner's one question expires through the real chat, with a faked clock."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from agents import tasks
from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import PLAN, READABLE, chat, recorded

pytestmark = pytest.mark.django_db(transaction=True)


QUESTION = (
    "1. Which pack should I show? If you don't answer, I'll pick the main pack on the page "
    "and leave the price out."
)


def ask_about_pack(fake_model: FakeModel, link: str, say: Callable[..., None]) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": QUESTION})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The page offers several packs.",
            "question": QUESTION,
            "question_items": [{"kind": "size", "question": "Which pack should I show?"}],
            "plan": None,
        },
    )
    say(link)


def a_plan_without_price() -> dict[str, object]:
    plan = {**PLAN["plan"], "scenes": [dict(scene) for scene in PLAN["plan"]["scenes"]]}
    plan["scenes"][-1]["line"] = "Get your Stoneware Mug today from our shop."
    plan["scenes"][-1]["overlay"] = None
    return {**PLAN, "plan": plan}


def test_no_answer_wakes_the_producer_once_and_names_the_defaults(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    assert not Job.objects.get().scenes.exists()
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", a_plan_without_price())

    tasks.expire_planner_questions()
    tasks.expire_planner_questions()

    assert Job.objects.get().scenes.exists()
    summaries = [text for _, text in chat(api, session_id) if "without your answer" in text]
    assert len(summaries) == 1
    assert "main" in summaries[0] and "price" in summaries[0] and "note" in summaries[0]
    assert sum("10 minutes" in text for _, text in recorded(api, session_id)) == 1
