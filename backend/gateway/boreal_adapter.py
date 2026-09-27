"""Makes clips through Boreal, Creatify's video model, on fal's queue API.

From fal's page for `creatify/boreal` (read on 2026-09-26): one endpoint does both kinds of
clip. Given `audio_url`, the picture is animated to say it and the audio is kept as given;
without, the clip moves as `prompt` says. `duration` is 1 to 20 seconds and may be a
fraction: it is rounded to a whole frame at 24 frames a second. With audio, only its first
`duration` seconds are used, and shorter audio is padded with silence. 720p costs $0.01 a
second. The picture is sent inside the request, as a data URI. The audio is put in fal's storage
first and sent as a link: fal names a file sent inside a request from its content type, it
names `audio/wav` `.bin`, and Boreal refuses audio that isn't named as audio.

fal's storage, as its own Python client uses it (read on 2026-09-27): `POST
/storage/upload/initiate?storage_type=gcs` with the file's name and content type hands back an
`upload_url` to `PUT` the file at, and the `file_url` it can then be fetched from.

The queue: `POST /{model}` hands back a `request_id`; `GET .../requests/{id}/status` says
IN_QUEUE, IN_PROGRESS or COMPLETED; `GET .../requests/{id}` then gives the clip, or why it
couldn't be made."""

import base64
import io
from typing import Any

import httpx
import PIL.Image
from django.conf import settings

from adforge.retry import OutsideServiceDown

from .types import ClipFailed, ClipStatus

MODEL = "creatify/boreal"


class BorealProvider:
    name = "fal"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.FAL_QUEUE_URL,
            headers={"Authorization": f"Key {settings.FAL_KEY}"},
            timeout=settings.FAL_TIMEOUT_SECONDS,
        )

    def submit(
        self, *, picture: bytes, audio: bytes | None, seconds: float, motion_prompt: str
    ) -> str:
        body: dict[str, Any] = {
            "prompt": _prompt(motion_prompt, speaks=audio is not None),
            "image_url": _data_uri(picture, f"image/{_extension(picture)}"),
            "duration": seconds,
            "resolution": "720p",
            "aspect_ratio": "9:16",
        }
        if audio is not None:
            body["audio_url"] = self._store(audio, file_name="line.wav", content_type="audio/wav")
        created = self._send("POST", f"/{MODEL}", paid=True, json=body)
        return str(created["request_id"])

    def status(self, *, video_id: str) -> ClipStatus:
        try:
            asked = self._send("GET", f"/{MODEL}/requests/{video_id}/status")
        except httpx.HTTPStatusError as error:
            # Only fal saying so: a 404 without its own reply says nothing about the clip.
            why = _detail(error.response)
            if error.response.status_code != 404 or why is None:
                raise
            return ClipStatus(state="failed", error=f"fal no longer knows of this clip: {why}")
        if asked["status"] != "COMPLETED":
            return ClipStatus(state="working")
        if asked.get("error"):
            return ClipStatus(state="failed", error=str(asked["error"]))
        try:
            made = self._send("GET", f"/{MODEL}/requests/{video_id}")
        except httpx.HTTPStatusError as error:
            # Finished without a clip: fal says why in the result.
            why = _detail(error.response)
            if why is None:
                raise
            return ClipStatus(state="failed", error=why)
        return ClipStatus(state="completed", video_url=made["video"]["url"])

    def download(self, *, url: str) -> bytes:
        # Somewhere other than fal's API, so it isn't sent the API key.
        try:
            response = httpx.get(url, timeout=300, follow_redirects=True)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"fal's clip could not be fetched: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"fal's clip link answered {response.status_code}")
        response.raise_for_status()
        return response.content

    def _store(self, data: bytes, *, file_name: str, content_type: str) -> str:
        """Put a file in fal's storage, for a request to link to. Gives the link."""
        where = self._send(
            "POST",
            f"{settings.FAL_STORAGE_URL}/storage/upload/initiate",
            params={"storage_type": "gcs"},
            json={"file_name": file_name, "content_type": content_type},
        )
        # Somewhere other than fal's API, so it isn't sent the API key.
        try:
            response = httpx.put(
                where["upload_url"],
                content=data,
                headers={"Content-Type": content_type},
                timeout=settings.FAL_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"fal's storage could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"fal's storage answered {response.status_code}")
        response.raise_for_status()
        return str(where["file_url"])

    def _send(
        self, method: str, path: str, *, paid: bool = False, **sending: Any
    ) -> dict[str, Any]:
        """Send a request to fal. A `paid` one is charged for once fal has it, so it is only
        asked again when it surely never got there."""
        try:
            response = self._client.request(method, path, **sending)
        except _NEVER_SENT as error:
            raise OutsideServiceDown(f"fal could not be reached: {error}") from error
        except httpx.TransportError as error:
            if paid:
                raise ClipFailed(
                    f"fal's reply was lost after the clip was asked for ({error}), so it may "
                    "still be made and charged for. It isn't asked for again, which could pay "
                    "twice"
                ) from error
            raise OutsideServiceDown(f"fal's reply was lost: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"fal answered {response.status_code}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply


# Failures before the request left this machine: fal never had it.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _prompt(motion_prompt: str, *, speaks: bool) -> str:
    """Boreal's prompt, in the sections its page asks for. A talking clip says the audio it
    is given; a B-roll clip has no speech or sound, since the voice is laid over it later."""
    speech = "Only the given audio, unchanged." if speaks else "None. Nobody speaks."
    return f"[VISUAL]\n{motion_prompt}\n\n[SPEECH]\n{speech}\n\n[SOUNDS]\nNone.\n\n[TEXT]\nNone."


def _data_uri(data: bytes, content_type: str) -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode()}"


def _detail(response: httpx.Response) -> str | None:
    """Why fal's reply says it failed, or None if it isn't fal's own reply."""
    try:
        said = response.json()
    except ValueError:
        return None
    detail = said.get("detail") if isinstance(said, dict) else None
    if detail is None:
        return None
    return detail if isinstance(detail, str) else str(detail)


def _extension(picture: bytes) -> str:
    """png or jpeg: what the starting picture's bytes hold."""
    with PIL.Image.open(io.BytesIO(picture)) as opened:
        return (opened.format or "png").lower()
