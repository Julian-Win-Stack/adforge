import socket
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
from gateway.openai_adapter import OpenAIProvider

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
      "image": ["/cdn/mug-front.png", "{side}"],
      "brand": {{"@type": "Brand", "name": "Kiln & Co"}},
      "offers": {{"@type": "Offer", "price": "24.00", "priceCurrency": "USD",
                  "availability": "https://schema.org/InStock"}}}}
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


# What the page check answers for the mug's page.
READABLE = {"decision": "readable", "reason": "The page names the mug, its price and its size."}

# What the producer plans for the mug's page: three scenes and two brand colours.
PLAN: dict[str, Any] = {
    "decision": "plan",
    "reason": "Three scenes: what the mug is, what it's like to use, and its price.",
    "question": None,
    "plan": {
        "scenes": [
            {"line": "Meet the Stoneware Mug from Kiln & Co.", "slot_seconds": 4},
            {"line": "Hand-thrown, holds 350 ml, and dishwasher safe.", "slot_seconds": 5},
            {"line": "Yours for $24.00.", "slot_seconds": 3},
        ],
        "brand_colours": ["#1F3A5F", "#F4EDE4"],
    },
}


@pytest.fixture(autouse=True)
def _isolated_outside_world(settings: Settings, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.RETRY_DELAYS_SECONDS = [0, 0]
    # The test shop runs on this machine, an address real jobs are never allowed to fetch.
    settings.FETCH_PRIVATE_ADDRESSES = True


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


def openai_reply(content: dict[str, Any], status: str = "completed") -> dict[str, Any]:
    """A Responses API reply as OpenAI sends it, billed for 1,200 tokens in and 300 out."""
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_789_000_000,
        "model": "gpt-5-mini",
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": status,
                "content": [content],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1_200,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 300,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1_500,
        },
    }


@pytest.fixture
def openai_server(httpserver: HTTPServer, settings: Settings) -> Iterator[Callable[..., None]]:
    """Our real OpenAI code, talking to a stand-in OpenAI server on this machine.
    Call it with the replies the server should send, one per request, in order."""
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")

    def reply_with(*replies: dict[str, Any]) -> None:
        for reply in replies:
            httpserver.expect_oneshot_request("/v1/responses", method="POST").respond_with_json(
                reply
            )

    with use_model(OpenAIProvider()):
        yield reply_with


class FakeDns:
    """What host names look up to when a job checks where a link points. Only that check
    sees these; the fetch itself still goes to the real host. A name not in `records`
    doesn't exist, and a record can be an error to raise instead of an address."""

    def __init__(self) -> None:
        self.records: dict[str, str | socket.gaierror] = {}
        self.lookups: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(socket, name)

    def getaddrinfo(self, host: str, port: int, **_: Any) -> list[Any]:
        self.lookups.append(host)
        record = self.records.get(host)
        if record is None:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
        if isinstance(record, socket.gaierror):
            raise record
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (record, port))]


# example.com's address: anywhere on the public internet.
PUBLIC_ADDRESS = "93.184.215.14"


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> FakeDns:
    """Name lookups as a real job sees them, with private addresses refused. The test shop
    runs on this machine, so a test that needs it to pass the check points it at
    PUBLIC_ADDRESS."""
    settings.FETCH_PRIVATE_ADDRESSES = False
    fake = FakeDns()
    monkeypatch.setattr("jobs.page.socket", fake)
    return fake
