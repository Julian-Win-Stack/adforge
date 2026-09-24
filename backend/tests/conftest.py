import io
import json
import socket
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import PIL.Image
import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from pydantic import BaseModel
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import celery_app
from agents.models import ToolCall
from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.gateway import use_model
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider
from gateway.types import ModelReply, ModelRequest, TurnReply, TurnRequest
from jobs.models import Job

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


def picture(width: int, height: int, colour: tuple[int, int, int], format: str = "PNG") -> bytes:
    """A real image file of one colour."""
    file = io.BytesIO()
    PIL.Image.new("RGB", (width, height), colour).save(file, format=format)
    return file.getvalue()


# The mug's photos, told apart by colour. The side one is as big as a phone takes them.
MUG_FRONT = picture(400, 300, (143, 170, 140))  # sage green
MUG_SIDE = picture(1600, 1200, (236, 229, 206))  # cream

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

# What the producer plans for the mug's page: three scenes.
PLAN: dict[str, Any] = {
    "decision": "plan",
    "reason": "Three scenes: what the mug is, what it's like to use, and its price.",
    "question": None,
    "plan": {
        "scenes": [
            {"line": "Meet the Stoneware Mug from Kiln & Co."},
            {"line": "Hand-thrown, holds 350 ml, and dishwasher safe."},
            {"line": "Yours for $24.00."},
        ],
        "product_colour": "sage green",
        "colour_photos": [1],
        "person_looks": "A potter in her thirties in a linen apron, in a sunny workshop.",
        "person_voice": "A warm, relaxed woman in her thirties with a soft British accent.",
    },
}
# The plan's 18 words take the fake voice 9 seconds: it speaks 2 words a second.


def facts_ok(*scenes: int) -> dict[str, Any]:
    """What the fact check answers when every one of `scenes` matches the page."""
    return {
        "decision": "checked",
        "reason": "Every claim is stated on the page.",
        "question": None,
        "lines": [
            {"scene": scene, "verdict": "ok", "problem": None, "page_says": None}
            for scene in scenes
        ],
    }


# The mug plan's fact check, when all three lines match the page.
FACTS_OK = facts_ok(1, 2, 3)


def plan_with(*lines: str) -> dict[str, Any]:
    """PLAN with these lines for its scenes."""
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": [{"line": line} for line in lines]}}


# The planning checks' arguments when the shop owner has made no choice.
NO_CHOICES: dict[str, Any] = {"line_choices": [], "length_choice": None}


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
def session_id(api: APIClient) -> str:
    response = api.post("/api/sessions/", {}, format="json")
    assert response.status_code == 201, response.json()
    started: str = response.json()["id"]
    return started


@pytest.fixture
def say(api: APIClient, session_id: str) -> Callable[..., None]:
    """Send the user's message through the chat, with any photos attached as (name,
    content). The producer runs before the request returns, so script its turns first."""

    def sending(text: str, *photos: tuple[str, bytes]) -> None:
        if photos:
            attached = [
                SimpleUploadedFile(name, content, content_type="image/png")
                for name, content in photos
            ]
            sent = api.post(
                f"/api/sessions/{session_id}/messages/",
                {"text": text, "photos": attached},
                format="multipart",
            )
        else:
            sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
        assert sent.status_code == 201, sent.json()

    return sending


def chat(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """The conversation as the browser shows it: who said what."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [(message["role"], message["text"]) for message in messages]


def given_to_the_producer(number: int) -> list[dict[str, Any]]:
    """What the producer's model was given on its `number`th turn (from 1), as recorded."""
    calls = ModelCall.objects.filter(purpose="produce").order_by("created_at", "id")
    conversation: list[dict[str, Any]] = calls[number - 1].handoff["conversation"]
    return conversation


def producer_turns() -> int:
    """How many turns the producer's model has taken, over every producer that ran."""
    return ModelCall.objects.filter(purpose="produce").count()


def results_of(tool: str) -> list[str]:
    """What each call of `tool` handed back to the producer, oldest first."""
    return list(ToolCall.objects.filter(tool=tool).values_list("result", flat=True))


def paid_for() -> list[str]:
    """The purpose of every model call the tools made, oldest first: the producer's own
    turns aside."""
    return list(
        ModelCall.objects.exclude(purpose="produce")
        .order_by("created_at", "id")
        .values_list("purpose", flat=True)
    )


def handoffs(purpose: str) -> list[dict[str, Any]]:
    """What each model call for `purpose` was handed, oldest first."""
    return list(
        ModelCall.objects.filter(purpose=purpose)
        .order_by("created_at", "id")
        .values_list("handoff", flat=True)
    )


def served(link: str, settings: Settings) -> bytes:
    """What the browser gets from a link: the web server hands out MEDIA_ROOT at /media/."""
    assert link.startswith("/media/"), f"{link} isn't a link the web server hands out"
    return (Path(settings.MEDIA_ROOT) / link.removeprefix("/media/")).read_bytes()


def lines() -> list[str]:
    """The line of each of the ad's scenes, in order."""
    return list(Job.objects.get().scenes.values_list("line", flat=True))


def a_producer_last_beat(session_id: str, *, seconds_before_it_counts_as_dead: float) -> None:
    """As if a producer is working in the session, and its last beat was this long before
    it counts as dead. Less than nothing means it already does."""
    ago = settings.PRODUCER_DEAD_AFTER_SECONDS - seconds_before_it_counts_as_dead
    Session.objects.filter(pk=session_id).update(
        producer_running=True, producer_seen_at=timezone.now() - timedelta(seconds=ago)
    )


@pytest.fixture
def page_read(fake_model: FakeModel, product_page_url: str, say: Callable[..., None]) -> str:
    """A chat whose ad has its product page read, with the page's 2 photos. Gives the link."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {product_page_url}")
    return product_page_url


@pytest.fixture
def planned(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """A chat whose ad is planned: three scenes."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)
    say("Plan it")


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


def openai_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """A reply whose text is `answer` as JSON, the way structured output comes back."""
    return openai_reply({"type": "output_text", "text": json.dumps(answer), "annotations": []})


def openai_turn(says: str = "", *calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """An agent's turn as OpenAI sends it: what it says, then each tool it calls, given as
    (call id, tool, arguments)."""
    reply = openai_reply({"type": "output_text", "text": says, "annotations": []})
    if not says:
        reply["output"] = []
    reply["output"] += [
        {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": tool,
            "arguments": json.dumps(arguments),
            "status": "completed",
        }
        for call_id, tool, arguments in calls
    ]
    return reply


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

    with use_model(OpenAIText()):
        yield reply_with


class OpenAIText(FakeModel):
    """Text calls and agents' turns go to our real OpenAI code; the portrait and voice are
    faked, so a test of what the models are sent needs no stand-in picture or voice service."""

    name = "openai"

    def __init__(self) -> None:
        super().__init__()
        self._openai = OpenAIProvider()

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        return self._openai.complete(request)

    def take_turn(self, request: TurnRequest) -> TurnReply:
        return self._openai.take_turn(request)


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
