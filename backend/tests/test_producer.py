"""The producer, driven through the chat the way the browser uses it. Its model is faked at
the gateway with a scripted sequence of turns, so each test walks an exact path through the
tools and checks what the chat shows and what was stored."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job

from .conftest import MUG_FRONT, READABLE, openai_answer, openai_turn

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def session_id(api: APIClient) -> str:
    response = api.post("/api/sessions/", {}, format="json")
    assert response.status_code == 201, response.json()
    started: str = response.json()["id"]
    return started


@pytest.fixture
def say(api: APIClient, session_id: str) -> Callable[[str], None]:
    """Send the user's message. The producer runs before the request returns."""

    def sending(text: str) -> None:
        sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
        assert sent.status_code == 201, sent.json()

    return sending


def chat(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """The conversation as the browser shows it: who said what."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [(message["role"], message["text"]) for message in messages]


def given_to_the_producer(turn: int) -> list[dict[str, Any]]:
    """What the producer's model was given on its `turn`th turn (from 1), as recorded."""
    calls = ModelCall.objects.filter(purpose="produce").order_by("created_at", "id")
    conversation: list[dict[str, Any]] = calls[turn - 1].handoff["conversation"]
    return conversation


def test_the_producer_reads_the_page_its_given_and_tells_the_user_what_it_found(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[[str], None],
) -> None:
    fake_model.respond(
        "produce",
        turn(
            says="I'll read your product page first.",
            calls=[("read_page", {"link": product_page_url, "target_seconds": 15})],
        ),
        turn(says="Your mug's page has what the ad needs, and two photos of it."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make me a 15 second ad for {product_page_url}")

    assert chat(api, session_id) == [
        ("user", f"Make me a 15 second ad for {product_page_url}"),
        ("agent", "I'll read your product page first."),
        ("agent", "Your mug's page has what the ad needs, and two photos of it."),
    ]
    job = Job.objects.get(session_id=session_id)
    assert (job.product_url, job.target_seconds) == (product_page_url, 15)
    assert "Stoneware Mug" in job.page_text
    assert job.photos.count() == 2
    # The checkpoint: who called which tool, with what, what it produced, and what it cost.
    checkpoint = ToolCall.objects.get()
    assert checkpoint.agent == "producer"
    assert checkpoint.tool == "read_page"
    assert checkpoint.arguments == {"link": product_page_url, "target_seconds": 15}
    assert checkpoint.job == job
    assert checkpoint.finished
    assert "Kept 2 product photos" in checkpoint.result
    # Checking the page cost 1,000 tokens in and 100 out on gpt-5-mini.
    assert checkpoint.cost_usd() == Decimal("0.00045")
    # The producer's second turn was given what the tool produced.
    assert given_to_the_producer(2)[-1] == {
        "kind": "tool_use",
        "call_id": checkpoint.call_id,
        "tool": "read_page",
        "arguments": {"link": product_page_url, "target_seconds": 15},
        "result": checkpoint.result,
    }


def test_the_producer_is_told_every_photo_the_page_had_that_was_skipped_and_why(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    session_id: str,
    say: Callable[[str], None],
) -> None:
    gone_url = httpserver.url_for("/cdn/removed.png")
    heic_url = httpserver.url_for("/cdn/mug.heic")
    front_url = httpserver.url_for("/cdn/mug-front.png")
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{gone_url}">'
        f'<meta property="og:image" content="{heic_url}">'
        f'<meta property="og:image" content="{front_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/removed.png").respond_with_data("gone", status=404)
    httpserver.expect_request("/cdn/mug.heic").respond_with_data(
        b"not a format models read", content_type="image/heic"
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    page_url = httpserver.url_for("/products/mug")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": page_url, "target_seconds": None})]),
        turn(says="I kept one photo of your mug. Two others couldn't be used."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {page_url}")

    result = given_to_the_producer(2)[-1]["result"]
    assert f"Skipped 2 photos:\n- {gone_url}: {gone_url} answered 404 NOT FOUND" in result
    assert f"- {heic_url}: It came back as image/heic, not a PNG, JPEG, WebP or GIF image." in (
        result
    )
    assert "Kept 1 product photo." in result
    assert chat(api, session_id)[-1] == (
        "agent",
        "I kept one photo of your mug. Two others couldn't be used.",
    )


def _serve_missing_page(httpserver: HTTPServer, fake_model: FakeModel) -> None:
    httpserver.expect_request("/products/mug").respond_with_data("not found", status=404)


def _serve_cookie_wall(httpserver: HTTPServer, fake_model: FakeModel) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body>Accept our cookies to continue</body></html>", content_type="text/html"
    )
    fake_model.respond(
        "check_page", {"decision": "unreadable", "reason": "The page is only a cookie banner."}
    )


def _serve_page_without_photos(httpserver: HTTPServer, fake_model: FakeModel) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond("check_page", READABLE)


@pytest.mark.parametrize(
    ("serve_page", "told"),
    [
        pytest.param(
            _serve_missing_page,
            "The page couldn't be read: {url} answered 404 NOT FOUND, so trying again won't "
            "help. Ask the shop owner for a working link to the product's own page.",
            id="the link is broken",
        ),
        pytest.param(
            _serve_cookie_wall,
            "The page was read but can't be used: The page is only a cookie banner. Ask the "
            "shop owner for a link to the product's own page.",
            id="the page isn't one product's",
        ),
        pytest.param(
            _serve_page_without_photos,
            "Kept 0 product photos.\nEvery scene is made from a product photo, so ask the "
            "shop owner to attach at least one.",
            id="the page has no photo to use",
        ),
    ],
)
def test_a_page_the_ad_cant_be_made_from_is_reported_to_the_producer_with_the_reason(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[[str], None],
    serve_page: Callable[[HTTPServer, FakeModel], None],
    told: str,
) -> None:
    page_url = httpserver.url_for("/products/mug")
    serve_page(httpserver, fake_model)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": page_url, "target_seconds": None})]),
        turn(says="I couldn't use that page. Could you send the link to the mug's own page?"),
    )

    say(f"Make an ad for {page_url}")

    assert told.format(url=page_url) in given_to_the_producer(2)[-1]["result"]


def test_the_real_openai_code_gives_the_producer_its_tools_and_reads_back_what_it_calls(
    api: APIClient,
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    session_id: str,
    say: Callable[[str], None],
) -> None:
    arguments = {"link": product_page_url, "target_seconds": 15}
    openai_server(
        openai_turn("I'll read your product page first.", ("call_a1", "read_page", arguments)),
        openai_answer(READABLE),
        openai_turn("Your mug's page has what the ad needs."),
    )

    say(f"Make me a 15 second ad for {product_page_url}")

    assert chat(api, session_id)[1:] == [
        ("agent", "I'll read your product page first."),
        ("agent", "Your mug's page has what the ad needs."),
    ]
    first, _check, second = [
        request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"
    ]
    assert first["model"] == "gpt-5.6-sol"
    assert first["input"] == [
        {"role": "user", "content": f"Make me a 15 second ad for {product_page_url}"}
    ]
    [read_page] = first["tools"]
    assert (read_page["type"], read_page["name"], read_page["strict"]) == (
        "function",
        "read_page",
        True,
    )
    assert read_page["description"].startswith("Start a job for an ad")
    assert read_page["parameters"]["required"] == ["link", "target_seconds"]
    # Its second turn is given its first: what it said, the tool it called, and the result.
    checkpoint = ToolCall.objects.get()
    assert (checkpoint.call_id, checkpoint.arguments) == ("call_a1", arguments)
    assert second["input"][1:] == [
        {"role": "assistant", "content": "I'll read your product page first."},
        {
            "type": "function_call",
            "call_id": "call_a1",
            "name": "read_page",
            "arguments": json.dumps(arguments),
        },
        {"type": "function_call_output", "call_id": "call_a1", "output": checkpoint.result},
    ]
    turns = ModelCall.objects.filter(purpose="produce")
    assert [(turn.provider, turn.outcome, turn.tool_call) for turn in turns] == [
        ("openai", "succeeded", None),
        ("openai", "succeeded", None),
    ]
    # gpt-5.6-sol: 1,200 x $4.00/M in + 300 x $20.00/M out = $0.0048 + $0.006.
    assert [turn.cost_usd for turn in turns] == [Decimal("0.0108"), Decimal("0.0108")]
