"""Writes down what was said in audio through ElevenLabs' HTTP API."""

import io
import wave
from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown

from .types import Transcription, Word


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.ELEVENLABS_BASE_URL,
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            timeout=120,
        )

    def transcribe(self, *, model: str, audio: bytes) -> Transcription:
        try:
            response = self._client.post(
                "/v1/speech-to-text",
                data={
                    "model_id": model,
                    "timestamps_granularity": "word",
                    # Exactly as spoken: nothing tidied away, and no sounds written as words.
                    "tag_audio_events": "false",
                    "no_verbatim": "false",
                },
                files={"file": ("line.wav", audio, "audio/wav")},
            )
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"ElevenLabs could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(f"ElevenLabs answered {response.status_code}")
        response.raise_for_status()
        heard: dict[str, Any] = response.json()
        # The spaces between words, and any sounds, come as entries of their own.
        words = tuple(
            Word(text=entry["text"], start=float(entry["start"]), end=float(entry["end"]))
            for entry in heard.get("words") or []
            if entry.get("type") == "word"
        )
        if not words or not heard.get("text"):
            raise ValueError("ElevenLabs heard no words in the audio")
        # What ElevenLabs bills by. Measured from the audio if the reply doesn't say, so
        # a call already paid for is still kept.
        seconds = heard.get("audio_duration_secs")
        return Transcription(
            text=heard["text"],
            words=words,
            audio_seconds=float(seconds) if seconds is not None else _seconds(audio),
        )


def _seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as audio:
        return float(audio.getnframes() / audio.getframerate())
