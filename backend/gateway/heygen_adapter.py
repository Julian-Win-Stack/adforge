"""Makes talking clips through HeyGen's HTTP API: Avatar IV, its default engine.

As HeyGen replied on 2026-09-24 and 2026-09-25 (docs/real-api-replies.md), and as its docs
at developers.heygen.com said on 2026-09-29: the starting picture and the line's audio are
each uploaded first (`POST /v3/assets`, a multipart `file`), and the clip is asked for by
their asset ids (`POST /v3/videos`, type `image`). `GET /v3/videos/{id}` says how it is
getting on: `completed` with a `video_url` that only works for a while, `failed` with a
`failure_message`, and anything else, such as `waiting` or `processing`, until then.

The clip is as long as the audio, whatever it is asked for, so `seconds` isn't sent. Its docs
say `motion_prompt` and `expressiveness` may only apply to a photo avatar, a step this skips:
the plain request, exactly as sent here, passed all 8 of the eval's talking clips (#87)."""

import io
from typing import Any

import httpx
import PIL.Image
from django.conf import settings

from adforge.retry import OutsideServiceDown

from .types import ClipFailed, ClipStatus


class HeyGenProvider:
    name = "heygen"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.HEYGEN_BASE_URL,
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            timeout=settings.HEYGEN_TIMEOUT_SECONDS,
        )

    def submit(
        self, *, picture: bytes, audio: bytes | None, seconds: float, motion_prompt: str
    ) -> str:
        if audio is None:
            raise ValueError("HeyGen only makes talking clips: a silent one is B-roll's")
        image_id = self._upload(f"starting_picture.{_extension(picture)}", picture)
        audio_id = self._upload("line.wav", audio, "audio/wav")
        created = self._send(
            "POST",
            "/v3/videos",
            paid=True,
            json={
                "type": "image",
                "image": {"type": "asset_id", "asset_id": image_id},
                "audio_asset_id": audio_id,
                "motion_prompt": motion_prompt,
                "expressiveness": "low",
                "aspect_ratio": "9:16",
                "resolution": "1080p",
                "title": "AdForge clip",
            },
        )
        return str(created["data"]["video_id"])

    def status(self, *, video_id: str) -> ClipStatus:
        try:
            made = self._send("GET", f"/v3/videos/{video_id}")["data"]
        except httpx.HTTPStatusError as error:
            # Only HeyGen saying so: a 404 for any other reason says nothing about the clip.
            # It answered `video_not_found` on 2026-09-25; its docs say `not_found` now.
            said = _error(error.response)
            if said.get("code") not in ("video_not_found", "not_found"):
                raise
            why = said.get("message")
            return ClipStatus(state="failed", error=f"HeyGen no longer knows of this clip: {why}")
        if made["status"] == "completed":
            return ClipStatus(state="completed", video_url=made["video_url"])
        if made["status"] == "failed":
            why = made.get("failure_message") or made.get("error") or "HeyGen gave no reason"
            return ClipStatus(state="failed", error=str(why))
        return ClipStatus(state="working")

    def download(self, *, url: str) -> bytes:
        # Somewhere other than HeyGen's API, so it isn't sent the API key.
        try:
            response = httpx.get(url, timeout=300, follow_redirects=True)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"HeyGen's clip could not be fetched: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"HeyGen's clip link answered {response.status_code}")
        response.raise_for_status()
        return response.content

    def _upload(self, name: str, data: bytes, content_type: str = "") -> str:
        content_type = content_type or f"image/{_extension(data)}"
        uploaded = self._send("POST", "/v3/assets", files={"file": (name, data, content_type)})
        return str(uploaded["data"]["asset_id"])

    def _send(
        self, method: str, path: str, *, paid: bool = False, **sending: Any
    ) -> dict[str, Any]:
        """Send a request to HeyGen. A `paid` one is charged for once HeyGen has it, so it
        is only asked again when it surely never got there."""
        try:
            response = self._client.request(method, path, **sending)
        except _NEVER_SENT as error:
            raise OutsideServiceDown(f"HeyGen could not be reached: {error}") from error
        except httpx.TransportError as error:
            if paid:
                raise ClipFailed(
                    f"HeyGen's reply was lost after the clip was asked for ({error}), so it may "
                    "still be made and charged for. It isn't asked for again, which could pay "
                    "twice"
                ) from error
            raise OutsideServiceDown(f"HeyGen's reply was lost: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"HeyGen answered {response.status_code}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply


# Failures before the request left this machine: HeyGen never had it.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _error(response: httpx.Response) -> dict[str, Any]:
    """What HeyGen's reply says went wrong, or nothing if it isn't HeyGen's own reply."""
    try:
        said = response.json()
    except ValueError:
        return {}
    error = said.get("error") if isinstance(said, dict) else None
    return error if isinstance(error, dict) else {}


def _extension(picture: bytes) -> str:
    """png or jpeg: what the starting picture's bytes hold, which HeyGen is told."""
    with PIL.Image.open(io.BytesIO(picture)) as opened:
        return (opened.format or "png").lower()
