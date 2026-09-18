from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_django import DjangoCaptureOnCommitCallbacks
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto, Question

from .conftest import MUG_FRONT, MUG_SIDE, PLAN, READABLE

pytestmark = pytest.mark.django_db

UNREADABLE = {"decision": "unreadable", "reason": "The page lists 12 mugs, not one."}


@pytest.fixture
def answer(
    api: APIClient, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> Callable[..., int]:
    """Answer the job's question through the API, run what it starts, and give the status."""

    def send(job_id: str, data: dict[str, object], format: str = "json") -> int:
        with django_capture_on_commit_callbacks(execute=True):
            response = api.post(f"/api/jobs/{job_id}/answer/", data, format=format)
        status: int = response.status_code
        return status

    return send


@pytest.mark.parametrize("cause", ["a broken link", "a page that isn't one product's"])
def test_a_job_that_couldnt_read_its_page_asks_for_a_working_link_and_reads_it(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
    cause: str,
) -> None:
    if cause == "a broken link":
        httpserver.expect_request("/products/gone").respond_with_data("not found", status=404)
        first_url = httpserver.url_for("/products/gone")
    else:
        httpserver.expect_request("/collections/mugs").respond_with_data(
            "<html><body><h1>All mugs</h1><p>12 products</p></body></html>",
            content_type="text/html",
        )
        first_url = httpserver.url_for("/collections/mugs")
        fake_model.respond("check_page", UNREADABLE)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    job_id = start_job(first_url)

    waiting = api.get(f"/api/jobs/{job_id}/").json()
    assert waiting["status"] == "needs_working_link"
    assert waiting["question"] == {
        "id": Question.objects.get(job_id=job_id).pk,
        "kind": "working_link",
        "question": "We couldn't read one product's page from that link. "
        "What's the link to the product's own page?",
    }

    assert answer(job_id, {"answer": product_page_url}) == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["product_url"] == product_page_url
    assert job["status"] == "planned"
    # The page read is the new one: its photos, its text.
    assert [photo["source_url"] for photo in job["photos"]] == [
        httpserver.url_for("/cdn/mug-front.png"),
        httpserver.url_for("/cdn/mug-side.png"),
    ]
    assert "Hand-thrown, holds 350 ml" in Job.objects.get(pk=job_id).page_text
    # The job now holds the new link, so the history keeps the one it replaced.
    [sent] = [e for e in job["activity"] if e["message"].startswith("You sent a new link")]
    assert (sent["message"], sent["reason"]) == (
        f"You sent a new link: {product_page_url}",
        f"The last link, {first_url}, didn't lead to one product's page, so the page is "
        "read again from this one.",
    )
    [asked] = Question.objects.filter(job_id=job_id)
    assert (asked.kind, asked.answer) == ("working_link", product_page_url)
    assert asked.answered_at is not None


@pytest.mark.parametrize(
    "link",
    [
        pytest.param("", id="nothing"),
        pytest.param("not a link", id="not a link"),
        pytest.param(
            "https://shop.example/products/" + "a" * 2000, id="a link over 2,000 characters"
        ),
    ],
)
def test_a_working_link_that_isnt_a_link_is_refused_and_the_job_keeps_waiting(
    api: APIClient,
    httpserver: HTTPServer,
    start_job: Callable[..., str],
    answer: Callable[..., int],
    link: str,
) -> None:
    httpserver.expect_request("/products/gone").respond_with_data("not found", status=404)
    job_id = start_job(httpserver.url_for("/products/gone"))
    before = api.get(f"/api/jobs/{job_id}/").json()
    # A link too long to store would come back as a server error; let it show as one.
    api.raise_request_exception = False

    assert answer(job_id, {"answer": link}) == 400

    after = api.get(f"/api/jobs/{job_id}/").json()
    assert after == before
    assert Question.objects.get(job_id=job_id).answered_at is None


@pytest.fixture
def page_without_photos(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond("check_page", READABLE)
    return httpserver.url_for("/products/mug")


def test_a_page_with_no_usable_photos_asks_for_uploads_and_keeps_them_like_the_pages(
    api: APIClient,
    fake_model: FakeModel,
    page_without_photos: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
) -> None:
    fake_model.respond("plan_ad", PLAN)
    job_id = start_job(page_without_photos)

    waiting = api.get(f"/api/jobs/{job_id}/").json()
    assert waiting["status"] == "needs_product_photos"
    assert waiting["question"] == {
        "id": Question.objects.get(job_id=job_id).pk,
        "kind": "product_photos",
        "question": "The page had no product photo we could use. "
        "Can you upload at least one photo of the product?",
    }

    uploads = [
        SimpleUploadedFile("front.png", MUG_FRONT, content_type="image/png"),
        SimpleUploadedFile("side.jpg", MUG_SIDE, content_type="image/jpeg"),
    ]
    assert answer(job_id, {"photos": uploads}, format="multipart") == 202

    job = api.get(f"/api/jobs/{job_id}/").json()
    assert job["status"] == "planned"
    stored = ProductPhoto.objects.filter(job_id=job_id)
    assert [(photo.position, photo.source_url, photo.file) for photo in stored] == [
        (1, "", f"jobs/{job_id}/photos/1.png"),
        (2, "", f"jobs/{job_id}/photos/2.jpg"),
    ]
    assert [file_store.read(photo.file) for photo in stored] == [MUG_FRONT, MUG_SIDE]
    assert [photo["url"] for photo in job["photos"]] == [
        file_store.url(f"jobs/{job_id}/photos/1.png"),
        file_store.url(f"jobs/{job_id}/photos/2.jpg"),
    ]
    # The plan is made knowing there are two photos.
    assert ModelCall.objects.get(job_id=job_id, purpose="plan_ad").handoff["photo_count"] == 2
    [asked] = Question.objects.filter(job_id=job_id)
    assert (asked.kind, asked.answer) == ("product_photos", "Uploaded front.png, side.jpg")


@pytest.mark.parametrize(
    ("photos", "format"),
    [
        # Sent as JSON: a form with no files has no photos field at all.
        pytest.param([], "json", id="no photos"),
        pytest.param(
            [SimpleUploadedFile("notes.txt", b"not a photo", content_type="text/plain")],
            "multipart",
            id="a file that isn't an image",
        ),
        pytest.param(
            [SimpleUploadedFile("front.heic", b"ftypheic", content_type="image/heic")],
            "multipart",
            id="an image models can't read",
        ),
        pytest.param(
            [
                SimpleUploadedFile("front.png", MUG_FRONT, content_type="image/png"),
                SimpleUploadedFile(
                    "huge.png", b"\x89PNG" + bytes(15_000_001), content_type="image/png"
                ),
            ],
            "multipart",
            id="one photo over 15 MB",
        ),
    ],
)
def test_uploads_that_arent_usable_photos_are_refused_and_the_job_keeps_waiting(
    api: APIClient,
    page_without_photos: str,
    start_job: Callable[..., str],
    answer: Callable[..., int],
    photos: list[SimpleUploadedFile],
    format: str,
) -> None:
    job_id = start_job(page_without_photos)
    before = api.get(f"/api/jobs/{job_id}/").json()

    assert answer(job_id, {"photos": photos}, format=format) == 400

    assert api.get(f"/api/jobs/{job_id}/").json() == before
    assert not ProductPhoto.objects.filter(job_id=job_id).exists()
    assert Question.objects.get(job_id=job_id).answered_at is None


def test_an_upload_in_a_format_models_cant_read_is_refused_saying_which_formats_work(
    api: APIClient,
    page_without_photos: str,
    start_job: Callable[..., str],
) -> None:
    job_id = start_job(page_without_photos)
    photos = [
        SimpleUploadedFile("front.png", MUG_FRONT, content_type="image/png"),
        SimpleUploadedFile("side.heic", b"ftypheic", content_type="image/heic"),
    ]

    response = api.post(f"/api/jobs/{job_id}/answer/", {"photos": photos}, format="multipart")

    assert (response.status_code, response.json()) == (
        400,
        {"photos": ["side.heic isn't a PNG, JPEG, WebP or GIF image."]},
    )
