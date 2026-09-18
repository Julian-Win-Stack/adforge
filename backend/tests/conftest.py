from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_django import DjangoCaptureOnCommitCallbacks, Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import celery_app
from gateway.fake import FakeModel
from gateway.gateway import use_model

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

MUG_FRONT = b"\x89PNG front of the mug"
MUG_SIDE = b"\x89PNG side of the mug"

PRODUCT_PAGE = """<!doctype html>
<html>
<head>
  <title>Stoneware Mug | Kiln & Co</title>
  <meta property="og:image" content="{side}">
  <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Product", "name": "Stoneware Mug",
      "image": ["/cdn/mug-front.png", "{side}"]}}
  </script>
  <script>window.analytics = "tracking code, not page text";</script>
  <style>.price {{ color: red; }}</style>
</head>
<body>
  <h1>Stoneware Mug</h1>
  <p class="price">$24.00</p>
  <p>Hand-thrown, holds 350 ml, dishwasher safe.</p>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _isolated_outside_world(settings: Settings, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.RETRY_DELAYS_SECONDS = [0, 0]


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def fake_model() -> Iterator[FakeModel]:
    fake = FakeModel()
    with use_model(fake):
        yield fake


@pytest.fixture
def product_page_url(httpserver: HTTPServer) -> str:
    """A shop served from a real local web server, with one product page and its photos."""
    side = httpserver.url_for("/cdn/mug-side.png")
    httpserver.expect_request("/products/mug").respond_with_data(
        PRODUCT_PAGE.format(side=side), content_type="text/html; charset=utf-8"
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    httpserver.expect_request("/cdn/mug-side.png").respond_with_data(
        MUG_SIDE, content_type="image/png"
    )
    return httpserver.url_for("/products/mug")


@pytest.fixture
def start_job(
    api: APIClient, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> Callable[..., str]:
    """Start a job through the API and run its background work to the end."""

    def start(product_url: str, **extra: Any) -> str:
        with django_capture_on_commit_callbacks(execute=True):
            response = api.post("/api/jobs/", {"product_url": product_url, **extra}, format="json")
        assert response.status_code == 201, response.json()
        job_id: str = response.json()["id"]
        return job_id

    return start
