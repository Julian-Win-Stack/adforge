from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.models import ProductPhoto, Question

from .conftest import MUG_FRONT, READABLE

pytestmark = pytest.mark.django_db


@pytest.fixture
def page_without_photos(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond("check_page", READABLE)
    return httpserver.url_for("/products/mug")


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
