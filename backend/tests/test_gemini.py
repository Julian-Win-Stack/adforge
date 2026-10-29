"""Our real Gemini code, watching videos through a stand-in Gemini server on this machine
that takes requests and replies the way Gemini's API reference documents them
(ai.google.dev/api/generate-content and ai.google.dev/api/files, read 2026-10-29)."""

import base64
import json
import time
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway import catalog
from gateway.gateway import use_model, watch_video
from gateway.gemini_adapter import GeminiFailed, GeminiProvider
from gateway.models import ModelCall
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    NotSetUp,
    UnusableReply,
)

from .conftest import a_video

pytestmark = pytest.mark.django_db

GENERATE = "/v1beta/models/gemini-3.8-flash:generateContent"


class Moments(BaseModel):
    moments: list[str]


class Told(Handoff):
    product_name: str


@pytest.fixture
def gemini(httpserver: HTTPServer, settings: Settings) -> GeminiProvider:
    settings.GEMINI_API_KEY = "gemini-test-key"
    settings.GEMINI_BASE_URL = httpserver.url_for("").rstrip("/")
    return GeminiProvider()


@pytest.fixture
def video_key() -> str:
    return file_store.save("videos/page-video.mp4", a_video(540, 960, sound=True))


def _watch(gemini: GeminiProvider, video_key: str) -> Moments:
    with use_model(gemini):
        return watch_video(
            job=None,
            purpose="watch_video",
            instructions="Watch the product's video.",
            handoff=Told(product_name="Stoneware Mug"),
            video_key=video_key,
            output=Moments,
        )


