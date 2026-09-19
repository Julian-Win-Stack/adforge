"""Designs voices and speaks with them through Inworld's HTTP API."""

import base64
from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown

# Uncompressed audio, so its length can be read without extra tools.
_SAMPLE_RATE = 48_000


class InworldProvider:
    name = "inworld"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.INWORLD_BASE_URL,
            headers={"Authorization": f"Basic {settings.INWORLD_API_KEY}"},
            timeout=120,
        )

    def design_voice(self, *, model: str, description: str, sample: str) -> str:
        designed = self._post(
            "/voices/v1/voices:design",
            {
                "designPrompt": description,
                "langCode": "EN_US",
                "previewText": sample,
                "voiceDesignConfig": {"numberOfSamples": 1},
            },
        )
        # The reply's shape is recorded in docs/real-api-replies.md.
        previews = designed.get("previewVoices") or []
        if not previews:
            raise ValueError("Inworld designed no voice")
        preview_id = previews[0]["voiceId"]
        # A designed voice can only speak once it is published.
        published = self._post(
            f"/voices/v1/voices/{preview_id}:publish",
            {"voiceId": preview_id, "displayName": "AdForge presenter"},
        )
        return str(published["voiceId"])

    def speak(self, *, model: str, voice_id: str, text: str) -> bytes:
        spoken = self._post(
            "/tts/v1/voice",
            {
                "text": text,
                "voiceId": voice_id,
                "modelId": model,
                "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": _SAMPLE_RATE},
            },
        )
        audio = base64.b64decode(spoken["audioContent"])
        # LINEAR16 comes back as a whole WAV file; its length is measured from the header.
        if not audio.startswith(b"RIFF"):
            raise ValueError("Inworld sent back audio that isn't a WAV file")
        return audio

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=body)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"Inworld could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"Inworld answered {response.status_code}")
        response.raise_for_status()
        reply: dict[str, Any] = response.json()
        return reply
