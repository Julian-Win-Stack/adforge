"""Makes talking clips from a picture and audio through HeyGen's HTTP API (Avatar IV)."""

import io
from typing import Any

import httpx
import PIL.Image
from django.conf import settings

from adforge.retry import OutsideServiceDown

from .types import ClipStatus


class HeyGenProvider:
    name = "heygen"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.HEYGEN_BASE_URL,
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            timeout=120,
        )

    def submit(self, *, picture: bytes, audio: bytes, motion_prompt: str) -> str:
        image_id = self._upload(f"starting_picture.{_extension(picture)}", picture)
        audio_id = self._upload("line.wav", audio, "audio/wav")
        created = self._send(
            "POST",
            "/v3/videos",
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
        # Waiting, pending or processing all mean it isn't made yet.
        made = self._send("GET", f"/v3/videos/{video_id}")["data"]
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

    def _send(self, method: str, path: str, **sending: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **sending)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"HeyGen could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"HeyGen answered {response.status_code}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply


def _extension(picture: bytes) -> str:
    """png or jpeg: what the starting picture's bytes hold, which HeyGen is told."""
    with PIL.Image.open(io.BytesIO(picture)) as opened:
        return (opened.format or "png").lower()