def answer(text: str, *, finish: str = "STOP") -> dict[str, Any]:
    """Gemini's reply with `text` as its answer, billed as the watcher test's first Gemini
    call was (runs/watcher-test/cache/gemini/garject/run-1.json)."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text, "thoughtSignature": "c2lnbmVk"}],
                    "role": "model",
                },
                "finishReason": finish,
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 18405,
            "candidatesTokenCount": 1328,
            "totalTokenCount": 23210,
            "promptTokensDetails": [
                {"modality": "TEXT", "tokenCount": 198},
                {"modality": "VIDEO", "tokenCount": 18207},
            ],
            "thoughtsTokenCount": 3477,
            "serviceTier": "standard",
        },
        "modelVersion": "gemini-3.8-flash",
        "responseId": "fake-response-1",
    }


TWISTED_OFF = answer(json.dumps({"moments": ["The lid is twisted off."]}))


def _kept(sent: list[Request], reply: dict[str, Any]) -> Any:
    def replying(request: Request) -> Response:
        sent.append(request)
        return Response(json.dumps(reply), content_type="application/json")

    return replying


def test_a_video_is_sent_inline_at_one_frame_a_second_before_its_handoff_with_the_schema(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str
) -> None:
    sent: list[Request] = []
    httpserver.expect_oneshot_request(GENERATE, method="POST").respond_with_handler(
        _kept(sent, TWISTED_OFF)
    )

    watched = _watch(gemini, video_key)

    assert watched == Moments(moments=["The lid is twisted off."])
    (request,) = sent
    assert request.headers["x-goog-api-key"] == "gemini-test-key"
    call = ModelCall.objects.get()
    assert request.json == {
        "systemInstruction": {"parts": [{"text": "Watch the product's video."}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    # The video before the text, as Gemini's video page advises for one video.
                    {
                        "inlineData": {
                            "mimeType": "video/mp4",
                            "data": base64.b64encode(file_store.read(call.video)).decode(),
                        },
                        "videoMetadata": {"fps": 1.0},
                        "mediaProcessing": "STATIC",
                    },
                    {"text": '{"product_name":"Stoneware Mug"}'},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": {
                "properties": {
                    "moments": {"items": {"type": "string"}, "title": "Moments", "type": "array"}
                },
                "required": ["moments"],
                "title": "Moments",
                "type": "object",
            },
            "mediaResolution": "MEDIA_RESOLUTION_HIGH",
            "thinkingConfig": {"thinkingLevel": "HIGH"},
        },
    }
    assert (call.provider, call.outcome) == ("gemini", "succeeded")
    # Thinking is billed as output: 1,328 + 3,477 tokens.
    assert (call.input_tokens, call.output_tokens) == (18405, 4805)
    # What the watcher test reckoned that call cost, $0.0318225 at $0.75 and $3.75 a million
    # tokens, kept to the millionth of a dollar.
    assert call.cost_usd == Decimal("0.031823")


def test_a_video_too_big_to_send_inline_is_put_in_geminis_file_store_and_watched_from_there(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_INLINE_REQUEST_BYTES = 1_000
    uri = httpserver.url_for("/v1beta/files/abc-123")
    started: list[Request] = []
    uploaded: list[Request] = []
    sent: list[Request] = []

    def starting(request: Request) -> Response:
        started.append(request)
        upload_url = httpserver.url_for("/upload/v1beta/files/session-1")
        return Response("{}", headers={"X-Goog-Upload-URL": upload_url})

    def uploading(request: Request) -> Response:
        uploaded.append(request)
        made = {"name": "files/abc-123", "uri": uri, "mimeType": "video/mp4"}
        return Response(
            json.dumps({"file": {**made, "state": "PROCESSING"}}),
            content_type="application/json",
        )

    httpserver.expect_ordered_request("/upload/v1beta/files", method="POST").respond_with_handler(
        starting
    )
    httpserver.expect_ordered_request(
        "/upload/v1beta/files/session-1", method="POST"
    ).respond_with_handler(uploading)
    # Still being made ready once, then ready.
    httpserver.expect_ordered_request("/v1beta/files/abc-123", method="GET").respond_with_json(
        {"name": "files/abc-123", "uri": uri, "state": "PROCESSING"}
    )
    httpserver.expect_ordered_request("/v1beta/files/abc-123", method="GET").respond_with_json(
        {"name": "files/abc-123", "uri": uri, "state": "ACTIVE"}
    )
    httpserver.expect_ordered_request(GENERATE, method="POST").respond_with_handler(
        _kept(sent, TWISTED_OFF)
    )

    watched = _watch(gemini, video_key)

    assert watched == Moments(moments=["The lid is twisted off."])
    the_video = file_store.read(ModelCall.objects.get().video)
    (start,) = started
    assert start.headers["x-goog-api-key"] == "gemini-test-key"
    assert start.headers["X-Goog-Upload-Protocol"] == "resumable"
    assert start.headers["X-Goog-Upload-Command"] == "start"
    assert start.headers["X-Goog-Upload-Header-Content-Length"] == str(len(the_video))
    assert start.headers["X-Goog-Upload-Header-Content-Type"] == "video/mp4"
    (upload,) = uploaded
    assert upload.headers["X-Goog-Upload-Offset"] == "0"
    assert upload.headers["X-Goog-Upload-Command"] == "upload, finalize"
    assert upload.get_data() == the_video
    (request,) = sent
    assert request.json["contents"][0]["parts"][0] == {
        "fileData": {"mimeType": "video/mp4", "fileUri": uri},
        "videoMetadata": {"fps": 1.0},
        "mediaProcessing": "STATIC",
    }


def test_a_video_gemini_fails_to_make_ready_fails_the_call_without_watching_it(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_INLINE_REQUEST_BYTES = 1_000
    upload_url = httpserver.url_for("/upload/v1beta/files/session-1")
    httpserver.expect_request("/upload/v1beta/files", method="POST").respond_with_data(
        "{}", headers={"X-Goog-Upload-URL": upload_url}
    )
    httpserver.expect_request("/upload/v1beta/files/session-1", method="POST").respond_with_json(
        {"file": {"name": "files/abc-123", "uri": "https://gemini/abc-123", "state": "FAILED"}}
    )

    with pytest.raises(GeminiFailed, match="couldn't make the video files/abc-123 ready"):
        _watch(gemini, video_key)

    # Asked again it would only fail again.
    assert ModelCall.objects.get().outcome == "failed"
    assert not [request for request, _ in httpserver.log if request.path == GENERATE]


def _stored_by_gemini(httpserver: HTTPServer, state: str) -> None:
    """Gemini's file store taking the upload, the file made in `state`."""
    upload_url = httpserver.url_for("/upload/v1beta/files/session-1")
    made = {"name": "files/abc-123", "uri": httpserver.url_for("/v1beta/files/abc-123")}
    httpserver.expect_request("/upload/v1beta/files", method="POST").respond_with_data(
        "{}", headers={"X-Goog-Upload-URL": upload_url}
    )
    httpserver.expect_request("/upload/v1beta/files/session-1", method="POST").respond_with_json(
        {"file": {**made, "state": state}}
    )
    httpserver.expect_request("/v1beta/files/abc-123", method="GET").respond_with_json(
        {**made, "state": state}
    )


