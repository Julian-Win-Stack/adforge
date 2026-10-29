"""Watches videos through Gemini's own HTTP API, sound and all.

From Gemini's API reference (ai.google.dev/api/generate-content, ai.google.dev/api/files)
and its video understanding page (ai.google.dev/gemini-api/docs/video-understanding), read
2026-10-29, and the watcher test's harness (runs/watcher-test/run.py in the project files,
Oct 27 2026), whose calls this one follows:

- `POST /v1beta/models/{model}:generateContent`, keyed by `x-goog-api-key`. The video is one
  part of the user's content, before the handoff's text ("place the text prompt after the
  video part"): its bytes inline (`inlineData`), or a link to it in Gemini's file store
  (`fileData`), with `videoMetadata.fps` for how many frames a second are looked at, which
  only works with static processing (`mediaProcessing: STATIC`, the default). Inline, the
  whole request must stay under 20 MB.
- Structured output: `generationConfig.responseMimeType` "application/json" with the answer's
  JSON Schema as `responseJsonSchema`. Keywords it doesn't enforce, such as `minLength`, it
  strips, so the answer is checked against the schema here too.
- A refusal: `promptFeedback.blockReason` set and no candidates, when what was sent is
  blocked; or a candidate whose `finishReason` is one of the safety reasons, when the answer
  is. Billed as `usageMetadata`: `promptTokenCount` in, and `candidatesTokenCount` plus
  `thoughtsTokenCount` out, as thinking is billed as output.
- Gemini's file store: `POST /upload/v1beta/files` with `X-Goog-Upload-Protocol: resumable`
  and `X-Goog-Upload-Command: start` hands back the link to upload to in its
  `X-Goog-Upload-URL` header; the bytes go there with `X-Goog-Upload-Command: upload,
  finalize` and offset 0, and the reply names the `file` with its `name`, `uri` and `state`.
  A video is watched once `GET /v1beta/{name}` says ACTIVE; FAILED means it never will be.
  Gemini deletes it itself after 48 hours."""

import base64
import hashlib
import json
import time
from typing import Any

import httpx
from django.conf import settings
from pydantic import BaseModel

from adforge.retry import OutsideServiceDown

from . import catalog
from .types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    ModelReply,
    NotSetUp,
    UnusableReply,
    VideoRequest,
)

# Why an answer stopped that are Gemini's safety filter refusing it (FinishReason, in the
# API reference). Every reason `promptFeedback.blockReason` gives is a refusal of what was
# sent.
_SAFETY_FINISHES = frozenset(
    {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }
)


# How long a video put in Gemini's file store is watched from there again (a retry, or the same
# video watched again) rather than uploaded again: under the 48 hours Gemini keeps it.
_REUSE_STORED_SECONDS = 47 * 3600


class GeminiFailed(Exception):
    """Gemini refused a request for a reason of its own that asking again won't change, such
    as a request it can't read. Not its safety filter: that is BlockedBySafetyFilter."""


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._key = settings.GEMINI_API_KEY
        self._client = httpx.Client(
            base_url=settings.GEMINI_BASE_URL,
            headers={"x-goog-api-key": self._key},
            timeout=catalog.ANSWER_SECONDS,
        )
        # The `fileData` of each video already in Gemini's file store, by its bytes' hash,
        # with when it was made ready.
        self._stored_videos: dict[str, tuple[dict[str, str], float]] = {}

    def watch[Out: BaseModel](self, request: VideoRequest[Out]) -> ModelReply[Out]:
        if not self._key:
            raise NotSetUp("Videos are watched on Gemini's API, and GEMINI_API_KEY is not set")
        video = request.video
        inline = {"inlineData": {"mimeType": video.media_type, "data": _base64(video.data)}}
        body = _body(request, inline)
        if len(json.dumps(body)) > settings.GEMINI_INLINE_REQUEST_BYTES:
            body = _body(request, {"fileData": self._stored(request)})
        timeout = catalog.SLOW_ANSWER_SECONDS.get(request.purpose, catalog.ANSWER_SECONDS)
        try:
            response = self._client.post(
                f"/v1beta/models/{request.model}:generateContent", json=body, timeout=timeout
            )
        except httpx.TimeoutException as error:
            if request.purpose in catalog.SLOW_ANSWER_SECONDS:
                raise AnswerTookTooLong(f"Gemini took over {timeout:g} s to answer") from error
            raise OutsideServiceDown(f"Gemini took too long to answer: {error}") from error
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"Gemini could not be reached: {error}") from error
        return _reply(request, _checked(response).json())

    def _stored(self, request: VideoRequest[Any]) -> dict[str, str]:
        """Put the video in Gemini's file store and wait until it can be watched, unless it
        is there already. Gives the part's `fileData`: its media type and link."""
        video = request.video
        digest = hashlib.sha256(video.data).hexdigest()
        if (kept := self._stored_videos.get(digest)) and (
            time.monotonic() - kept[1] < _REUSE_STORED_SECONDS
        ):
            return kept[0]
        started = self._send(
            "POST",
            "/upload/v1beta/files",
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(video.data)),
                "X-Goog-Upload-Header-Content-Type": video.media_type,
            },
            json={"file": {"display_name": request.purpose}},
        )
        upload_url = started.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise GeminiFailed("Gemini's file store gave no link to upload the video to")
        stored: dict[str, Any] = self._send(
            "POST",
            upload_url,
            headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
            content=video.data,
        ).json()["file"]
        waited_since = time.monotonic()
        while stored.get("state") != "ACTIVE":
            if stored.get("state") == "FAILED":
                raise GeminiFailed(f"Gemini couldn't make the video {stored['name']} ready")
            if time.monotonic() - waited_since > settings.GEMINI_FILE_WAIT_SECONDS:
                raise OutsideServiceDown(
                    f"Gemini was still making the video {stored['name']} ready after "
                    f"{settings.GEMINI_FILE_WAIT_SECONDS:g} s"
                )
            time.sleep(settings.GEMINI_FILE_POLL_SECONDS)
            stored = self._send("GET", f"/v1beta/{stored['name']}").json()
        file_data = {"mimeType": video.media_type, "fileUri": stored["uri"]}
        self._stored_videos[digest] = (file_data, time.monotonic())
        return file_data

    def _send(self, method: str, url: str, **sending: Any) -> httpx.Response:
        try:
            response = self._client.request(method, url, **sending)
        except httpx.TransportError as error:
            raise OutsideServiceDown(
                f"Gemini's file store could not be reached: {error}"
            ) from error
        return _checked(response)


