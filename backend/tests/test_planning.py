import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pytest_django import DjangoCaptureOnCommitCallbacks
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto, Question, Scene
from jobs.tasks import keep_photo, plan_ad, read_page

from .conftest import PLAN, READABLE, openai_answer

pytestmark = pytest.mark.django_db


def test_the_plan_is_stored_with_the_job_and_its_scenes_show_on_the_page(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(product_page_url, target_seconds=12)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    assert job["scenes"] == [
        {
            "number": 1,
            "line": "Meet the Stoneware Mug from Kiln & Co.",
            "slot_seconds": 4,
            "status": "planned",
        },
        {
            "number": 2,
            "line": "Hand-thrown, holds 350 ml, and dishwasher safe.",
            "slot_seconds": 5,
            "status": "planned",
        },
        {"number": 3, "line": "Yours for $24.00.", "slot_seconds": 3, "status": "planned"},
    ]
    assert [(entry["message"], entry["reason"]) for entry in job["activity"][-2:]] == [
        (
            "Planning the ad",
            "The plan sets the scenes, what the person says in each, and how long each lasts.",
        ),
        ("Planned 3 scenes", PLAN["reason"]),
    ]
    # The producer plans from the page itself, the target length and the photos it has.
    handoff = ModelCall.objects.get(job_id=job_id, purpose="plan_ad").handoff
    assert "$24.00" in handoff["page_text"]
    assert "InStock" in handoff["page_text"]
    assert (handoff["target_seconds"], handoff["photo_count"], handoff["answers"]) == (12, 2, [])


def test_the_producer_is_shown_every_product_photo_by_its_number(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    openai_server(openai_answer(READABLE), openai_answer(PLAN))

    job_id = start_job(product_page_url)

    plan_request = [request for request, _ in httpserver.log if request.path == "/v1/responses"][1]
    [message] = plan_request.get_json()["input"]
    assert message["role"] == "user"
    handoff, *photos = message["content"]
    assert photos == [
        {"type": "input_text", "text": "Photo 1"},
        {
            "type": "input_image",
            # MUG_FRONT, the front photo's bytes exactly as the shop served them.
            "image_url": "data:image/png;base64,iVBORyBmcm9udCBvZiB0aGUgbXVn",
            "detail": "low",
        },
        {"type": "input_text", "text": "Photo 2"},
        {
            "type": "input_image",
            # MUG_SIDE.
            "image_url": "data:image/png;base64,iVBORyBzaWRlIG9mIHRoZSBtdWc=",
            "detail": "low",
        },
    ]
    # The record says which photos were shown by their keys in the file store, not their bytes.
    call = ModelCall.objects.get(job_id=job_id, purpose="plan_ad")
    assert call.images == [
        {"label": "Photo 1", "key": f"jobs/{job_id}/photos/1.png"},
        {"label": "Photo 2", "key": f"jobs/{job_id}/photos/2.png"},
    ]
    assert handoff["type"] == "input_text"
    assert json.loads(handoff["text"]) == call.handoff


def test_the_products_colour_and_the_photos_showing_it_are_stored_with_the_job(
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    # Sage green, shown only in photo 2. Not photo 1, so marking the first photo by default
    # can't pass.
    fake_model.respond("plan_ad", _plan_with(colour_photos=[2]))

    job_id = start_job(product_page_url)

    assert Job.objects.get(pk=job_id).product_colour == "sage green"
    photos = ProductPhoto.objects.filter(job_id=job_id)
    assert [(photo.position, photo.shows_product_colour) for photo in photos] == [
        (1, False),
        (2, True),
    ]


def _plan_with(**changes: Any) -> dict[str, Any]:
    """PLAN with some of its plan's fields replaced."""
    return {**PLAN, "plan": {**PLAN["plan"], **changes}}


def _scene(**changes: Any) -> dict[str, Any]:
    return {"line": "Meet the Stoneware Mug.", "slot_seconds": 4, **changes}


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(_plan_with(scenes=[]), id="no scenes"),
        pytest.param(_plan_with(scenes=[_scene(slot_seconds=0)]), id="a zero-second slot"),
        pytest.param(_plan_with(scenes=[_scene(slot_seconds=4.5)]), id="a fractional slot"),
        pytest.param(_plan_with(scenes=[_scene(slot_seconds="4")]), id="a slot given as text"),
        pytest.param(_plan_with(scenes=[_scene(line="  ")]), id="a scene with nothing to say"),
        pytest.param(_plan_with(product_colour=" "), id="no colour"),
        pytest.param(_plan_with(colour_photos=[]), id="no photo showing the colour"),
        pytest.param(_plan_with(colour_photos=[1, 3]), id="a photo after the last one"),
        pytest.param(_plan_with(colour_photos=[0]), id="a photo before the first one"),
        pytest.param(_plan_with(colour_photos=["1"]), id="a photo number given as text"),
        pytest.param({**PLAN, "plan": None}, id="a plan decision with no plan"),
        pytest.param(
            {**PLAN, "decision": "ask", "question": "Which size?"}, id="a question and a plan"
        ),
    ],
)
def test_a_plan_that_breaks_the_rules_fails_the_job_and_nothing_of_it_is_kept(
    api: APIClient,
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    start_job: Callable[..., str],
    reply: dict[str, Any],
) -> None:
    openai_server(
        openai_answer(READABLE),
        openai_answer(reply),
    )

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert job["activity"][-1]["message"] == "Could not plan the ad"
    assert job["activity"][-1]["reason"].startswith(
        "The model's answer couldn't be used: gpt-5.6-sol gave an answer for plan_ad that "
        "could not be read:"
    )
    assert job["scenes"] == []
    assert not Scene.objects.filter(job_id=job_id).exists()
    assert Job.objects.get(pk=job_id).product_colour == ""
    assert not ProductPhoto.objects.filter(job_id=job_id, shows_product_colour=True).exists()
    # The bad plan is still paid for: 1,200 x $4.00/M in + 300 x $20.00/M out.
    plan_call = ModelCall.objects.get(job_id=job_id, purpose="plan_ad")
    assert (plan_call.outcome, plan_call.cost_usd) == ("failed", Decimal("0.0108"))
    # Nothing is asked of a model after a plan that broke the rules.
    assert [request.path for request, _ in httpserver.log].count("/v1/responses") == 2


def test_a_photo_kept_before_only_readable_formats_were_kept_fails_the_plan_saying_why(
    api: APIClient, fake_model: FakeModel
) -> None:
    # A job whose page was read before photos had to be PNG, JPEG, WebP or GIF: it kept an
    # AVIF. Tasks never run inside this test's transaction, so planning starts by hand.
    started = api.post("/api/jobs/", {"product_url": "https://shop.example/mug"}, format="json")
    job_id = started.json()["id"]
    job = Job.objects.get(pk=job_id)
    keep_photo(job, 1, b"\x00\x00\x00\x1cftypavif", "image/avif", source_url="https://x.test/1")
    Job.objects.filter(pk=job_id).update(status=Job.Status.PAGE_READ)

    plan_ad.delay(job_id)

    job_view = api.get(f"/api/jobs/{job_id}/").json()
    assert job_view["status"] == "failed"
    assert (job_view["activity"][-1]["message"], job_view["activity"][-1]["reason"]) == (
        "Could not plan the ad",
        f"Photo 1 (jobs/{job_id}/photos/1.avif) is image/avif, which models can't read. "
        "Only PNG, JPEG, WebP or GIF photos can be shown to the model.",
    )
    # Refused before the model was asked, so nothing was paid for.
    assert not ModelCall.objects.filter(job_id=job_id).exists()


ASK = {
    "decision": "ask",
    "reason": "The page shows two prices, $24.00 and $19.00, and the ad can only say one.",
    "question": "The page shows $24.00 and $19.00. Which price should the ad say?",
    "plan": None,
}


def test_the_producers_question_waits_for_an_answer_and_the_plan_uses_it(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", ASK, PLAN)
    job_id = start_job(product_page_url)

    waiting = api.get(f"/api/jobs/{job_id}/").json()
    assert waiting["status"] == "needs_answer"
    assert waiting["scenes"] == []
    [asked] = Question.objects.filter(job_id=job_id)
    assert waiting["question"] == {"id": asked.pk, "kind": "producer", "question": ASK["question"]}
    assert (waiting["activity"][-1]["message"], waiting["activity"][-1]["reason"]) == (
        f"Asked: {ASK['question']}",
        ASK["reason"],
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            f"/api/jobs/{job_id}/answer/", {"answer": "$19.00, the sale price."}, format="json"
        )

    assert response.status_code == 202
    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    assert job["question"] is None
    assert len(job["scenes"]) == 3
    # The second plan is made knowing the answer.
    first, second = ModelCall.objects.filter(job_id=job_id, purpose="plan_ad")
    assert first.handoff["answers"] == []
    assert second.handoff["answers"] == [
        {"question": ASK["question"], "answer": "$19.00, the sale price."}
    ]
    [asked] = Question.objects.filter(job_id=job_id)
    assert (asked.kind, asked.question, asked.reason, asked.answer) == (
        "producer",
        ASK["question"],
        ASK["reason"],
        "$19.00, the sale price.",
    )
    assert asked.answered_at is not None


def test_the_same_question_asked_again_is_a_new_question_with_its_own_id(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", ASK, ASK, PLAN)
    job_id = start_job(product_page_url)
    first = api.get(f"/api/jobs/{job_id}/").json()["question"]

    with django_capture_on_commit_callbacks(execute=True):
        api.post(f"/api/jobs/{job_id}/answer/", {"answer": "Not sure."}, format="json")

    second = api.get(f"/api/jobs/{job_id}/").json()["question"]
    assert (first["question"], second["question"]) == (ASK["question"], ASK["question"])
    # The page tells them apart by id, so the second one gets a fresh, empty form.
    stored = [q.pk for q in Question.objects.filter(job_id=job_id)]
    assert [first.get("id"), second.get("id")] == stored
    assert len(set(stored)) == 2


def test_a_restart_while_waiting_for_an_answer_keeps_waiting_without_asking_again(
    api: APIClient, fake_model: FakeModel, product_page_url: str, start_job: Callable[..., str]
) -> None:
    fake_model.respond("check_page", READABLE)
    # Only one plan is scripted: planning again would find no reply and fail the job.
    fake_model.respond("plan_ad", ASK)
    job_id = start_job(product_page_url)
    before = api.get(f"/api/jobs/{job_id}/").json()

    # The queue hands the planning task out again, as it does when a worker restarts
    # before finishing it.
    plan_ad.delay(job_id)

    after = api.get(f"/api/jobs/{job_id}/").json()
    assert after["status"] == "needs_answer"
    [asked] = Question.objects.filter(job_id=job_id)
    assert after["question"] == {"id": asked.pk, "kind": "producer", "question": ASK["question"]}
    assert after["activity"] == before["activity"]
    assert Question.objects.filter(job_id=job_id).count() == 1
    assert ModelCall.objects.filter(job_id=job_id, purpose="plan_ad").count() == 1


@pytest.mark.parametrize("task", [read_page, plan_ad], ids=["read_page", "plan_ad"])
def test_a_task_handed_out_again_after_the_job_is_planned_changes_and_pays_for_nothing(
    task: Any,
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    # One reply each: a task that ran again would find no reply and fail the job.
    fake_model.respond("plan_ad", PLAN)
    job_id = start_job(product_page_url)
    before = api.get(f"/api/jobs/{job_id}/").json()
    requests_to_shop = len(httpserver.log)

    # The queue hands a finished task out again, as it does when a worker stops after
    # doing the work but before telling the queue it's done.
    task.delay(job_id)

    after = api.get(f"/api/jobs/{job_id}/").json()
    assert after == before
    assert after["status"] == "planned"
    assert len(httpserver.log) == requests_to_shop
    assert ModelCall.objects.filter(job_id=job_id).count() == 2
    assert ProductPhoto.objects.filter(job_id=job_id).count() == 2
    assert Scene.objects.filter(job_id=job_id).count() == 3