def test_a_video_gemini_is_still_making_ready_after_the_wait_is_given_up_on_as_gemini_down(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_INLINE_REQUEST_BYTES = 1_000
    settings.GEMINI_FILE_WAIT_SECONDS = 0.05
    settings.GEMINI_FILE_POLL_SECONDS = 0.01
    _stored_by_gemini(httpserver, "PROCESSING")

    with pytest.raises(OutsideServiceDown, match="still making the video files/abc-123 ready"):
        _watch(gemini, video_key)

    assert {call.outcome for call in ModelCall.objects.all()} == {"failed"}
    assert not [request for request, _ in httpserver.log if request.path == GENERATE]


def test_a_file_store_that_gives_no_upload_link_fails_the_call_and_isnt_asked_again(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_INLINE_REQUEST_BYTES = 1_000
    httpserver.expect_request("/upload/v1beta/files", method="POST").respond_with_json({})

    with pytest.raises(GeminiFailed, match="gave no link to upload the video to"):
        _watch(gemini, video_key)

    assert ModelCall.objects.get().outcome == "failed"


def test_a_video_in_geminis_file_store_is_not_uploaded_again_when_the_watch_is_asked_again(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_INLINE_REQUEST_BYTES = 1_000
    _stored_by_gemini(httpserver, "ACTIVE")
    down = {"error": {"code": 503, "message": "Try again later.", "status": "UNAVAILABLE"}}
    httpserver.expect_oneshot_request(GENERATE, method="POST").respond_with_json(down, status=503)
    httpserver.expect_request(GENERATE, method="POST").respond_with_json(TWISTED_OFF)

    assert _watch(gemini, video_key) == Moments(moments=["The lid is twisted off."])
    paths = [request.path for request, _ in httpserver.log]
    assert paths.count("/upload/v1beta/files") == 1
    assert paths.count(GENERATE) == 2


SAFETY_BLOCKS = [
    pytest.param(
        {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 9}},
        id="the video or handoff blocked",
    ),
    pytest.param(
        {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}},
        id="the video or handoff prohibited",
    ),
    pytest.param(answer("", finish="SAFETY"), id="the answer stopped for safety"),
    pytest.param(answer("", finish="PROHIBITED_CONTENT"), id="the answer stopped as prohibited"),
]


@pytest.mark.parametrize("reply", SAFETY_BLOCKS)
def test_a_refusal_from_geminis_safety_filter_is_said_to_be_blocked_and_not_asked_again(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, reply: dict[str, Any]
) -> None:
    httpserver.expect_request(GENERATE, method="POST").respond_with_json(reply)

    with pytest.raises(BlockedBySafetyFilter):
        _watch(gemini, video_key)

    assert ModelCall.objects.get().outcome == "failed"


@pytest.mark.parametrize(
    ("status", "said"),
    [
        pytest.param(429, "RESOURCE_EXHAUSTED", id="a rate limit"),
        pytest.param(503, "UNAVAILABLE", id="gemini down"),
    ],
)
def test_a_rate_limit_or_gemini_down_is_asked_again_and_never_counts_as_blocked(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, status: int, said: str
) -> None:
    failing = {"error": {"code": status, "message": "Try again later.", "status": said}}
    httpserver.expect_request(GENERATE, method="POST").respond_with_json(failing, status=status)

    with pytest.raises(OutsideServiceDown) as down:
        _watch(gemini, video_key)

    assert not isinstance(down.value, BlockedBySafetyFilter)
    calls = ModelCall.objects.all()
    assert [call.attempt for call in calls] == [1, 2, 3]
    assert all(str(status) in call.error for call in calls)


def test_a_rate_limit_that_passes_is_watched_on_the_next_try(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str
) -> None:
    httpserver.expect_ordered_request(GENERATE, method="POST").respond_with_json(
        {"error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}},
        status=429,
    )
    httpserver.expect_ordered_request(GENERATE, method="POST").respond_with_json(TWISTED_OFF)

    assert _watch(gemini, video_key) == Moments(moments=["The lid is twisted off."])
    assert [call.outcome for call in ModelCall.objects.all()] == ["failed", "succeeded"]


def test_a_watch_that_takes_too_long_is_given_up_on_and_never_counts_as_blocked(
    httpserver: HTTPServer,
    gemini: GeminiProvider,
    video_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog, "SLOW_ANSWER_SECONDS", {"watch_video": 0.2})

    def slowly(_: Request) -> Response:
        time.sleep(1)
        return Response(json.dumps(TWISTED_OFF), content_type="application/json")

    httpserver.expect_request(GENERATE, method="POST").respond_with_handler(slowly)

    with pytest.raises(AnswerTookTooLong):
        _watch(gemini, video_key)

    assert ModelCall.objects.get().outcome == "failed"
    # The stand-in server is shared: let the slow reply finish, so it isn't logged in the
    # next test's turn.
    deadline = time.monotonic() + 5
    while not httpserver.log and time.monotonic() < deadline:
        time.sleep(0.05)


def test_an_error_gemini_gives_for_another_reason_is_shown_as_its_own_and_not_asked_again(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str
) -> None:
    httpserver.expect_request(GENERATE, method="POST").respond_with_json(
        {
            "error": {
                "code": 400,
                "message": "Request contains an invalid argument.",
                "status": "INVALID_ARGUMENT",
            }
        },
        status=400,
    )

    with pytest.raises(Exception, match="400.*Request contains an invalid argument") as failed:
        _watch(gemini, video_key)

    assert not isinstance(failed.value, (BlockedBySafetyFilter, OutsideServiceDown))
    assert ModelCall.objects.get().outcome == "failed"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(answer('{"moments": "not a list"}'), id="out of the schema"),
        pytest.param(answer('{"moments": ["The lid'), id="cut off"),
        pytest.param(answer('{"moments": []}', finish="MAX_TOKENS"), id="stopped before the end"),
    ],
)
def test_an_answer_that_cant_be_used_is_refused_and_still_billed(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, reply: dict[str, Any]
) -> None:
    httpserver.expect_request(GENERATE, method="POST").respond_with_json(reply)

    with pytest.raises(UnusableReply):
        _watch(gemini, video_key)

    call = ModelCall.objects.get()
    assert (call.outcome, call.input_tokens, call.output_tokens) == ("failed", 18405, 4805)
    assert call.cost_usd == Decimal("0.031823")


def test_without_a_gemini_key_a_video_isnt_watched_and_says_so(
    httpserver: HTTPServer, gemini: GeminiProvider, video_key: str, settings: Settings
) -> None:
    settings.GEMINI_API_KEY = ""

    with pytest.raises(NotSetUp, match="GEMINI_API_KEY"):
        _watch(GeminiProvider(), video_key)

    assert not httpserver.log
