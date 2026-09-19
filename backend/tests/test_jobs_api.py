import json
import logging
import socket
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import IntegrityError
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto
from jobs.tasks import keep_photo, read_page

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    PLAN,
    PRODUCT_PAGE,
    PUBLIC_ADDRESS,
    READABLE,
    FakeDns,
    openai_answer,
    openai_reply,
    picture,
)

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


def test_reading_the_page_stores_its_text_and_explains_every_step(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    page_text = Job.objects.get(pk=job_id).page_text
    # The count varies with the test server's port, which is part of the page's photo links.
    # Planning follows these four; the planning tests cover its entries.
    assert [(entry["message"], entry["reason"]) for entry in job["activity"][:4]] == [
        (
            "Reading the product page",
            "Every fact and picture in the ad has to come from the page, never made up.",
        ),
        (
            f"Stored {len(page_text)} characters of page text and the page's HTML",
            "The script's claims will be checked against this text, and the HTML shows "
            "exactly what the page said on the day it was read.",
        ),
        ("The page has what the ad needs", READABLE["reason"]),
        (
            "Saved 2 product photos",
            "The ad has to show the real product, so its photos are kept with the job.",
        ),
    ]
    assert "Stoneware Mug" in page_text
    assert "$24.00" in page_text
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in page_text
    assert "tracking code" not in page_text


def test_the_page_text_includes_the_product_data_the_page_declares_for_search_engines(
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(product_page_url)

    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in Job.objects.get(pk=job_id).page_text
    check = ModelCall.objects.get(job_id=job_id, purpose="check_page")
    assert "InStock" in check.handoff["page_text"]


def test_a_long_page_still_hands_the_models_its_product_data(
    fake_model: FakeModel, httpserver: HTTPServer, start_job: Callable[..., str]
) -> None:
    # A page with 203,000 characters of reviews: more than the models are sent. The stock
    # is only in the structured data, which the page text puts after all the words.
    reviews = "<p>" + "Lovely mug, would buy again. " * 7_000 + "</p>"
    httpserver.expect_request("/products/long-mug").respond_with_data(
        PRODUCT_PAGE.format(side="/cdn/mug-side.png").replace("</body>", reviews + "</body>"),
        content_type="text/html; charset=utf-8",
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    httpserver.expect_request("/cdn/mug-side.png").respond_with_data(
        MUG_SIDE, content_type="image/png"
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(httpserver.url_for("/products/long-mug"))

    assert len(Job.objects.get(pk=job_id).page_text) > 200_000
    for purpose in ("check_page", "plan_ad"):
        sent = ModelCall.objects.get(job_id=job_id, purpose=purpose).handoff["page_text"]
        assert len(sent) <= 200_000
        assert sent.startswith("Stoneware Mug | Kiln & Co\nStoneware Mug\n$24.00")
        assert '"availability": "https://schema.org/InStock"' in sent


def test_the_pages_original_html_is_kept_as_a_file_exactly_as_served(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)

    job_id = start_job(product_page_url)

    served = PRODUCT_PAGE.format(side=httpserver.url_for("/cdn/mug-side.png")).encode()
    assert file_store.read(Job.objects.get(pk=job_id).page_html_key) == served


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


def test_a_page_read_run_again_after_a_crash_keeps_each_photo_once(
    api: APIClient, fake_model: FakeModel, product_page_url: str
) -> None:
    # A job whose first read stopped after saving the front photo: the worker died, and
    # the queue hands the task out again. Tasks never run inside this test's transaction.
    job_id = api.post("/api/jobs/", {"product_url": product_page_url}, format="json").json()["id"]
    job = Job.objects.get(pk=job_id)
    keep_photo(job, 1, MUG_FRONT, "image/png", source_url="https://shop.example/front.png")
    Job.objects.filter(pk=job_id).update(status=Job.Status.READING_PAGE)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    read_page.delay(job_id)

    stored = ProductPhoto.objects.filter(job_id=job_id).order_by("position")
    assert [(photo.position, file_store.read(photo.file)) for photo in stored] == [
        (1, MUG_FRONT),
        (2, MUG_SIDE),
    ]
    # The producer is shown each photo once, under the number it's stored at.
    shown = ModelCall.objects.get(job_id=job_id, purpose="plan_ad").images
    assert [image["label"] for image in shown] == ["Photo 1", "Photo 2"]


def test_a_job_cant_keep_two_photos_at_the_same_position() -> None:
    # The backstop to a re-read starting its photos over: two photos stored under one number
    # would show the producer "Photo 1" twice.
    job = Job.objects.create(product_url="https://shop.example/products/mug")
    keep_photo(job, 1, MUG_FRONT, "image/png", source_url="https://shop.example/front.png")

    with pytest.raises(IntegrityError, match='unique constraint "one_photo_per_position"'):
        keep_photo(job, 1, MUG_SIDE, "image/png", source_url="https://shop.example/side.png")


def test_polling_after_an_entry_returns_only_the_entries_since_then(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    job_id = start_job(product_page_url)
    everything = api.get(f"/api/jobs/{job_id}/").json()["activity"]

    since_second = api.get(f"/api/jobs/{job_id}/?after=2").json()["activity"]

    assert [entry["seq"] for entry in everything] == [1, 2, 3, 4, 5, 6]
    assert [entry["seq"] for entry in since_second] == [3, 4, 5, 6]
    assert since_second == everything[2:]


def test_every_model_call_is_recorded_with_its_cost_time_outcome_and_judgement(
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    # The gateway reads its clock when a call starts, then again when the answer arrives.
    clock = iter([100.0, 100.25, 200.0, 203.5])
    monkeypatch.setattr("gateway.gateway.time", SimpleNamespace(monotonic=lambda: next(clock)))

    job_id = start_job(product_page_url)

    call, plan_call = ModelCall.objects.filter(job_id=job_id).order_by("created_at")
    assert call.purpose == "check_page"
    assert call.model == "gpt-5-mini"
    assert call.outcome == "succeeded"
    # 1,000 input tokens at $0.25 per million plus 100 output tokens at $2.00 per million.
    assert call.cost_usd == Decimal("0.000450")
    assert call.duration_ms == 250
    assert call.decision == "readable"
    assert call.reason == READABLE["reason"]
    assert "Stoneware Mug" in call.handoff["page_text"]
    assert (plan_call.purpose, plan_call.model, plan_call.duration_ms) == (
        "plan_ad",
        "gpt-5.6-sol",
        3_500,
    )
    # 1,000 input tokens at $4.00 per million plus 100 output tokens at $20.00 per million.
    assert plan_call.cost_usd == Decimal("0.006000")
    assert (plan_call.decision, plan_call.reason) == ("plan", PLAN["reason"])


def test_a_shop_that_is_briefly_down_is_tried_again(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_oneshot_request("/products/mug").respond_with_data("busy", status=503)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(product_page_url)

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "planned"


def test_a_model_provider_that_is_briefly_down_is_tried_again_and_each_try_is_recorded(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    fake_model.respond("check_page", OutsideServiceDown("503 from the provider"), READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(product_page_url)

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "planned"
    calls = ModelCall.objects.filter(job_id=job_id, purpose="check_page").order_by("attempt")
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


def test_a_missing_product_page_waits_for_a_working_link_without_retrying(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_request("/products/gone").respond_with_data("not found", status=404)

    job_id = start_job(httpserver.url_for("/products/gone"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert "404" in job["activity"][-1]["reason"]
    assert len(httpserver.log) == 1
    assert not ModelCall.objects.filter(job_id=job_id).exists()


def test_a_page_too_big_to_be_a_product_page_says_so_rather_than_blaming_the_shop(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jobs.page.MAX_PAGE_BYTES", 100)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    reason = job["activity"][-1]["reason"]
    assert "too big" in reason
    assert "refused" not in reason


def test_a_link_to_a_private_network_address_is_never_fetched(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
    settings: Settings,
) -> None:
    settings.FETCH_PRIVATE_ADDRESSES = False

    job_id = start_job(product_page_url)  # Served from this machine, like localhost.

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert "private network address" in job["activity"][-1]["reason"]
    assert httpserver.log == []


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
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(httpserver.url_for("/products/old-mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert any(product_page_url in entry["message"] for entry in job["activity"])
    check = ModelCall.objects.get(job_id=job_id, purpose="check_page")
    assert check.handoff["page_url"] == product_page_url


def test_a_page_the_model_judges_unreadable_waits_for_a_working_link_with_its_reason(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    unreadable = {"decision": "unreadable", "reason": "The page is only a cookie banner."}
    fake_model.respond("check_page", unreadable)

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert job["activity"][-1]["reason"] == unreadable["reason"]
    assert job["photos"] == []


def test_a_page_with_no_usable_product_photos_waits_for_photos_and_says_what_was_skipped(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    photo_url = httpserver.url_for("/cdn/removed.png")
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{photo_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/removed.png").respond_with_data("gone", status=404)
    fake_model.respond("check_page", READABLE)

    job_id = start_job(httpserver.url_for("/products/mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_product_photos"
    assert any(
        photo_url in entry["message"] and "404" in entry["reason"] for entry in job["activity"]
    )
    assert "product photo" in job["activity"][-1]["reason"]


def test_a_shop_that_stays_down_fails_the_job_after_three_tries_and_says_why(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data("busy", status=503)

    job_id = start_job(httpserver.url_for("/products/mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert (job["activity"][-1]["message"], job["activity"][-1]["reason"]) == (
        "Could not read the product page",
        "An outside service stayed down after several tries: "
        f"{httpserver.url_for('/products/mug')} answered 503 SERVICE UNAVAILABLE.",
    )
    assert len(httpserver.log) == 3
    assert not ModelCall.objects.filter(job_id=job_id).exists()


def test_a_shop_whose_name_lookup_keeps_failing_is_tried_again_then_fails_the_job(
    api: APIClient, fake_model: FakeModel, dns: FakeDns, start_job: Callable[..., str]
) -> None:
    # The name exists, but the lookup service is having a moment: that can pass.
    dns.records["shop.example"] = socket.gaierror(
        socket.EAI_AGAIN, "Temporary failure in name resolution"
    )

    job_id = start_job("https://shop.example/products/mug")

    assert api.get(f"/api/jobs/{job_id}/").json()["status"] == "failed"
    assert dns.lookups == ["shop.example"] * 3


@pytest.mark.parametrize("private_addresses_blocked", [True, False])
def test_a_link_to_a_website_that_doesnt_exist_waits_for_a_working_link_without_retrying(
    api: APIClient,
    fake_model: FakeModel,
    dns: FakeDns,
    start_job: Callable[..., str],
    settings: Settings,
    private_addresses_blocked: bool,
) -> None:
    settings.FETCH_PRIVATE_ADDRESSES = not private_addresses_blocked

    job_id = start_job("https://shopp.example/products/mug")  # A typo: no such website.

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert "shopp.example doesn't exist" in job["activity"][-1]["reason"]
    assert dns.lookups == ["shopp.example"]
    assert not ModelCall.objects.filter(job_id=job_id).exists()


def test_a_link_that_redirects_forever_waits_for_a_working_link(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_request("/products/loop").respond_with_data(
        "", status=302, headers={"Location": "/products/loop"}
    )

    job_id = start_job(httpserver.url_for("/products/loop"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert "redirected more than 10 times" in job["activity"][-1]["reason"]
    assert len(httpserver.log) == 11  # The link itself, then 10 redirects followed.


def test_a_link_that_redirects_to_a_private_network_address_is_not_followed(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    dns: FakeDns,
    start_job: Callable[..., str],
    settings: Settings,
) -> None:
    settings.FETCH_PRIVATE_ADDRESSES = False
    dns.records["localhost"] = PUBLIC_ADDRESS  # The shop passes the check...
    dns.records["127.0.0.1"] = "127.0.0.1"  # ...and sends us to this machine's own admin.
    admin_url = f"http://127.0.0.1:{httpserver.port}/internal/admin"
    httpserver.expect_request("/old-mug").respond_with_data(
        "", status=302, headers={"Location": admin_url}
    )
    httpserver.expect_request("/internal/admin").respond_with_data("the admin's secrets")

    job_id = start_job(httpserver.url_for("/old-mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_working_link"
    assert job["activity"][-1]["reason"] == (
        f"{admin_url} leads to a private network address, which is never fetched."
    )
    assert [request.path for request, _ in httpserver.log] == ["/old-mug"]


def test_a_photo_link_to_a_private_network_address_is_never_fetched(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    dns: FakeDns,
    start_job: Callable[..., str],
    settings: Settings,
) -> None:
    settings.FETCH_PRIVATE_ADDRESSES = False
    dns.records["localhost"] = PUBLIC_ADDRESS
    dns.records["127.0.0.1"] = "127.0.0.1"
    photo_url = f"http://127.0.0.1:{httpserver.port}/cdn/secret.png"
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{photo_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/secret.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    fake_model.respond("check_page", READABLE)

    job_id = start_job(httpserver.url_for("/products/mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_product_photos"
    assert (
        f"Skipped the photo at {photo_url}",
        (f"{photo_url} leads to a private network address, which is never fetched."),
    ) in [(entry["message"], entry["reason"]) for entry in job["activity"]]
    assert [request.path for request, _ in httpserver.log] == ["/products/mug"]
    assert job["photos"] == []


def test_a_page_that_lists_no_photos_waits_for_product_photos(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond("check_page", READABLE)

    job_id = start_job(httpserver.url_for("/products/mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "needs_product_photos"
    assert [entry["message"] for entry in job["activity"]][-2:] == [
        "The page has what the ad needs",
        "Waiting for product photos",
    ]
    assert job["photos"] == []


def _serve_banner_page(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data(
        "<html>Summer sale!</html>", content_type="text/html"
    )


def _serve_heic_photo(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data(
        b"\x00\x00\x00\x18ftypheic", content_type="image/heic"
    )


def _serve_huge_photo(httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobs.page.MAX_PHOTO_BYTES", 100)
    httpserver.expect_request("/cdn/bad").respond_with_data(
        b"\x89PNG" + b"0" * 200, content_type="image/png"
    )


def _serve_down_photo_host(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data("busy", status=503)


@pytest.mark.parametrize(
    ("serve_bad_photo", "why_skipped"),
    [
        pytest.param(
            _serve_banner_page,
            "It came back as text/html, not a PNG, JPEG, WebP or GIF image.",
            id="not an image",
        ),
        pytest.param(
            _serve_heic_photo,
            "It came back as image/heic, not a PNG, JPEG, WebP or GIF image.",
            id="an image models can't read",
        ),
        pytest.param(
            _serve_huge_photo,
            "{url} is too big to be a product photo, at over 100 bytes.",
            id="too big",
        ),
        pytest.param(
            _serve_down_photo_host,
            "{url} answered 503 SERVICE UNAVAILABLE.",
            id="its server stays down",
        ),
    ],
)
def test_a_photo_that_cant_be_used_is_skipped_with_its_reason_and_the_rest_are_kept(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    serve_bad_photo: Callable[[HTTPServer, pytest.MonkeyPatch], None],
    why_skipped: str,
) -> None:
    bad_url = httpserver.url_for("/cdn/bad")
    good_url = httpserver.url_for("/cdn/mug-front.png")
    # 69 bytes: small enough to be kept when the too-big case lowers the limit to 100.
    good_photo = picture(1, 1, (143, 170, 140))
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{bad_url}">'
        f'<meta property="og:image" content="{good_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        good_photo, content_type="image/png"
    )
    serve_bad_photo(httpserver, monkeypatch)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    job_id = start_job(httpserver.url_for("/products/mug"))

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    messages = [entry["message"] for entry in job["activity"]]
    skipped = job["activity"][messages.index(f"Skipped the photo at {bad_url}")]
    assert skipped["reason"] == why_skipped.format(url=bad_url)
    assert messages[messages.index("Planning the ad") - 1] == "Saved 1 product photo"
    stored = ProductPhoto.objects.filter(job_id=job_id)
    assert [(photo.source_url, file_store.read(photo.file)) for photo in stored] == [
        (good_url, good_photo)
    ]


def test_the_real_openai_code_sends_the_page_and_reads_back_a_judgement(
    api: APIClient,
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    openai_server(
        openai_answer(READABLE),
        openai_answer(PLAN),
    )

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    assert ("The page has what the ad needs", READABLE["reason"]) in [
        (entry["message"], entry["reason"]) for entry in job["activity"]
    ]
    assert [scene["line"] for scene in job["scenes"]] == [
        "Meet the Stoneware Mug from Kiln & Co.",
        "Hand-thrown, holds 350 ml, and dishwasher safe.",
        "Yours for $24.00.",
    ]
    check, plan = [
        request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"
    ]
    assert check["model"] == "gpt-5-mini"
    assert json.loads(check["input"])["page_url"] == product_page_url
    assert "Stoneware Mug" in json.loads(check["input"])["page_text"]
    assert check["text"]["format"]["type"] == "json_schema"
    assert plan["model"] == "gpt-5.6-sol"
    # The plan's handoff comes first in its message, ahead of the product photos.
    [message] = plan["input"]
    plan_handoff = json.loads(message["content"][0]["text"])
    assert "Stoneware Mug" in plan_handoff["page_text"]
    assert plan["text"]["format"]["type"] == "json_schema"
    call = ModelCall.objects.get(job_id=job_id, purpose="check_page")
    assert (call.provider, call.outcome, call.input_tokens, call.output_tokens) == (
        "openai",
        "succeeded",
        1_200,
        300,
    )
    assert call.cost_usd == Decimal("0.0009")  # 1,200 x $0.25/M + 300 x $2.00/M.


def test_an_openai_reply_that_cant_be_used_fails_the_job_and_says_why(
    api: APIClient,
    openai_server: Callable[[Any], None],
    product_page_url: str,
    start_job: Callable[..., str],
) -> None:
    openai_server(openai_reply({"type": "refusal", "refusal": "I can't help with that."}))

    job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert job["activity"][-1]["message"] == "Could not check the product page"
    assert job["activity"][-1]["reason"] == (
        "The model's answer couldn't be used: gpt-5-mini gave no usable answer for check_page."
    )
    assert job["photos"] == []


def test_an_unexpected_error_fails_the_job_and_leaves_the_details_in_the_server_log(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    start_job: Callable[..., str],
    settings: Settings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Storage points at a file, not a folder, so saving the page's HTML blows up.
    (tmp_path / "not-a-folder").write_text("")
    settings.MEDIA_ROOT = tmp_path / "not-a-folder"

    with caplog.at_level(logging.ERROR, logger="jobs.tasks"):
        job_id = start_job(product_page_url)

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "failed"
    assert (job["activity"][-1]["message"], job["activity"][-1]["reason"]) == (
        "Something went wrong while reading the page",
        "An unexpected error stopped the job; the details are in the server log.",
    )
    [logged] = caplog.records
    assert logged.getMessage() == f"Reading the page failed for job {job_id}"
    assert logged.exc_info is not None


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
