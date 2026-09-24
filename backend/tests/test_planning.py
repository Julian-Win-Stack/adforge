from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import DjangoCaptureOnCommitCallbacks
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import ProducedItem, ProductPhoto, Question, Scene
from jobs.tasks import check_plan, make_person, plan_ad, read_page

from .conftest import FACTS_OK, PLAN, READABLE

pytestmark = pytest.mark.django_db


ASK = {
    "decision": "ask",
    "reason": "The page shows two prices, $24.00 and $19.00, and the ad can only say one.",
    "question": "The page shows $24.00 and $19.00. Which price should the ad say?",
    "plan": None,
}


def test_the_same_question_asked_again_is_a_new_question_with_its_own_id(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", ASK, ASK, PLAN)
    fake_model.respond("fact_check", FACTS_OK)
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
    assert after["question"] == {
        "id": asked.pk,
        "kind": "producer",
        "question": ASK["question"],
        "options": [],
    }
    assert after["activity"] == before["activity"]
    assert Question.objects.filter(job_id=job_id).count() == 1
    assert ModelCall.objects.filter(job_id=job_id, purpose="plan_ad").count() == 1


@pytest.mark.parametrize(
    "task",
    [read_page, plan_ad, make_person, check_plan],
    ids=["read_page", "plan_ad", "make_person", "check_plan"],
)
def test_a_task_handed_out_again_after_the_job_is_checked_changes_and_pays_for_nothing(
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
    fake_model.respond("fact_check", FACTS_OK)
    job_id = start_job(product_page_url)
    before = api.get(f"/api/jobs/{job_id}/").json()
    requests_to_shop = len(httpserver.log)

    # The queue hands a finished task out again, as it does when a worker stops after
    # doing the work but before telling the queue it's done.
    task.delay(job_id)

    after = api.get(f"/api/jobs/{job_id}/").json()
    assert after == before
    assert after["status"] == "ready_to_render"
    assert len(httpserver.log) == requests_to_shop
    assert ModelCall.objects.filter(job_id=job_id).count() == 6
    assert ProductPhoto.objects.filter(job_id=job_id).count() == 2
    assert Scene.objects.filter(job_id=job_id).count() == 3
    assert ProducedItem.objects.filter(job_id=job_id).count() == 2
