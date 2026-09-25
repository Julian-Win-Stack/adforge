"""Makes background music through fal's HTTP API."""

from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown


class FalProvider:
    name = "fal"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.FAL_BASE_URL,
            headers={"Authorization": f"Key {settings.FAL_KEY}"},
            timeout=settings.FAL_TIMEOUT_SECONDS,
        )

    def compose(self, *, model: str, prompt: str, seconds: int) -> bytes:
        # fal.run answers once the music is made, with a link to it.
        made = self._post(f"/{model}", {"prompt": prompt, "duration": seconds, "num_samples": 1})
        return _download(made["audio"]["url"])

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=body)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"fal could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"fal answered {response.status_code}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply


def _download(url: str) -> bytes:
    # Somewhere other than fal's API, so it isn't sent the API key.
    try:
        response = httpx.get(url, timeout=settings.FAL_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.TransportError as error:
        raise OutsideServiceDown(f"fal's music could not be fetched: {error}") from error
    if response.status_code == 429 or response.status_code >= 500:
        raise OutsideServiceDown(f"fal's music link answered {response.status_code}")
    response.raise_for_status()
    return response.content
