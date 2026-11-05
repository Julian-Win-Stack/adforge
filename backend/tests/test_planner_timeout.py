"""The planner's one question expires through the real chat, with a faked clock."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

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
            "question_items": [
                {"kind": "photos", "question": "Are these the right product photos?"},
                {"kind": "size", "question": "Which pack should I show?"},
            ],
            "plan": None,
        },
    )
    say(link)


def a_plan_without_price() -> dict[str, Any]:
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


def test_a_message_at_nine_minutes_cancels_the_timeout(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=9))
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", PLAN)
    say("Use the 350 ml pack")

    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=11))
    tasks.expire_planner_questions()

    assert Job.objects.get().scenes.exists()
    assert not any("without your answer" in text for _, text in chat(api, session_id))
    assert not any("10 minutes" in text for _, text in recorded(api, session_id))


def test_a_worker_restart_keeps_the_deadline_and_does_not_repeat_the_timeout(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=9))
    tasks.carry_on_after_the_worker_starts()
    assert not Job.objects.get().scenes.exists()
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", a_plan_without_price())
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))

    tasks.carry_on_after_the_worker_starts()
    tasks.carry_on_after_the_worker_starts()

    assert Job.objects.get().scenes.exists()
    assert sum("without your answer" in text for _, text in chat(api, session_id)) == 1
    assert sum("10 minutes" in text for _, text in recorded(api, session_id)) == 1


def test_a_stock_question_keeps_waiting_past_ten_minutes(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": "Is the mug in stock?"})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The page contradicts itself about stock.",
            "question": "Is the mug in stock?",
            "question_items": [
                {"kind": "photos", "question": "Are these the right product photos?"},
                {"kind": "stock", "question": "Is the mug in stock?"},
            ],
            "plan": None,
        },
    )
    say(product_page_url)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(hours=1))

    tasks.expire_planner_questions()
    tasks.carry_on_after_the_worker_starts()

    assert not Job.objects.get().scenes.exists()
    assert not any("without your answer" in text for _, text in chat(api, session_id))
    assert not any("10 minutes" in text for _, text in recorded(api, session_id))


def test_an_unanswered_demo_is_said_to_camera_and_uses_the_stated_defaults(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("ask_owner", {"question": "Send a photo or approve my demo?"})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The pack is ambiguous and there is no photo of this use.",
            "question": "Send a photo or approve my demo?",
            "question_items": [
                {"kind": "photos", "question": "Are these the right product photos?"},
                {"kind": "size", "question": "Which pack?"},
                {"kind": "in_use_photo", "question": "Can you send a photo of it in use?"},
                {"kind": "demo", "question": "May I show tea poured into the mug?"},
            ],
            "plan": None,
        },
    )
    say(product_page_url)
    questions = [text for role, text in chat(api, session_id) if role == "agent"]
    assert len(questions) == 1
    assert "2. Which pack?" in questions[0] and "4. May I show" in questions[0]
    assert "price" in questions[0] and "camera" in questions[0]
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", a_plan_without_price())

    tasks.expire_planner_questions()

    job = Job.objects.get()
    assert all(scene.kind == "talking" for scene in job.scenes.all())
    assert any("350 ml" in scene.line for scene in job.scenes.all())
    assert all("$" not in scene.line + scene.overlay for scene in job.scenes.all())
    plan_requests = [request for request in fake_model.asked if request.purpose == "plan_ad"]
    defaults = plan_requests[-1].handoff.model_dump()["planning_defaults"]
    assert defaults and "camera" in defaults and "price" in defaults
    summary = [text for _, text in chat(api, session_id) if "without your answer" in text]
    assert len(summary) == 1 and "camera" in summary[0]


@pytest.mark.parametrize("price_in", ["line", "overlay"])
def test_a_timed_out_size_choice_retries_a_plan_that_still_states_a_price(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    price_in: str,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    bad = a_plan_without_price()
    bad["plan"]["scenes"][-1][price_in] = "Only $24.00, get yours today."
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", bad, a_plan_without_price())

    tasks.expire_planner_questions()

    assert all("$" not in scene.line + scene.overlay for scene in Job.objects.get().scenes.all())
    # The original ask, the refused price, then the corrected plan.
    assert sum(request.purpose == "plan_ad" for request in fake_model.asked) == 3
