from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto

from .conftest import MUG_FRONT, MUG_SIDE

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


READABLE = {"decision": "readable", "reason": "The page names the mug, its price and its size."}


def test_reading_the_page_stores_its_text_and_explains_every_step(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "page_read"
    assert all(entry["message"] and entry["reason"] for entry in job["activity"])
    assert job["activity"][-1]["reason"] == READABLE["reason"]
    page_text = Job.objects.get(pk=job_id).page_text
    assert "Stoneware Mug" in page_text
    assert "$24.00" in page_text
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in page_text
    assert "tracking code" not in page_text


def test_the_product_photos_the_page_declares_are_stored_with_the_job(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)

    job_id = start_job(product_page_url)

    photos = api.get(f"/api/jobs/{job_id}/").json()["photos"]
    assert [photo["source_url"].rsplit("/", 1)[-1] for photo in photos] == [
        "mug-front.png",
        "mug-side.png",
    ]
    stored = ProductPhoto.objects.filter(job_id=job_id).order_by("position")
    assert [file_store.read(photo.file) for photo in stored] == [MUG_FRONT, MUG_SIDE]


def test_polling_after_an_entry_returns_only_the_entries_since_then(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    job_id = start_job(product_page_url)
    everything = api.get(f"/api/jobs/{job_id}/").json()["activity"]

    since_second = api.get(f"/api/jobs/{job_id}/?after=2").json()["activity"]

    assert [entry["seq"] for entry in everything] == list(range(1, len(everything) + 1))
    assert since_second == everything[2:]


def test_every_model_call_is_recorded_with_its_cost_time_outcome_and_judgement(
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)

    job_id = start_job(product_page_url)

    [call] = ModelCall.objects.filter(job_id=job_id)
    assert call.purpose == "check_page"
    assert call.model == "gpt-5-mini"
    assert call.outcome == "succeeded"
    # 1,000 input tokens at $0.25 per million plus 100 output tokens at $2.00 per million.
    assert call.cost_usd == Decimal("0.000450")
    assert call.duration_ms is not None
    assert call.decision == "readable"
    assert call.reason == READABLE["reason"]
    assert "Stoneware Mug" in call.handoff["page_text"]


def test_a_shop_that_is_briefly_down_is_tried_again(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_oneshot_request("/products/mug").respond_with_data("busy", status=503)
    fake_model.respond("check_page", READABLE)

    job_id = start_job(product_page_url)

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "page_read"


def test_a_model_provider_that_is_briefly_down_is_tried_again_and_each_try_is_recorded(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", OutsideServiceDown("503 from the provider"), READABLE)

    job_id = start_job(product_page_url)

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "page_read"
    calls = ModelCall.objects.filter(job_id=job_id).order_by("attempt")
    assert [(call.attempt, call.outcome) for call in calls] == [(1, "failed"), (2, "succeeded")]
    assert "503 from the provider" in calls[0].error


def test_a_model_provider_that_stays_down_fails_the_job_and_says_why(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", *[OutsideServiceDown("503 from the provider")] * 3)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert "503 from the provider" in job["activity"][-1]["reason"]
    assert ModelCall.objects.filter(job_id=job_id, outcome="failed").count() == 3


def test_a_missing_product_page_fails_the_job_without_retrying(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_request("/products/gone").respond_with_data("not found", status=404)

    job_id = start_job(httpserver.url_for("/products/gone"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert "404" in job["activity"][-1]["reason"]
    assert len(httpserver.log) == 1
    assert not ModelCall.objects.filter(job_id=job_id).exists()


def test_a_link_that_redirects_says_which_page_was_actually_read(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    # Shops often send a removed product's link somewhere else, like a category page.
    httpserver.expect_request("/products/old-mug").respond_with_data(
        "", status=301, headers={"Location": "/products/mug"}
    )
    fake_model.respond("check_page", READABLE)

    job_id = start_job(httpserver.url_for("/products/old-mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert any(product_page_url in entry["message"] for entry in job["activity"])
    assert ModelCall.objects.get(job_id=job_id).handoff["page_url"] == product_page_url


def test_a_page_the_model_judges_unreadable_fails_the_job_with_its_reason(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    unreadable = {"decision": "unreadable", "reason": "The page is only a cookie banner."}
    fake_model.respond("check_page", unreadable)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert job["activity"][-1]["reason"] == unreadable["reason"]


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
