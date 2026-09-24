from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.models import Job

from .conftest import FACTS_OK, PLAN, READABLE

pytestmark = pytest.mark.django_db


def test_a_started_job_can_be_fetched_with_its_link_and_target_length(api: APIClient) -> None:
    started = api.post(
        "/api/jobs/",
        {"product_url": "https://shop.example/products/mug", "target_seconds": 15},
        format="json",
    )

    assert started.status_code == 201
    job = api.get(f"/api/jobs/{started.json()['id']}/").json()
    assert job["product_url"] == "https://shop.example/products/mug"
    assert job["target_seconds"] == 15
    assert job["status"] == "queued"


def test_polling_after_an_entry_returns_only_the_entries_since_then(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)
    job_id = start_job(product_page_url)
    everything = api.get(f"/api/jobs/{job_id}/").json()["activity"]

    since_second = api.get(f"/api/jobs/{job_id}/?after=2").json()["activity"]

    assert [entry["seq"] for entry in everything] == list(range(1, len(everything) + 1))
    assert len(everything) > 2
    assert [entry["seq"] for entry in since_second] == list(range(3, len(everything) + 1))
    assert since_second == everything[2:]


@pytest.mark.parametrize(
    "body",
    [
        {"product_url": "not a link"},
        {"product_url": "https://shop.example/products/mug", "target_seconds": 0},
        {"product_url": "https://shop.example/products/mug", "target_seconds": 12.5},
        {"product_url": "https://shop.example/products/mug", "target_seconds": 100_000},
    ],
)
def test_a_job_with_a_bad_link_or_length_is_refused(api: APIClient, body: dict[str, Any]) -> None:
    response = api.post("/api/jobs/", body, format="json")

    assert response.status_code == 400
    assert not Job.objects.exists()
