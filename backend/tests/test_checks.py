from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, Question
from jobs.tasks import make_person

from .conftest import FACTS_OK, PLAN, READABLE, facts_ok

pytestmark = pytest.mark.django_db


def rewrite(line: str) -> dict[str, Any]:
    return {"line": line}


def plan_with(*lines: str) -> dict[str, Any]:
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": [{"line": line} for line in lines]}}


def scene_lines(api: APIClient, job_id: str) -> list[str]:
    return [scene["line"] for scene in api.get(f"/api/jobs/{job_id}/").json()["scenes"]]


class WorkerStopped(BaseException):
    """The worker process dying mid-task: nothing in the job catches it."""


def test_a_step_handed_out_again_after_it_finished_starts_the_next_one(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    # The worker stopped once the person was made, before the checks got going.
    fake_model.respond("fact_check", WorkerStopped())
    with pytest.raises(WorkerStopped):
        start_job(product_page_url)
    job = Job.objects.get()
    assert job.status == "checking_plan"

    # The broker hands the finished step out again.
    fake_model.respond("fact_check", FACTS_OK)
    make_person(str(job.pk))

    assert api.get(f"/api/jobs/{job.pk}/").json()["status"] == "ready_to_render"
    assert ModelCall.objects.filter(purpose__in=["draw_person", "design_voice"]).count() == 2


def test_a_line_asked_about_stays_on_record_when_shortening_drops_its_scene(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Yours for $19.99."))
    fails: dict[str, Any] = {
        "decision": "checked",
        "reason": "Scene 2's price isn't the page's.",
        "question": None,
        "lines": [
            {"scene": 2, "verdict": "wrong", "problem": "Wrong price.", "page_says": "$24.00"}
        ],
    }
    first = {**fails, "lines": [facts_ok(1)["lines"][0], *fails["lines"]]}
    fake_model.respond("fact_check", first, fails, fails)
    fake_model.respond("rewrite_line", rewrite("Only $19.99."), rewrite("Just $19.99."))
    job_id = start_job(product_page_url, target_seconds=2)
    # 3 words and 9: 6 seconds, over the 2-second target.
    own_line = "Yours for just $24.00 today, from Kiln & Co."
    assert answer(job_id, {"answer": "own", "line": own_line}) == 202
    assert api.get(f"/api/jobs/{job_id}/").json()["question"]["kind"] == "length"
    fake_model.respond("shorten_script", {"lines": ["Meet the mug."]})

    assert answer(job_id, {"answer": "shorten"}) == 202

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "ready_to_render"
    assert scene_lines(api, job_id) == ["Meet the mug."]
    asked = [(each.kind, each.scene_id) for each in Question.objects.filter(job_id=job_id)]
    assert asked == [("fact_check", None), ("length", None)]
