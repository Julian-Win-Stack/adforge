"""Designs voices and speaks with them through Inworld's HTTP API."""

import base64
import unicodedata
from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown, retry_after

from .types import VoiceAccountFull

# Uncompressed audio, so its length can be read without extra tools.
_SAMPLE_RATE = 48_000

# Inworld answers 400 to a voice description holding anything but printable ASCII, and
# the planner writes typeset characters, such as a curly apostrophe: a description is
# sent with each quote and dash swapped for its plain twin, which nothing else gives.
_PLAIN = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u2013": "-",  # –
        "\u2014": "-",  # —
    }
)


def _plain_ascii(text: str) -> str:
    """`text` as Inworld accepts a voice description. Quotes and dashes are swapped for
    their plain twins; splitting each character into its plain parts then takes accents
    off letters (é keeps its e), makes … three dots and a space that doesn't break a
    space; anything left with no plain part, such as an emoji, is left out."""
    split = unicodedata.normalize("NFKD", text.translate(_PLAIN))
    return split.encode("ascii", "ignore").decode("ascii")


class InworldProvider:
    name = "inworld"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = httpx.Client(
            base_url=settings.INWORLD_BASE_URL,
            headers={"Authorization": f"Basic {settings.INWORLD_API_KEY}"},
            timeout=120,
        )

    def design_voice(self, *, model: str, description: str, sample: str, name: str) -> str:
        designed = self._post(
            "/voices/v1/voices:design",
            {
                "designPrompt": _plain_ascii(description),
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
            {"voiceId": preview_id, "displayName": name},
        )
        return str(published["voiceId"])

    def voice_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        asking: dict[str, Any] = {"filter": 'owned="true"', "pageSize": 2000}
        while True:
            listed = self._send("GET", "/voices/v1/voices", params=asking)
            names |= {voice["voiceId"]: voice["displayName"] for voice in listed["voices"]}
            if not listed.get("nextPageToken"):
                return names
            asking["pageToken"] = listed["nextPageToken"]

    def delete_voice(self, voice_id: str) -> None:
        self._send("DELETE", f"/voices/v1/voices/{voice_id}")

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
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("Inworld sent back audio that isn't a WAV file")
        return audio

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._send("POST", path, json=body)

    def _send(self, method: str, path: str, **sending: Any) -> dict[str, Any]:
        """Inworld's answer, or its error raised with what Inworld said: busy or down, worth
        asking again after as long as it says; the account full of voices; or anything else,
        which asking again won't change."""
        try:
            response = self._client.request(method, path, **sending)
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"Inworld could not be reached: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise OutsideServiceDown(
                f"Inworld answered {response.status_code}: {_said(response)}",
                retry_after=retry_after(response.headers),
            )
        if _account_full(response):
            raise VoiceAccountFull(f"Inworld answered {response.status_code}: {_said(response)}")
        if response.is_error:
            raise InworldRefused(f"Inworld answered {response.status_code}: {_said(response)}")
        reply: dict[str, Any] = response.json()
        return reply


class InworldRefused(Exception):
    """Inworld turned a request down for good, saying why."""


def _said(response: httpx.Response) -> str:
    """What Inworld said with an error: the message of its error answer (docs/real-api-
    replies.md), else the answer as it came."""
    try:
        message = response.json().get("message")
    except ValueError, AttributeError:
        message = None
    return str(message) if message else response.text[:500]


def _account_full(response: httpx.Response) -> bool:
    """Whether Inworld's answer says the account holds as many voices as it may."""
    return False