def _body(request: VideoRequest[Any], video: dict[str, Any]) -> dict[str, Any]:
    """The generateContent request: the video part, given `inlineData` or `fileData`, then the
    handoff, answered in the output's shape."""
    return {
        "systemInstruction": {"parts": [{"text": request.instructions}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        **video,
                        "videoMetadata": {"fps": request.frames_per_second},
                        "mediaProcessing": "STATIC",
                    },
                    {"text": request.handoff.model_dump_json()},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": request.output.model_json_schema(),
            "mediaResolution": settings.GEMINI_MEDIA_RESOLUTION,
            "thinkingConfig": {"thinkingLevel": settings.GEMINI_THINKING_LEVEL},
        },
    }


def _reply[Out: BaseModel](request: VideoRequest[Out], said: dict[str, Any]) -> ModelReply[Out]:
    """Gemini's answer in the output's shape, or why it can't be had."""
    usage = said.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or 0)
    output_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
        usage.get("thoughtsTokenCount") or 0
    )

    def unusable(why: str) -> UnusableReply:
        return UnusableReply(
            f"{request.model}'s answer for {request.purpose} can't be used: {why}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    blocked = (said.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise BlockedBySafetyFilter(
            f"Gemini's safety filter refused what {request.purpose} sent it ({blocked})"
        )
    candidates = said.get("candidates") or []
    if not candidates:
        raise unusable("it gave no answer")
    finish = candidates[0].get("finishReason")
    if finish in _SAFETY_FINISHES:
        raise BlockedBySafetyFilter(
            f"Gemini's safety filter stopped its answer for {request.purpose} ({finish})"
        )
    if finish != "STOP":
        raise unusable(f"it stopped before the end ({finish})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    try:
        output = request.output.model_validate_json(text)
    except ValueError as error:  # Includes pydantic's ValidationError for cut-off JSON.
        raise unusable(f"it could not be read: {error}") from error
    return ModelReply(output=output, input_tokens=input_tokens, output_tokens=output_tokens)


def _checked(response: httpx.Response) -> httpx.Response:
    """The response, unless it failed: for a moment, worth asking again (a rate limit, or
    Gemini down), or for good."""
    if response.status_code == 429:
        raise OutsideServiceDown(f"Gemini answered 429, too many requests: {_why(response)}")
    if response.status_code >= 500:
        raise OutsideServiceDown(f"Gemini answered {response.status_code}: {_why(response)}")
    if response.status_code >= 400:
        raise GeminiFailed(f"Gemini answered {response.status_code}: {_why(response)}")
    return response


def _why(response: httpx.Response) -> str:
    """What Gemini's error reply says, or its words as they are when it isn't one."""
    try:
        said = response.json()
    except ValueError:
        return response.text[:500]
    error = said.get("error") if isinstance(said, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return f"{error['message']} ({error.get('status', '')})"
    return response.text[:500]


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
