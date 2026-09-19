"""Designs voices and speaks with them through Inworld's HTTP API."""

import base64
import io
import wave
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
        preview = (designed.get("previewVoices") or designed.get("voicePreviews") or [{}])[0]
        preview_id = preview.get("voiceId") or preview.get("previewId")
        if not preview_id:
            raise ValueError("Inworld designed no voice")
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
        return audio if audio.startswith(b"RIFF") else _as_wav(audio)

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


def _as_wav(samples: bytes) -> bytes:
    """Wrap bare 16-bit mono samples in a WAV header."""
    audio = io.BytesIO()
    with wave.open(audio, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(_SAMPLE_RATE)
        file.writeframes(samples)
    return audio.getvalue()
