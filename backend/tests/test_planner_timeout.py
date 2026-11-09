"""The planner's one question expires through the real chat, with a faked clock."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from pydantic import ValidationError
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents import tasks
from agents.models import PlannerQuestion
from chat.models import Session
from gateway.fake import FakeModel, turn
from jobs.models import Job
from jobs.planning import QuestionItem

from .conftest import PLAN, PRODUCT_PAGE, READABLE, chat, recorded

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
    plan["scenes"][-1]["line"] = (
        "Get your Stoneware Mug from Kiln & Co today for your favourite tea."
    )
    plan["scenes"][-1]["overlay"] = None
    plan["end_card_price"] = ""
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
    fake_model.respond(
        "plan_ad",
        {**PLAN, "question_resolutions": [{"item": 2, "answer_quote": "Use the 350 ml pack"}]},
    )
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


def test_a_page_conflict_question_keeps_waiting_past_ten_minutes(
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
        turn(calls=[("ask_owner", {"question": "Is the mug 350 ml or 400 ml?"})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The page gives two sizes for the same mug.",
            "question": "Is the mug 350 ml or 400 ml?",
            "question_items": [
                {"kind": "photos", "question": "Are these the right product photos?"},
                {"kind": "page_conflict", "question": "Is the mug 350 ml or 400 ml?"},
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


def test_a_stock_question_saved_before_stock_was_dropped_keeps_waiting(
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
        turn(calls=[("ask_owner", {"question": "Is the mug 350 ml or 400 ml?"})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The page gives two sizes for the same mug.",
            "question": "Is the mug 350 ml or 400 ml?",
            "question_items": [
                {"kind": "photos", "question": "Are these the right product photos?"},
                {"kind": "page_conflict", "question": "Is the mug 350 ml or 400 ml?"},
            ],
            "plan": None,
        },
    )
    say(product_page_url)
    # A round asked while the planner could still ask about stock.
    asked = PlannerQuestion.objects.get()
    asked.items = [
        {**item, "kind": "stock"} if item["kind"] == "page_conflict" else item
        for item in asked.items
    ]
    asked.save(update_fields=["items"])
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
    # No scene films the unanswered demo of tea poured into the mug: the B-roll scene only
    # shows the mug at its best.
    assert not any(
        scene.broll_kind == "does a job" or "pour" in scene.shows.casefold()
        for scene in job.scenes.all()
    )
    assert any("350 ml" in scene.line for scene in job.scenes.all())
    assert all("$" not in scene.line + scene.overlay for scene in job.scenes.all())
    plan_requests = [request for request in fake_model.asked if request.purpose == "plan_ad"]
    defaults = plan_requests[-1].handoff.model_dump()["planning_defaults"]
    assert defaults and "camera" in defaults and "price" in defaults
    summary = [text for _, text in chat(api, session_id) if "without your answer" in text]
    assert len(summary) == 1 and "camera" in summary[0]


@pytest.mark.parametrize("price_in", ["end card", "line", "overlay"])
def test_a_plan_stating_a_price_while_the_size_question_timed_out_is_kept_and_noticed(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    price_in: str,
) -> None:
    # The planner is told "say no price" for an unanswered size or price question. It never
    # sent a plan back in round 12, so a plan that still states one goes ahead with a
    # developer notice (Julian, Nov 9 2026, rules review).
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    priced = a_plan_without_price()
    if price_in == "end card":
        priced["plan"]["end_card_price"] = "$24.00"
    else:
        priced["plan"]["scenes"][-1][price_in] = "Only $24.00, get yours today."
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", priced)

    tasks.expire_planner_questions()

    assert Job.objects.get().scenes.exists()
    # The original ask, then the plan, kept.
    assert sum(request.purpose == "plan_ad" for request in fake_model.asked) == 2
    assert any(
        "A size or price question is still unanswered, but a line, an overlay or the end card "
        "states a price." in text
        for _, text in recorded(api, session_id)
    )


def test_a_late_note_can_override_the_defaults_before_the_plan_is_saved(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    fake_model.respond("produce", turn(says="I'll carry on with the stated defaults."))
    tasks.expire_planner_questions()
    assert not Job.objects.get().scenes.exists()

    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    owner_note = "Use the 350 ml pack, and include its $24.00 price after all."
    fake_model.respond(
        "plan_ad", {**PLAN, "question_resolutions": [{"item": 2, "answer_quote": owner_note}]}
    )
    say(owner_note)

    assert any("$24.00" in scene.line for scene in Job.objects.get().scenes.all())


def test_a_size_timeout_picks_the_main_option_from_a_page_with_multiple_packs(
    fake_model: FakeModel,
    product_page_url: str,
    httpserver: HTTPServer,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PRODUCT_PAGE.format(side=httpserver.url_for("/cdn/mug-side.png")).replace(
        "<h1>Stoneware Mug</h1>",
        "<h1>Stoneware Mug</h1>"
        "<p>Main option selected: 350 ml Starter Pack, $24.00.</p>"
        "<p>Other option: 500 ml Family Pack, $30.00.</p>",
    )
    httpserver.expect_request("/products/mug-packs").respond_with_data(
        page, content_type="text/html; charset=utf-8"
    )
    ask_about_pack(fake_model, httpserver.url_for("/products/mug-packs"), say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    picked_main = a_plan_without_price()
    picked_main["plan"]["scenes"][0]["line"] = (
        "Meet the 350 ml Starter Pack of the Stoneware Mug from Kiln & Co."
    )
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", picked_main)

    tasks.expire_planner_questions()

    request = [request for request in fake_model.asked if request.purpose == "plan_ad"][-1]
    handoff = request.handoff.model_dump()
    assert "Main option selected: 350 ml Starter Pack" in handoff["page_text"]
    assert "500 ml Family Pack" in handoff["page_text"]
    assert handoff["planning_defaults"]
    script = " ".join(scene.line + " " + scene.overlay for scene in Job.objects.get().scenes.all())
    assert "350 ml Starter Pack" in script
    assert "500 ml" not in script and "Family Pack" not in script and "$" not in script


def test_an_overdue_question_waits_until_the_other_active_session_finishes(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    other = api.post("/api/sessions/", {}, format="json").json()["id"]
    # As in test_chat_api's busy-session cases: its background producer is still working.
    Session.objects.filter(pk=other).update(producer_running=True)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", a_plan_without_price())

    tasks.expire_planner_questions()

    assert not Job.objects.get().scenes.exists()
    assert not any("without your answer" in text for _, text in chat(api, session_id))

    Session.objects.filter(pk=other).update(producer_running=False)
    tasks.expire_planner_questions()

    assert Job.objects.get().scenes.exists()
    assert sum("without your answer" in text for _, text in chat(api, session_id)) == 1


@pytest.mark.parametrize("after_timeout", [False, True], ids=["partial answer", "late note"])
@pytest.mark.parametrize(
    ("owner_note", "resolutions"),
    [
        ("Thanks!", []),
        (
            "1: Use those photos",
            [{"item": 1, "answer_quote": "1: Use those photos"}],
        ),
    ],
    ids=["acknowledgement", "photos only"],
)
def test_an_unrelated_reply_leaves_the_size_question_unanswered_and_a_price_noticed(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    after_timeout: bool,
    owner_note: str,
    resolutions: list[dict[str, Any]],
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(
        timezone, "now", lambda: asked_at + timedelta(minutes=10 if after_timeout else 9)
    )
    if after_timeout:
        fake_model.respond("produce", turn(says="I'll carry on with the stated defaults."))
        tasks.expire_planner_questions()
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", {**PLAN, "question_resolutions": resolutions})

    say(owner_note)

    assert Job.objects.get().scenes.exists()
    assert any(
        "A size or price question is still unanswered" in text
        for _, text in recorded(api, session_id)
    )


@pytest.mark.parametrize("quote_source", ["invented", "initial message", "producer"])
def test_a_price_override_needs_owner_words_from_after_the_shown_question(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    quote_source: str,
) -> None:
    ask_about_pack(fake_model, product_page_url, say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    producer_said = "I'll carry on with the stated defaults."
    fake_model.respond("produce", turn(says=producer_said))
    tasks.expire_planner_questions()
    unsupported_quote = {
        "invented": "Use the large pack and include the price.",
        "initial message": product_page_url,
        "producer": producer_said,
    }[quote_source]
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond(
        "plan_ad",
        {**PLAN, "question_resolutions": [{"item": 2, "answer_quote": unsupported_quote}]},
        a_plan_without_price(),
    )

    say("Thanks!")

    assert Job.objects.get().scenes.exists()
    assert all("$" not in scene.line + scene.overlay for scene in Job.objects.get().scenes.all())
    assert sum(request.purpose == "plan_ad" for request in fake_model.asked) == 3


def test_a_no_price_default_still_allows_a_weight_stated_in_pounds(
    fake_model: FakeModel,
    product_page_url: str,
    httpserver: HTTPServer,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PRODUCT_PAGE.format(side=httpserver.url_for("/cdn/mug-side.png")).replace(
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "Hand-thrown, holds 350 ml, dishwasher safe. Weighs five pounds.",
    )
    httpserver.expect_request("/products/heavy-mug").respond_with_data(
        page, content_type="text/html; charset=utf-8"
    )
    ask_about_pack(fake_model, httpserver.url_for("/products/heavy-mug"), say)
    asked_at = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: asked_at + timedelta(minutes=10))
    weighted_plan = a_plan_without_price()
    weighted_plan["plan"]["scenes"][1]["line"] = (
        "Hand-thrown, holds 350 ml, and weighs five pounds for everyday drinks."
    )
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="The plan is ready."))
    fake_model.respond("plan_ad", weighted_plan)

    tasks.expire_planner_questions()

    assert Job.objects.get().scenes.count() == 3
    assert any("five pounds" in scene.line for scene in Job.objects.get().scenes.all())


def test_the_planner_has_no_stock_question_to_ask() -> None:
    # Stock is never in a line or an overlay, so it is never a reason to ask the owner.
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({"kind": "stock", "question": "Is it in stock?"})
