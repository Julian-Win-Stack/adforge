"""Makes B-roll clips through Boreal-H3, on Creatify's own API.

From Creatify's API page (Create a Boreal task) and the scene #16 check, whose replies are
in docs/runs/second-run-review/check/ (docs/broll-picture-logic.md, "What Boreal-H3 takes"):
`POST /api/boreal/` asks for a clip and hands back its task `id`; `GET /api/boreal/{id}/`
says how it is getting on, `done` with a `video_output` link that only works for a while,
`failed` or `rejected` with a `failed_reason`, and anything else until then. A clip is 5 to
15 whole seconds at 0.4 credits a second at 768p. It is made from a starting picture, its
exact first frame (`image_url`), or from up to 9 example pictures (`reference_image_urls`),
the first 5 free; mixing the two is refused. With a starting picture the video takes its
shape, and Boreal-H3 takes no other; with example pictures it is set with `aspect_ratio`.

Pictures are sent as links, so each is put on fal's storage first. As fal's own Python
client uses it and as it replied on 2026-09-27 (docs/real-api-replies.md):
`POST /storage/auth/token?storage_type=fal-cdn-v3` hands our key a token and the `base_url`
to store files at; `POST {base_url}/files/upload` with the token, the file and its name
hands back the `access_url` it can be fetched from."""

import io
from collections.abc import Sequence
from typing import Any

import httpx
import PIL.Image
from django.conf import settings

from adforge.retry import OutsideServiceDown

from .types import ClipFailed, ClipStatus

MODEL = "boreal-h3"
# Creatify's statuses for a task it has finished with.
_FAILED = ("failed", "rejected")


class CreatifyProvider:
    name = "creatify"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.CREATIFY_BASE_URL,
            headers={
                "X-API-ID": settings.CREATIFY_API_ID,
                "X-API-KEY": settings.CREATIFY_API_KEY,
            },
            timeout=settings.CREATIFY_TIMEOUT_SECONDS,
        )
        self._fal = httpx.Client(
            headers={"Authorization": f"Key {settings.FAL_KEY}"},
            timeout=settings.FAL_TIMEOUT_SECONDS,
        )

    def submit_broll(
        self,
        *,
        starting_picture: bytes | None,
        example_pictures: Sequence[bytes],
        seconds: int,
        prompt: str,
    ) -> str:
        body: dict[str, Any] = {
            "model": MODEL,
            "resolution": "768p",
            "duration": seconds,
            "prompt": prompt,
            "prompt_enhancement": settings.CREATIFY_PROMPT_ENHANCEMENT,
        }
        if starting_picture is not None:
            body["image_url"] = self._store(starting_picture, "starting-picture")
        else:
            # Any picture's shape works: the video's is set here (shape check, 2026-10-03).
            body["aspect_ratio"] = "9:16"
            body["reference_image_urls"] = [
                self._store(picture, f"example-picture-{number}")
                for number, picture in enumerate(example_pictures, start=1)
            ]
        created = self._send("POST", "/api/boreal/", paid=True, json=body)
        return str(created["id"])

    def status(self, *, video_id: str) -> ClipStatus:
        try:
            task = self._send("GET", f"/api/boreal/{video_id}/")
        except httpx.HTTPStatusError as error:
            # Only Creatify saying so: a 404 without its own reply says nothing about the clip.
            why = _detail(error.response)
            if error.response.status_code != 404 or why is None:
                raise
            return ClipStatus(state="failed", error=f"Creatify no longer knows of this clip: {why}")
        if task["status"] == "done":
            return ClipStatus(state="completed", video_url=task["video_output"])
        if task["status"] in _FAILED:
            why = task.get("failed_reason") or "Creatify gave no reason"
            return ClipStatus(state="failed", error=str(why))
        return ClipStatus(state="working")

    def download(self, *, url: str) -> bytes:
        return _unkeyed("GET", url, "Creatify's clip link", follow_redirects=True).content

    def _store(self, picture: bytes, name: str) -> str:
        """Put a picture in fal's storage, named `name` and its format. Gives the link to it."""
        extension = _extension(picture)
        try:
            token = self._fal.post(
                f"{settings.FAL_STORAGE_URL}/storage/auth/token",
                params={"storage_type": "fal-cdn-v3"},
                json={},
            )
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"fal's storage could not be reached: {error}") from error
        given = _checked(token, "fal's storage").json()
        stored = _unkeyed(
            "POST",
            f"{given['base_url']}/files/upload",
            "fal's storage",
            content=picture,
            headers={
                "Authorization": f"{given['token_type']} {given['token']}",
                "Content-Type": f"image/{extension}",
                "X-Fal-File-Name": f"{name}.{extension}",
            },
        )
        return str(stored.json()["access_url"])

    def _send(
        self, method: str, path: str, *, paid: bool = False, **sending: Any
    ) -> dict[str, Any]:
        """Send a request to Creatify, with the API keys. A `paid` one is charged for once
        Creatify has it, so it is only asked again when it surely never got there."""
        try:
            response = self._client.request(method, path, **sending)
        except _NEVER_SENT as error:
            raise OutsideServiceDown(f"Creatify could not be reached: {error}") from error
        except httpx.TransportError as error:
            if paid:
                raise ClipFailed(
                    f"Creatify's reply was lost after the clip was asked for ({error}), so it "
                    "may still be made and charged for. It isn't asked for again, which could "
                    "pay twice"
                ) from error
            raise OutsideServiceDown(f"Creatify's reply was lost: {error}") from error
        if paid and 400 <= response.status_code < 500 and response.status_code != 429:
            # Refused: nothing was made, and asked again it would only be refused again.
            raise ClipFailed(f"Creatify refused it ({response.status_code}): {response.text}")
        if paid and response.status_code >= 500 and response.status_code != 503:
            # It got there and went wrong: it may still be made, and charged for. Too busy (503)
            # means it wasn't taken, so that one is asked again.
            raise ClipFailed(
                f"Creatify answered {response.status_code} after the clip was asked for, so it "
                "may still be made and charged for. It isn't asked for again, which could pay "
                "twice"
            )
        reply: dict[str, Any] = _checked(response, "Creatify").json()
        return reply


def _unkeyed(method: str, url: str, service: str, **sending: Any) -> httpx.Response:
    """Send a request somewhere other than Creatify's API or fal's, such as a link either
    gave, so it isn't sent their keys."""
    try:
        response = httpx.request(method, url, timeout=300, **sending)
    except httpx.TransportError as error:
        raise OutsideServiceDown(f"{service} could not be reached: {error}") from error
    return _checked(response, service)


def _checked(response: httpx.Response, service: str) -> httpx.Response:
    """The response, unless it failed: for a moment, worth asking again, or for good."""
    if response.status_code == 429 or response.status_code >= 500:
        raise OutsideServiceDown(f"{service} answered {response.status_code}")
    response.raise_for_status()
    return response


# Failures before the request left this machine: Creatify never had it.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _detail(response: httpx.Response) -> str | None:
    """Why Creatify's reply says it failed, or None if it isn't Creatify's own reply."""
    try:
        said = response.json()
    except ValueError:
        return None
    detail = said.get("detail") if isinstance(said, dict) else None
    return None if detail is None else str(detail)


def _extension(picture: bytes) -> str:
    """png or jpeg: what the picture's bytes hold."""
    with PIL.Image.open(io.BytesIO(picture)) as opened:
        return (opened.format or "png").lower()
