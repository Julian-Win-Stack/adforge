from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from rest_framework.test import APIClient

from jobs.models import Question

BEFORE = [("jobs", "0004_plan_scenes_and_questions")]
AFTER = [("jobs", "0005_questions_for_jobs_already_waiting")]


def migrate(targets: list[tuple[str, str]]) -> Any:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_a_job_already_waiting_gets_the_question_it_waits_on(api: APIClient) -> None:
    old = migrate(BEFORE)
    Job = old.get_model("jobs", "Job")
    link_job = Job.objects.create(
        product_url="https://shop.example/gone", status="needs_working_link"
    )
    link_job.activity.create(seq=1, message="Reading the page", reason="The job has started.")
    link_job.activity.create(
        seq=2, message="Waiting for a working link", reason="The page was a 404."
    )
    photos_job = Job.objects.create(
        product_url="https://shop.example/mug", status="needs_product_photos"
    )

    migrate(AFTER)

    assert api.get(f"/api/jobs/{link_job.pk}/").json()["question"] == {
        "id": Question.objects.get(job_id=link_job.pk).pk,
        "kind": "working_link",
        "question": "We couldn't read one product's page from that link. "
        "What's the link to the product's own page?",
    }
    assert api.get(f"/api/jobs/{photos_job.pk}/").json()["question"] == {
        "id": Question.objects.get(job_id=photos_job.pk).pk,
        "kind": "product_photos",
        "question": "The page had no product photo we could use. "
        "Can you upload at least one photo of the product?",
    }
    # Why the job asked comes from the last thing it recorded, where there is one.
    assert Question.objects.get(job_id=link_job.pk).reason == "The page was a 404."
    assert Question.objects.get(job_id=photos_job.pk).reason == ""
