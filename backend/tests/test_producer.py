"""The producer, driven through the chat the way the browser uses it. Its model is faked at
the gateway with a scripted sequence of turns, so each test walks an exact path through the
tools and checks what the chat shows and what was stored."""

import json
from collections.abc import Callable
from decimal import Decimal

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider
from gateway.types import Said, StepFinished, TurnHandoff, TurnRequest
from jobs.models import Job

from .conftest import (
    FACTS_OK,
    MUG_FRONT,
    MUG_SIDE,
    PLAN,
    READABLE,
    chat,
    given_to_the_producer,
    openai_answer,
    openai_turn,
    picture,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def test_the_producer_reads_the_page_its_given_and_tells_the_user_what_it_found(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
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
    say: Callable[..., None],
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
    say: Callable[..., None],
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
    say: Callable[..., None],
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
    tools = {tool["name"]: tool for tool in first["tools"]}
    assert list(tools) == [
        "read_page",
        "use_photos",
        "plan_ad",
        "create_person",
        "run_planning_checks",
        "make_starting_picture",
        "make_line_audio",
        "transcribe_line_audio",
        "make_clip",
        "assemble_ad",
    ]
    assert {(tool["type"], tool["strict"]) for tool in tools.values()} == {("function", True)}
    read_page = tools["read_page"]
    assert read_page["description"].startswith("Start a job for an ad")
    assert read_page["parameters"]["required"] == ["link", "target_seconds"]
    # A strict tool's arguments, nested ones too, list every field and allow no others.
    checks = tools["run_planning_checks"]["parameters"]
    assert checks["required"] == ["line_choices", "length_choice"]
    line_choice = checks["$defs"]["LineChoice"]
    assert (line_choice["required"], line_choice["additionalProperties"]) == (
        ["scene", "choice", "own_line"],
        False,
    )
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


def test_the_real_openai_code_tells_the_producer_a_step_finished_as_the_system_not_the_user(
    httpserver: HTTPServer, settings: Settings
) -> None:
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")
    httpserver.expect_oneshot_request("/v1/responses", method="POST").respond_with_json(
        openai_turn("Scene 1's picture is ready!")
    )
    finished = "Background step finished: scene 1's starting picture is ready (version 1)."

    reply = OpenAIProvider().take_turn(
        TurnRequest(
            purpose="produce",
            model="gpt-5.6-sol",
            instructions="You are the producer.",
            handoff=TurnHandoff(
                conversation=[Said(by="user", text="Make it"), StepFinished(text=finished)],
                tools=[],
            ),
            tools=(),
        )
    )

    assert reply.turn.says == "Scene 1's picture is ready!"
    (request, _) = httpserver.log[0]
    assert request.get_json()["input"] == [
        {"role": "user", "content": "Make it"},
        {"role": "developer", "content": finished},
    ]


def attachments(api: APIClient, session_id: str) -> list[tuple[str, str, list[str]]]:
    """The conversation as the browser shows it, with the kind of each file a message carries."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [
        (message["role"], message["text"], [each["kind"] for each in message["attachments"]])
        for message in messages
    ]


def test_the_producer_takes_a_brief_to_a_checked_plan_that_is_ready_to_render(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(
            says="I'll read your product page first.",
            calls=[("read_page", {"link": product_page_url, "target_seconds": 15})],
        ),
        turn(says="Now I'll plan the ad.", calls=[("plan_ad", {})]),
        turn(says="Next, the person who presents it.", calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", {"line_choices": [], "length_choice": None})]),
        turn(says="Meet your presenter! The script is checked and ready to make."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make me a 15 second ad for {product_page_url}")

    job = Job.objects.get(session_id=session_id)
    assert job.status == "ready_to_render"
    assert list(job.scenes.values_list("line", flat=True)) == [
        scene["line"] for scene in PLAN["plan"]["scenes"]
    ]
    # The person is shown in the chat as soon as it is made: a message with no words that
    # carries the portrait and the voice reading the script.
    assert attachments(api, session_id) == [
        ("user", f"Make me a 15 second ad for {product_page_url}", []),
        ("agent", "I'll read your product page first.", []),
        ("agent", "Now I'll plan the ad.", []),
        ("agent", "Next, the person who presents it.", []),
        ("agent", "", ["picture", "sound"]),
        ("agent", "Meet your presenter! The script is checked and ready to make.", []),
    ]
    person = api.get(f"/api/sessions/{session_id}/messages/").json()[4]["attachments"]
    portrait, voice = job.produced.get(kind="portrait"), job.produced.get(kind="voice")
    assert [each["url"] for each in person] == [
        file_store.url(portrait.file),
        file_store.url(voice.file),
    ]
    # The producer is told what that message carries, so it can talk about it.
    assert given_to_the_producer(4)[-1] == {
        "kind": "said",
        "by": "agent",
        "text": "[Attached 1 picture and 1 sound]",
    }
    # Each tool's model calls are recorded against its checkpoint.
    checkpoints = ToolCall.objects.all()
    assert [
        (each.tool, each.job_id, list(each.model_calls.values_list("purpose", flat=True)))
        for each in checkpoints
    ] == [
        ("read_page", job.pk, ["check_page"]),
        ("plan_ad", job.pk, ["plan_ad"]),
        ("create_person", job.pk, ["draw_person", "design_voice", "measure_voice"]),
        ("run_planning_checks", job.pk, ["fact_check"]),
    ]
    assert "ready to render" in checkpoints[3].result
    # Every model call knows the session it was for, the producer's own turns included, so
    # the session's whole cost adds up from them.
    assert {str(call.session_id) for call in ModelCall.objects.all()} == {session_id}
    assert ModelCall.objects.filter(session_id=session_id, purpose="produce").count() == 5


def test_the_plan_reads_the_whole_conversation_so_an_answer_is_read_next_to_its_question(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    question = "The page shows $24.00 and a sale price of $19.00. Which should the ad say?"
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says=question),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The page gives two prices, and the ad must say the one a buyer pays.",
            "question": question,
            "plan": None,
        },
    )
    say(f"Make an ad for {product_page_url}")

    # The planner's question is the tool's result, for the producer to put to the user.
    assert question in given_to_the_producer(3)[-1]["result"]
    assert not Job.objects.get().scenes.exists()

    fake_model.respond(
        "produce", turn(calls=[("plan_ad", {})]), turn(says="Planned with the sale price.")
    )
    fake_model.respond("plan_ad", PLAN)
    say("The sale one")

    first, second = ModelCall.objects.filter(purpose="plan_ad")
    assert first.handoff["conversation"] == [
        {"by": "user", "text": f"Make an ad for {product_page_url}"}
    ]
    assert second.handoff["conversation"] == [
        {"by": "user", "text": f"Make an ad for {product_page_url}"},
        {"by": "producer", "text": question},
        {"by": "user", "text": "The sale one"},
    ]
    assert Job.objects.get().scenes.count() == 3


def test_the_users_photos_are_added_to_the_job_after_the_pages(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I kept 2 photos of your mug."),
    )
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {product_page_url}")
    own = picture(300, 400, (60, 90, 70))
    fake_model.respond(
        "produce",
        turn(calls=[("use_photos", {})]),
        turn(says="I've added your photo to the page's two."),
    )

    say("Use my photo too", ("mine.png", own))

    job = Job.objects.get()
    photos = list(job.photos.all())
    assert [(photo.position, photo.source_url) for photo in photos] == [
        (1, product_page_url.replace("/products/mug", "/cdn/mug-front.png")),
        (2, product_page_url.replace("/products/mug", "/cdn/mug-side.png")),
        (3, ""),
    ]
    assert [file_store.read(photo.file) for photo in photos] == [MUG_FRONT, MUG_SIDE, own]
    assert "3 product photos" in ToolCall.objects.get(tool="use_photos").result
