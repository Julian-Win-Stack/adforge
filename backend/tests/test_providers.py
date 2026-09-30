"""Our real picture and voice code, talking to stand-in services on this machine that reply
the way the real ones did on 2026-09-19 (docs/real-api-replies.md)."""

import base64
import io
import json
import socket
import time
import wave
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway import gateway
from gateway.boreal_adapter import BorealProvider
from gateway.elevenlabs_adapter import ElevenLabsProvider
from gateway.fal_adapter import FalProvider
from gateway.gateway import (
    collect_clip,
    design_voice,
    draw_picture,
    edit_picture,
    make_music,
    speak,
    submit_clip,
    transcribe,
    use_model,
)
from gateway.heygen_adapter import HeyGenProvider
from gateway.inworld_adapter import InworldProvider
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider
from gateway.types import ClipFailed, Word

from .conftest import picture

pytestmark = pytest.mark.django_db

VOICE_ID = "starry-sparrow-8090__design-voice-ec5cf8c1"


def wav(seconds: float) -> bytes:
    """Silence as Inworld sends LINEAR16 speech: a whole WAV file, 48 kHz, mono, 16-bit."""
    audio = io.BytesIO()
    with wave.open(audio, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(48_000)
        file.writeframes(b"\0\0" * round(seconds * 48_000))
    return audio.getvalue()


@pytest.fixture
def inworld(httpserver: HTTPServer, settings: Settings) -> InworldProvider:
    settings.INWORLD_API_KEY = "inworld-test-key"
    settings.INWORLD_BASE_URL = httpserver.url_for("")
    return InworldProvider()


def test_a_voice_is_designed_published_and_heard_through_inworld(
    httpserver: HTTPServer, inworld: InworldProvider
) -> None:
    authorised = {"Authorization": "Basic inworld-test-key"}
    httpserver.expect_oneshot_request(
        "/voices/v1/voices:design",
        method="POST",
        headers=authorised,
        json={
            "designPrompt": "A warm, relaxed woman in her thirties with a soft British accent.",
            "langCode": "EN_US",
            "previewText": "Yours for $24.00.",
            "voiceDesignConfig": {"numberOfSamples": 1},
        },
    ).respond_with_json(
        {
            "langCode": "EN_US",
            "previewVoices": [
                {
                    "voiceId": VOICE_ID,
                    "previewText": "Yours for $24.00.",
                    "previewAudio": base64.b64encode(wav(1.5)).decode(),
                }
            ],
        }
    )
    httpserver.expect_oneshot_request(
        f"/voices/v1/voices/{VOICE_ID}:publish",
        method="POST",
        headers=authorised,
        json={"voiceId": VOICE_ID, "displayName": "AdForge presenter"},
    ).respond_with_json(
        {
            "name": "workspaces/starry-sparrow-8090/voices/design-voice-ec5cf8c1",
            "langCode": "EN_US",
            "displayName": "AdForge presenter",
            "voiceId": VOICE_ID,
            "source": "TVD",
        }
    )
    speech = wav(5.4)
    httpserver.expect_oneshot_request(
        "/tts/v1/voice",
        method="POST",
        headers=authorised,
        json={
            "text": "Hand-thrown, holds 350 ml, and dishwasher safe.",
            "voiceId": VOICE_ID,
            "modelId": "inworld-tts-2",
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48_000},
        },
    ).respond_with_json(
        {
            "audioContent": base64.b64encode(speech).decode(),
            "usage": {"processedCharactersCount": 47, "modelId": "inworld-tts-2"},
        }
    )

    with use_model(inworld):
        voice_id = design_voice(
            job=None,
            purpose="design_voice",
            description="A warm, relaxed woman in her thirties with a soft British accent.",
            sample="Yours for $24.00.",
        )
        key = speak(
            job=None,
            purpose="measure_voice",
            voice_id=voice_id,
            text="Hand-thrown, holds 350 ml, and dishwasher safe.",
        )

    assert voice_id == VOICE_ID
    assert file_store.read(key) == speech
    spoken = ModelCall.objects.get(purpose="measure_voice")
    # Inworld billed the 47 characters it said, at $25 a million.
    assert (spoken.provider, spoken.characters, spoken.cost_usd) == (
        "inworld",
        47,
        Decimal("0.001175"),
    )


def test_a_voice_description_is_sent_to_inworld_in_plain_ascii(
    httpserver: HTTPServer, inworld: InworldProvider
) -> None:
    # Inworld refuses any other character in a description with a 400, and the planner
    # writes typeset ones: a curly apostrophe stopped an ad in the first run (#01 redo).
    sent: list[dict[str, Any]] = []

    def design(request: Request) -> Response:
        sent.append(request.get_json())
        return Response(json.dumps({"previewVoices": [{"voiceId": VOICE_ID}]}))

    httpserver.expect_oneshot_request(
        "/voices/v1/voices:design", method="POST"
    ).respond_with_handler(design)
    httpserver.expect_oneshot_request(
        f"/voices/v1/voices/{VOICE_ID}:publish", method="POST"
    ).respond_with_json({"voiceId": VOICE_ID})

    with use_model(inworld):
        voice_id = design_voice(
            job=None,
            purpose="design_voice",
            description="A clear, warm woman’s voice — like a café owner’s, “bright”, "
            "‘lively’ and calm, a Spanish señora – 30s to 40s…🙂",
            sample="Yours for $24.00.",
        )

    assert voice_id == VOICE_ID
    (designed,) = sent
    assert designed["designPrompt"] == (
        "A clear, warm woman's voice - like a cafe owner's, \"bright\", 'lively' and calm, "
        "a Spanish senora - 30s to 40s..."
    )


@pytest.mark.parametrize(
    "speech",
    [
        pytest.param(b"ID3 an mp3 file", id="mp3"),
        # RIFF holds other kinds of file too: a WAV file says WAVE after its size.
        pytest.param(b"RIFF\x24\0\0\0AVI an avi file", id="another RIFF file"),
    ],
)
def test_speech_that_isnt_a_wav_file_is_refused(
    httpserver: HTTPServer, inworld: InworldProvider, speech: bytes
) -> None:
    httpserver.expect_oneshot_request("/tts/v1/voice", method="POST").respond_with_json(
        {"audioContent": base64.b64encode(speech).decode()}
    )

    with use_model(inworld), pytest.raises(ValueError, match="isn't a WAV file"):
        speak(job=None, purpose="measure_voice", voice_id=VOICE_ID, text="Yours for $24.00.")
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_a_picture_is_made_from_other_pictures_through_openai(
    httpserver: HTTPServer, settings: Settings
) -> None:
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")
    portrait = file_store.save("portrait.png", picture(720, 1280, (180, 150, 120)))
    # As big as a phone takes them: the picture model is sent the whole photo, not a copy
    # shrunk for looking at.
    photo_content = picture(1600, 1200, (143, 170, 140), format="JPEG")
    photo = file_store.save("photo.jpg", photo_content)
    made = picture(1152, 2048, (200, 180, 160))
    sent: list[Request] = []

    def reply(request: Request) -> Response:
        sent.append(request)
        return Response(
            json.dumps(
                {
                    "created": 1_789_849_905,
                    "data": [{"b64_json": base64.b64encode(made).decode()}],
                    "usage": {
                        "input_tokens": 1_060,
                        "input_tokens_details": {"image_tokens": 1_000, "text_tokens": 60},
                        "output_tokens": 2_000,
                        "output_tokens_details": {"image_tokens": 2_000, "text_tokens": 0},
                        "total_tokens": 3_060,
                    },
                }
            ),
            content_type="application/json",
        )

    httpserver.expect_oneshot_request("/v1/images/edits", method="POST").respond_with_handler(reply)

    with use_model(OpenAIProvider()):
        key = edit_picture(
            job=None,
            purpose="make_starting_picture",
            prompt="She holds the mug up beside her face.",
            pictures=[portrait, photo],
        )

    (request,) = sent
    assert request.form.to_dict() == {
        "model": "gpt-image-2.5-sunburst",
        "prompt": "She holds the mug up beside her face.",
        "size": "1152x2048",
        "quality": "high",
    }
    assert [file.read() for file in request.files.getlist("image[]")] == [
        file_store.read(portrait),
        photo_content,
    ]
    # OpenAI refuses a picture sent without its type.
    assert [file.content_type for file in request.files.getlist("image[]")] == [
        "image/png",
        "image/jpeg",
    ]
    assert file_store.read(key) == made
    edited = ModelCall.objects.get()
    assert edited.handoff == {
        "prompt": "She holds the mug up beside her face.",
        "pictures": [portrait, photo],
    }
    # 60 text tokens at $5 a million, 1,000 picture tokens in at $8 and 2,000 out at $30.
    assert (edited.input_tokens, edited.output_tokens, edited.cost_usd) == (
        1_060,
        2_000,
        Decimal("0.068300"),
    )


def test_a_portrait_is_drawn_through_openai(httpserver: HTTPServer, settings: Settings) -> None:
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")
    portrait = picture(720, 1280, (180, 150, 120))
    httpserver.expect_oneshot_request(
        "/v1/images/generations",
        method="POST",
        json={
            "model": "gpt-image-2.5-sunburst",
            "prompt": "A potter in her thirties in a linen apron.",
            "size": "720x1280",
            "quality": "high",
        },
    ).respond_with_json(
        {
            "created": 1_789_849_905,
            "background": "opaque",
            "data": [
                {
                    "b64_json": base64.b64encode(portrait).decode(),
                    "revised_prompt": None,
                    "url": None,
                }
            ],
            "output_format": "png",
            "quality": "high",
            "size": "720x1280",
            "usage": {
                "input_tokens": 42,
                "input_tokens_details": {"image_tokens": 0, "text_tokens": 42},
                "output_tokens": 947,
                "output_tokens_details": {"image_tokens": 947, "text_tokens": 0},
                "total_tokens": 989,
            },
        }
    )

    with use_model(OpenAIProvider()):
        key = draw_picture(
            job=None, purpose="draw_person", prompt="A potter in her thirties in a linen apron."
        )

    assert key.endswith(".png")
    assert file_store.read(key) == portrait
    drawn = ModelCall.objects.get()
    # 42 text tokens at $5 a million and 947 picture tokens at $30 a million.
    assert (drawn.input_tokens, drawn.output_tokens, drawn.cost_usd) == (
        42,
        947,
        Decimal("0.028620"),
    )


@pytest.fixture
def elevenlabs(httpserver: HTTPServer, settings: Settings) -> ElevenLabsProvider:
    settings.ELEVENLABS_API_KEY = "elevenlabs-test-key"
    settings.ELEVENLABS_BASE_URL = httpserver.url_for("")
    return ElevenLabsProvider()


def heard(*words: tuple[str, float, float]) -> dict[str, Any]:
    """A transcript as ElevenLabs sends it: each word, with the spaces between them as
    entries of their own."""
    entries: list[dict[str, Any]] = []
    for text, start, end in words:
        if entries:
            entries.append({"text": " ", "type": "spacing", "start": start, "end": start})
        entries.append({"text": text, "type": "word", "start": start, "end": end, "logprob": 0})
    return {
        "language_code": "eng",
        "language_probability": 0.99,
        "text": " ".join(text for text, _, _ in words),
        "words": entries,
        "audio_duration_secs": 5.4,
        "transcription_id": "tr-1",
    }


def test_audio_is_transcribed_word_by_word_through_elevenlabs(
    httpserver: HTTPServer, elevenlabs: ElevenLabsProvider
) -> None:
    speech = wav(5.4)
    key = file_store.save("speak_line.wav", speech)
    sent: list[Request] = []
    reply = heard(("Hand-thrown,", 0.1, 0.7), ("holds", 0.8, 1.1), ("350", 1.2, 1.9))
    # A sound that isn't a word is left out: only what was said is kept.
    reply["words"].insert(0, {"text": "(breath)", "type": "audio_event", "start": 0, "end": 0.1})

    def replying(request: Request) -> Response:
        sent.append(request)
        return Response(json.dumps(reply), content_type="application/json")

    httpserver.expect_oneshot_request(
        "/v1/speech-to-text", method="POST", headers={"xi-api-key": "elevenlabs-test-key"}
    ).respond_with_handler(replying)

    with use_model(elevenlabs):
        transcription = transcribe(job=None, purpose="transcribe_line", audio_key=key)

    (request,) = sent
    # Exactly as spoken: filler words, repeats and false starts are kept, not tidied away.
    assert request.form.to_dict() == {
        "model_id": "scribe_v2",
        "timestamps_granularity": "word",
        "tag_audio_events": "false",
        "no_verbatim": "false",
    }
    assert request.files["file"].read() == speech
    assert transcription.text == "Hand-thrown, holds 350"
    assert transcription.words == (
        Word(text="Hand-thrown,", start=0.1, end=0.7),
        Word(text="holds", start=0.8, end=1.1),
        Word(text="350", start=1.2, end=1.9),
    )
    heard_call = ModelCall.objects.get()
    assert heard_call.handoff == {"audio": key}
    assert heard_call.output == {
        "text": "Hand-thrown, holds 350",
        "words": [
            {"text": "Hand-thrown,", "start": 0.1, "end": 0.7},
            {"text": "holds", "start": 0.8, "end": 1.1},
            {"text": "350", "start": 1.2, "end": 1.9},
        ],
        "audio_seconds": 5.4,
    }
    # ElevenLabs billed the 5.4 seconds it heard, at $0.22 an hour.
    assert (heard_call.provider, heard_call.audio_seconds, heard_call.cost_usd) == (
        "elevenlabs",
        5.4,
        Decimal("0.000330"),
    )


def test_elevenlabs_is_tried_again_when_it_is_down_and_every_try_is_recorded(
    httpserver: HTTPServer, elevenlabs: ElevenLabsProvider
) -> None:
    key = file_store.save("speak_line.wav", wav(5.4))
    httpserver.expect_ordered_request("/v1/speech-to-text", method="POST").respond_with_data(
        "busy", status=503
    )
    # Said twice, so heard twice: the transcript is what was said, not what should have been.
    httpserver.expect_ordered_request("/v1/speech-to-text", method="POST").respond_with_json(
        heard(("the", 0.1, 0.3), ("the", 0.4, 0.6), ("mug", 0.7, 1.0))
    )

    with use_model(elevenlabs):
        transcription = transcribe(job=None, purpose="transcribe_line", audio_key=key)

    assert [word.text for word in transcription.words] == ["the", "the", "mug"]
    first, second = ModelCall.objects.all()
    assert (first.attempt, first.outcome, first.cost_usd) == (1, "failed", None)
    assert "503" in first.error
    assert (second.attempt, second.outcome, second.audio_seconds) == (2, "succeeded", 5.4)
    assert second.cost_usd == Decimal("0.000330")
    assert second.duration_ms is not None


@pytest.mark.parametrize(
    "reply",
    [
        {"language_code": "eng", "text": "", "audio_duration_secs": 1.0},
        # Only a sound was heard: there is text, but not one word.
        {
            "language_code": "eng",
            "text": "(laughs)",
            "words": [{"text": "(laughs)", "type": "audio_event", "start": 0.1, "end": 0.9}],
            "audio_duration_secs": 1.0,
        },
    ],
)
def test_a_transcript_with_no_words_is_refused(
    httpserver: HTTPServer, elevenlabs: ElevenLabsProvider, reply: dict[str, Any]
) -> None:
    key = file_store.save("speak_line.wav", wav(1))
    httpserver.expect_oneshot_request("/v1/speech-to-text", method="POST").respond_with_json(reply)

    with use_model(elevenlabs), pytest.raises(ValueError, match="no words"):
        transcribe(job=None, purpose="transcribe_line", audio_key=key)
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_audio_whose_length_elevenlabs_doesnt_say_is_measured(
    httpserver: HTTPServer, elevenlabs: ElevenLabsProvider
) -> None:
    key = file_store.save("speak_line.wav", wav(2.5))
    reply = heard(("Yours", 0.1, 0.4))
    del reply["audio_duration_secs"]
    httpserver.expect_oneshot_request("/v1/speech-to-text", method="POST").respond_with_json(reply)

    with use_model(elevenlabs):
        transcription = transcribe(job=None, purpose="transcribe_line", audio_key=key)

    # Paid for, so kept: billed by the audio's own length.
    assert transcription.audio_seconds == 2.5
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.SUCCEEDED


# --- fal -----------------------------------------------------------------------------------


@pytest.fixture
def fal(httpserver: HTTPServer, settings: Settings) -> FalProvider:
    settings.FAL_KEY = "fal-test-key"
    settings.FAL_BASE_URL = httpserver.url_for("")
    return FalProvider()


# As fal's Sonilo sends it: AAC in an MP4 file, which only needs to be kept, not read.
MUSIC = b"\0\0\0\x1cftypM4A music"


def test_music_is_made_through_fal_and_fetched_from_where_fal_keeps_it(
    httpserver: HTTPServer, fal: FalProvider
) -> None:
    httpserver.expect_oneshot_request(
        "/sonilo/v1.1/text-to-music",
        method="POST",
        headers={"Authorization": "Key fal-test-key"},
        json={"prompt": "Calm. Instrumental only.", "duration": 14, "num_samples": 1},
    ).respond_with_json(
        # The reply's shape, from fal's model page for Sonilo v1.1.
        {
            "audio": {
                "url": httpserver.url_for("/files/music.m4a"),
                "content_type": "audio/mp4",
                "file_name": "music.m4a",
                "file_size": len(MUSIC),
            },
            "audios": [],
        }
    )
    # Fetched without the API key: the link is somewhere other than fal's API.
    httpserver.expect_oneshot_request("/files/music.m4a", method="GET").respond_with_data(
        MUSIC, content_type="audio/mp4"
    )

    with use_model(fal):
        key = make_music(
            job=None, purpose="make_music", prompt="Calm. Instrumental only.", seconds=14
        )

    assert file_store.read(key) == MUSIC
    assert key.endswith(".m4a")
    (made,) = ModelCall.objects.all()
    assert (made.provider, made.model, made.audio_seconds) == (
        "fal",
        "sonilo/v1.1/text-to-music",
        14,
    )
    # $0.0025 a second of music.
    assert made.cost_usd == Decimal("0.035")
    httpserver.check_assertions()


def test_fal_is_tried_again_when_it_is_down_and_every_try_is_recorded(
    httpserver: HTTPServer, fal: FalProvider
) -> None:
    httpserver.expect_ordered_request("/sonilo/v1.1/text-to-music").respond_with_data(
        "busy", status=503
    )
    httpserver.expect_ordered_request("/sonilo/v1.1/text-to-music").respond_with_json(
        {"audio": {"url": httpserver.url_for("/files/music.m4a")}}
    )
    httpserver.expect_ordered_request("/files/music.m4a").respond_with_data(MUSIC)

    with use_model(fal):
        make_music(job=None, purpose="make_music", prompt="Calm.", seconds=5)

    first, second = ModelCall.objects.all()
    assert (first.attempt, first.outcome, first.cost_usd) == (1, "failed", None)
    assert "503" in first.error
    assert (second.attempt, second.outcome) == (2, "succeeded")


def test_music_fal_cant_make_is_not_asked_for_again(
    httpserver: HTTPServer, fal: FalProvider
) -> None:
    httpserver.expect_oneshot_request("/sonilo/v1.1/text-to-music").respond_with_json(
        {"detail": "prompt is not allowed"}, status=422
    )

    with use_model(fal), pytest.raises(httpx.HTTPStatusError):
        make_music(job=None, purpose="make_music", prompt="Calm.", seconds=5)

    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


# --- Boreal on fal's queue ------------------------------------------------------------------
# Replies shaped as fal's API schema for creatify/boreal describes them (read on 2026-09-26),
# not yet recorded from a real run. fal's storage replies as it did on 2026-09-27
# (docs/real-api-replies.md).


@pytest.fixture
def boreal(httpserver: HTTPServer, settings: Settings) -> BorealProvider:
    settings.FAL_KEY = "fal-test-key"
    settings.FAL_QUEUE_URL = httpserver.url_for("")
    settings.FAL_STORAGE_URL = httpserver.url_for("")
    return BorealProvider()


AUTHORISED = {"Authorization": "Key fal-test-key"}
QUEUED = {
    "request_id": "req-1",
    "status": "IN_QUEUE",
    "status_url": "https://queue.fal.run/creatify/boreal/requests/req-1/status",
    "response_url": "https://queue.fal.run/creatify/boreal/requests/req-1",
}
# Where fal says how clip req-1 is getting on, where it says the made clip is, and the link
# to the clip itself.
STATUS = "/creatify/boreal/requests/req-1/status"
# Where fal's storage gives a token to store files with, where a file is stored with it, and
# the link it then gives the file.
TOKEN = "/storage/auth/token"
UPLOADING = "/cdn/files/upload"
STORED_LINK = "https://v3b.fal.media/files/b/0aac27e0/hzumoERxCN0OZTqbpMb2L_line.wav"
RESULT = "/creatify/boreal/requests/req-1"
CLIP_LINK = "/files/out.mp4"
MADE = b"\0\0\0\x18ftypmp42 a whole clip"


def submitting(picture_key: str = "", audio_key: str | None = "", seconds: float = 5.4) -> str:
    """Ask for a 5.4-second talking clip, of a picture and audio kept in the file store, or
    for a silent one when `audio_key` is None."""
    return submit_clip(
        job=None,
        purpose="make_broll_clip",
        picture_key=picture_key
        or file_store.save("starting_picture.png", picture(72, 128, (1, 2, 3))),
        audio_key=audio_key
        if audio_key is None
        else audio_key or file_store.save("speak_line.wav", wav(5.4)),
        seconds=seconds,
        motion_prompt="She talks to the camera.",
    )


def collecting() -> str:
    """Wait for clip req-1 to be made, then fetch it."""
    return collect_clip(job=None, purpose="collect_broll_clip", video_id="req-1")


def with_files_read_back(body: dict[str, Any]) -> dict[str, Any]:
    """A request's body with each file sent inside it as a data URI read back: what the URI
    says the file is, and the file."""
    read_back: dict[str, Any] = {}
    for name, value in body.items():
        if isinstance(value, str) and value.startswith("data:"):
            says, encoded = value.split(",", 1)
            value = (says, base64.b64decode(encoded))
        read_back[name] = value
    return read_back


@pytest.fixture
def stored(httpserver: HTTPServer) -> list[dict[str, Any]]:
    """fal's storage giving a token to our key, and taking every file stored with it, at
    STORED_LINK. Gives each file's name, what it was said to be, what it held, and what it
    was sent with."""
    files: list[dict[str, Any]] = []

    def uploading(request: Request) -> Response:
        files.append(
            {
                "name": request.headers.get("X-Fal-File-Name"),
                "type": request.content_type,
                "held": request.get_data(),
                "sent with": request.headers.get("Authorization"),
            }
        )
        return Response(
            json.dumps({"access_url": STORED_LINK, "uploaded": True}),
            content_type="application/json",
        )

    httpserver.expect_request(
        TOKEN, method="POST", query_string="storage_type=fal-cdn-v3", headers=AUTHORISED
    ).respond_with_json(
        {
            "token": "cdn-token",
            "created_at": "2026-09-27T21:46:36.102130+00:00",
            "expires_at": "2026-10-27T21:46:36.102130+00:00",
            "base_url": httpserver.url_for("/cdn"),
            "token_type": "Bearer",
        }
    )
    httpserver.expect_request(UPLOADING, method="POST").respond_with_handler(uploading)
    return files


@pytest.fixture
def asked(httpserver: HTTPServer, stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """fal taking every clip asked of it with our key, as req-1, and every file put in its
    storage. Gives what each clip asked for."""
    bodies: list[dict[str, Any]] = []

    def queueing(request: Request) -> Response:
        bodies.append(request.get_json())
        return Response(json.dumps(QUEUED), content_type="application/json")

    httpserver.expect_request(
        "/creatify/boreal", method="POST", headers=AUTHORISED
    ).respond_with_handler(queueing)
    return bodies


@pytest.fixture
def made(httpserver: HTTPServer) -> None:
    """fal having made clip req-1: it says so, says where the clip is, and hands it over."""
    httpserver.expect_request(STATUS, headers=AUTHORISED).respond_with_json(
        {"request_id": "req-1", "status": "COMPLETED"}
    )
    httpserver.expect_request(RESULT, headers=AUTHORISED).respond_with_json(
        {"video": {"url": httpserver.url_for(CLIP_LINK), "content_type": "video/mp4"}}
    )
    httpserver.expect_request(CLIP_LINK).respond_with_data(MADE)


def hanging_up(request: Request) -> Response:
    """The line going dead once the request has arrived, before it is answered."""
    request.environ["werkzeug.socket"].shutdown(socket.SHUT_RDWR)
    return Response(status=502)


def too_busy(request: Request) -> Response:
    return Response("Too Many Requests", status=429)


def broken(request: Request) -> Response:
    return Response("Internal Server Error", status=500)


def test_a_talking_clip_is_asked_for_saying_the_line_in_720p_portrait(
    boreal: BorealProvider, asked: list[dict[str, Any]], stored: list[dict[str, Any]]
) -> None:
    starting_picture = picture(1152, 2048, (200, 180, 160))
    line = wav(5.4)

    with use_model(boreal):
        submitting(
            file_store.save("starting_picture.png", starting_picture),
            file_store.save("speak_line.wav", line),
        )

    (body,) = asked
    # The picture is sent whole, inside the request; the line's audio by a link to it in fal's
    # storage.
    assert with_files_read_back(body) == {
        "prompt": "[VISUAL]\nShe talks to the camera.\n\n[SPEECH]\nOnly the given audio, "
        "unchanged.\n\n[SOUNDS]\nNone.\n\n[TEXT]\nNone.",
        "image_url": ("data:image/png;base64", starting_picture),
        "duration": 5.4,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "audio_url": STORED_LINK,
    }
    # Named as a WAV file, which Boreal takes. Sent inside the request, fal named it `.bin`
    # and Boreal refused it. It is stored with the storage's token, not our key.
    assert stored == [
        {"name": "line.wav", "type": "audio/wav", "held": line, "sent with": "Bearer cdn-token"}
    ]


def test_a_clip_with_no_audio_is_asked_for_with_no_speech_or_sound(
    boreal: BorealProvider, asked: list[dict[str, Any]], stored: list[dict[str, Any]]
) -> None:
    starting_picture = picture(72, 128, (1, 2, 3))

    with use_model(boreal):
        submitting(file_store.save("p.png", starting_picture), None, seconds=6)

    (body,) = asked
    # No audio is sent: the voice is laid over the clip afterwards.
    assert with_files_read_back(body) == {
        "prompt": "[VISUAL]\nShe talks to the camera.\n\n[SPEECH]\nNone. Nobody speaks."
        "\n\n[SOUNDS]\nNone.\n\n[TEXT]\nNone.",
        "image_url": ("data:image/png;base64", starting_picture),
        "duration": 6,
        "resolution": "720p",
        "aspect_ratio": "9:16",
    }
    assert stored == []


def test_a_clip_asked_for_is_known_by_the_id_fal_gives_it(
    boreal: BorealProvider, asked: list[dict[str, Any]]
) -> None:
    with use_model(boreal):
        video_id = submitting()

    # Recorded, so a step run again waits for this clip rather than paying for another.
    assert (video_id, ModelCall.objects.get().output) == ("req-1", {"video_id": "req-1"})


def test_a_talking_clip_is_billed_by_fal_for_every_second_of_it(
    boreal: BorealProvider, asked: list[dict[str, Any]]
) -> None:
    with use_model(boreal):
        submitting(seconds=5.4)

    submitted = ModelCall.objects.get()
    # $0.01 a second at 720p.
    assert (submitted.provider, submitted.model, submitted.video_seconds, submitted.cost_usd) == (
        "fal",
        "creatify/boreal",
        5.4,
        Decimal("0.054"),
    )


def test_a_clip_is_fetched_only_once_fal_says_it_is_made(
    httpserver: HTTPServer, boreal: BorealProvider
) -> None:
    # A clip waits in fal's queue, is worked on, then is made.
    for state in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"):
        httpserver.expect_oneshot_request(STATUS, headers=AUTHORISED).respond_with_json(
            {"request_id": "req-1", "status": state}
        )
    httpserver.expect_oneshot_request(RESULT, headers=AUTHORISED).respond_with_json(
        {"video": {"url": httpserver.url_for(CLIP_LINK), "content_type": "video/mp4"}}
    )
    httpserver.expect_oneshot_request(CLIP_LINK).respond_with_data(MADE)

    with use_model(boreal):
        key = collecting()

    assert file_store.read(key) == MADE
    assert [request.path for request, _ in httpserver.log] == [
        STATUS,
        STATUS,
        STATUS,
        RESULT,
        CLIP_LINK,
    ]


def test_a_fetched_clip_is_recorded_at_no_cost(boreal: BorealProvider, made: None) -> None:
    with use_model(boreal):
        key = collecting()

    # Paid for when it was asked for: waiting for it and fetching it cost nothing more.
    collected = ModelCall.objects.get()
    assert (collected.handoff, collected.output, collected.cost_usd) == (
        {"video_id": "req-1"},
        {"file": key},
        Decimal(0),
    )


def test_a_clip_boreal_couldnt_make_is_not_asked_for_again(
    httpserver: HTTPServer, boreal: BorealProvider
) -> None:
    httpserver.expect_oneshot_request(STATUS).respond_with_json(
        {"request_id": "req-1", "status": "COMPLETED"}
    )
    httpserver.expect_oneshot_request(RESULT).respond_with_json(
        {"detail": "The image was flagged by the content checker."}, status=422
    )

    with use_model(boreal), pytest.raises(ClipFailed, match="flagged by the content checker"):
        collecting()
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_a_clip_fal_finished_with_an_error_counts_as_one_it_couldnt_make(
    httpserver: HTTPServer, boreal: BorealProvider
) -> None:
    # fal says why as it says the clip is finished: there is no clip to fetch.
    httpserver.expect_oneshot_request(STATUS).respond_with_json(
        {"request_id": "req-1", "status": "COMPLETED", "error": "The video could not be made."}
    )

    with use_model(boreal), pytest.raises(ClipFailed, match=r"^The video could not be made\.$"):
        collecting()


def test_a_clip_fal_no_longer_knows_of_counts_as_one_it_couldnt_make(
    httpserver: HTTPServer, boreal: BorealProvider
) -> None:
    httpserver.expect_oneshot_request(STATUS).respond_with_json(
        {"detail": "Request not found"}, status=404
    )

    # Waited for, it would never be made.
    with (
        use_model(boreal),
        pytest.raises(ClipFailed, match="^fal no longer knows of this clip: Request not found$"),
    ):
        collecting()


@pytest.mark.parametrize(
    "asking", [STATUS, RESULT], ids=["how the clip is getting on", "where the clip is"]
)
@pytest.mark.parametrize(
    ("reply", "content_type"),
    [
        pytest.param("Not Found", "text/plain", id="plain text"),
        pytest.param('{"message": "Not Found"}', "application/json", id="JSON without a detail"),
    ],
)
def test_a_not_found_that_isnt_fals_own_reply_doesnt_count_as_the_clip_failing(
    httpserver: HTTPServer,
    boreal: BorealProvider,
    made: None,
    asking: str,
    reply: str,
    content_type: str,
) -> None:
    # Such as its address having moved: the clip it was asked about may still be made, and
    # counting it as failed would pay for it again.
    httpserver.expect_oneshot_request(asking).respond_with_data(
        reply, status=404, content_type=content_type
    )

    with use_model(boreal), pytest.raises(httpx.HTTPStatusError, match="404 NOT FOUND"):
        collecting()


def test_fal_refusing_to_say_how_a_clip_is_getting_on_doesnt_count_as_the_clip_failing(
    httpserver: HTTPServer, boreal: BorealProvider
) -> None:
    # Such as when the account has run out of money. The clip is already paid for: counted
    # as failed, it would be paid for again once the account is topped up.
    httpserver.expect_oneshot_request(STATUS).respond_with_json(
        {"detail": "User is locked. Reason: Exhausted balance."}, status=403
    )

    with use_model(boreal), pytest.raises(httpx.HTTPStatusError, match="403 FORBIDDEN"):
        collecting()


@pytest.mark.parametrize(
    "asking",
    [STATUS, RESULT, CLIP_LINK],
    ids=["how the clip is getting on", "where the clip is", "the clip itself"],
)
@pytest.mark.parametrize(
    "failing",
    [
        pytest.param(hanging_up, id="reply lost"),
        pytest.param(too_busy, id="too busy"),
        pytest.param(broken, id="server error"),
    ],
)
def test_a_made_clip_is_still_fetched_when_fal_fails_for_a_moment(
    httpserver: HTTPServer,
    boreal: BorealProvider,
    made: None,
    asking: str,
    failing: Callable[[Request], Response],
) -> None:
    # Asking about a clip, or fetching it, costs nothing, so it is asked again. Giving up
    # would pay for the clip a second time.
    httpserver.expect_oneshot_request(asking).respond_with_handler(failing)

    with use_model(boreal):
        key = collecting()

    assert file_store.read(key) == MADE
    assert [(c.attempt, c.outcome) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED),
        (2, ModelCall.Outcome.SUCCEEDED),
    ]


def test_a_clip_link_that_no_longer_works_isnt_kept_as_the_clip(
    httpserver: HTTPServer, boreal: BorealProvider, made: None
) -> None:
    # The link to a made clip only works for a while.
    httpserver.expect_oneshot_request(CLIP_LINK).respond_with_data(
        "Request has expired", status=403
    )

    with use_model(boreal), pytest.raises(httpx.HTTPStatusError, match="403 FORBIDDEN"):
        collecting()
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_a_clip_fal_is_too_busy_for_is_asked_for_again(
    httpserver: HTTPServer, boreal: BorealProvider, stored: list[dict[str, Any]]
) -> None:
    # Too busy to take it, so nothing was made: asking again can't pay twice.
    httpserver.expect_oneshot_request("/creatify/boreal", method="POST").respond_with_data(
        "Service Unavailable", status=503
    )
    httpserver.expect_oneshot_request("/creatify/boreal", method="POST").respond_with_json(QUEUED)

    with use_model(boreal):
        assert submitting() == "req-1"

    assert [(c.attempt, c.outcome, c.cost_usd) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED, None),
        (2, ModelCall.Outcome.SUCCEEDED, Decimal("0.054")),
    ]


@pytest.mark.parametrize(
    "asking", [TOKEN, UPLOADING], ids=["a token to store the audio", "storing the audio"]
)
@pytest.mark.parametrize(
    "failing",
    [
        pytest.param(hanging_up, id="reply lost"),
        pytest.param(too_busy, id="too busy"),
        pytest.param(broken, id="server error"),
    ],
)
def test_a_talking_clip_is_asked_for_again_when_fals_storage_fails_for_a_moment(
    httpserver: HTTPServer,
    boreal: BorealProvider,
    asked: list[dict[str, Any]],
    asking: str,
    failing: Callable[[Request], Response],
) -> None:
    # Putting the audio in storage costs nothing, and the clip isn't asked for until it's
    # there, so asking again can't pay twice.
    httpserver.expect_oneshot_request(asking).respond_with_handler(failing)

    with use_model(boreal):
        assert submitting() == "req-1"

    assert len(asked) == 1
    assert [(c.attempt, c.outcome, c.cost_usd) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED, None),
        (2, ModelCall.Outcome.SUCCEEDED, Decimal("0.054")),
    ]


def test_a_talking_clip_isnt_asked_for_while_fals_storage_cant_be_reached(
    boreal: BorealProvider, asked: list[dict[str, Any]], settings: Settings
) -> None:
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        settings.FAL_STORAGE_URL = f"http://127.0.0.1:{unused.getsockname()[1]}"
    unreachable = BorealProvider()

    # Told apart from fal's queue being down, which is a different thing to look into.
    with (
        use_model(unreachable),
        pytest.raises(
            OutsideServiceDown,
            match="still down after 3 tries: fal's storage could not be reached",
        ),
    ):
        submitting()

    # With nowhere for the audio, the clip is never asked for, so nothing is paid for.
    assert asked == []


def test_a_clip_fal_cant_be_reached_for_is_asked_for_again(settings: Settings) -> None:
    # Nothing answers where fal should be, so the request never left: nothing was paid for.
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        settings.FAL_QUEUE_URL = f"http://127.0.0.1:{unused.getsockname()[1]}"
    unreachable = BorealProvider()

    # With no audio, so nothing is put in fal's storage first.
    with (
        use_model(unreachable),
        pytest.raises(
            OutsideServiceDown, match="still down after 3 tries: fal could not be reached"
        ),
    ):
        submitting(audio_key=None)

    assert [(c.attempt, c.outcome) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED),
        (2, ModelCall.Outcome.FAILED),
        (3, ModelCall.Outcome.FAILED),
    ]


def test_a_clip_whose_reply_from_fal_is_lost_isnt_asked_for_again(
    httpserver: HTTPServer,
    boreal: BorealProvider,
    stored: list[dict[str, Any]],
    settings: Settings,
) -> None:
    settings.FAL_TIMEOUT_SECONDS = 0.2
    impatient = BorealProvider()
    taken: list[str] = []

    def answering_too_late(request: Request) -> Response:
        taken.append(request.path)
        time.sleep(0.5)
        return Response(json.dumps(QUEUED))

    httpserver.expect_request("/creatify/boreal", method="POST").respond_with_handler(
        answering_too_late
    )

    with (
        use_model(impatient),
        pytest.raises(ClipFailed, match="^fal's reply was lost after the clip was asked for"),
    ):
        submitting()

    # fal may have taken it, and charged for it: asking again could pay twice.
    assert taken == ["/creatify/boreal"]
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


# --- HeyGen Avatar IV --------------------------------------------------------------------------
# Replies as HeyGen gave them on 2026-09-24 and 2026-09-25 (docs/real-api-replies.md), with
# what its docs said on 2026-09-29: a clip's first status may be `waiting`, and the code for a
# clip it doesn't know of is now `not_found`.


@pytest.fixture
def heygen(httpserver: HTTPServer, settings: Settings) -> HeyGenProvider:
    settings.HEYGEN_API_KEY = "heygen-test-key"
    settings.HEYGEN_BASE_URL = httpserver.url_for("")
    return HeyGenProvider()


HEYGEN_AUTHORISED = {"x-api-key": "heygen-test-key"}
HEYGEN_CLIP = "/v3/videos/vid-1"


def taking_uploads(httpserver: HTTPServer) -> list[tuple[str, bytes, str]]:
    """HeyGen taking every file uploaded to it with our key. Gives each one's name, bytes and
    type."""
    uploaded: list[tuple[str, bytes, str]] = []

    def upload(request: Request) -> Response:
        file = request.files["file"]
        uploaded.append((file.filename or "", file.read(), file.content_type or ""))
        return Response(
            json.dumps({"data": {"asset_id": f"asset-{len(uploaded)}"}}),
            content_type="application/json",
        )

    httpserver.expect_request(
        "/v3/assets", method="POST", headers=HEYGEN_AUTHORISED
    ).respond_with_handler(upload)
    return uploaded


def asking_heygen(picture_key: str = "", audio_key: str = "") -> str:
    """Ask for a 5.4-second talking clip, of a picture and audio kept in the file store."""
    return submit_clip(
        job=None,
        purpose="make_talking_clip",
        picture_key=picture_key
        or file_store.save("starting_picture.png", picture(72, 128, (1, 2, 3))),
        audio_key=audio_key or file_store.save("speak_line.wav", wav(5.4)),
        seconds=5.4,
        motion_prompt="She talks to the camera.",
    )


def collecting_from_heygen() -> str:
    """Wait for clip vid-1 to be made, then fetch it."""
    return collect_clip(job=None, purpose="collect_talking_clip", video_id="vid-1")


def test_a_talking_clip_is_made_from_a_picture_and_audio_through_heygen(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    starting_picture = picture(1152, 2048, (200, 180, 160))
    line = wav(5.4)
    uploaded = taking_uploads(httpserver)
    # The request the eval's 8 passing clips were made with (#87).
    httpserver.expect_oneshot_request(
        "/v3/videos",
        method="POST",
        headers=HEYGEN_AUTHORISED,
        json={
            "type": "image",
            "image": {"type": "asset_id", "asset_id": "asset-1"},
            "audio_asset_id": "asset-2",
            "motion_prompt": "She talks to the camera.",
            "expressiveness": "low",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "title": "AdForge clip",
        },
    ).respond_with_json({"data": {"video_id": "vid-1", "status": "waiting"}})
    # Waiting, then being made, then made.
    for status in ("waiting", "processing"):
        httpserver.expect_oneshot_request(
            HEYGEN_CLIP, method="GET", headers=HEYGEN_AUTHORISED
        ).respond_with_json({"data": {"id": "vid-1", "status": status}})
    httpserver.expect_oneshot_request(
        HEYGEN_CLIP, method="GET", headers=HEYGEN_AUTHORISED
    ).respond_with_json(
        {
            "data": {
                "id": "vid-1",
                "status": "completed",
                "duration": 5.42,
                "video_url": httpserver.url_for(CLIP_LINK),
            }
        }
    )
    httpserver.expect_oneshot_request(CLIP_LINK).respond_with_data(MADE)

    with use_model(heygen):
        video_id = asking_heygen(
            file_store.save("starting_picture.png", starting_picture),
            file_store.save("speak_line.wav", line),
        )
        key = collecting_from_heygen()

    assert video_id == "vid-1"
    assert uploaded == [
        ("starting_picture.png", starting_picture, "image/png"),
        ("line.wav", line, "audio/wav"),
    ]
    assert file_store.read(key) == MADE
    # The link to the clip is somewhere other than HeyGen's API: it isn't sent our key.
    (fetching,) = [request for request, _ in httpserver.log if request.path == CLIP_LINK]
    assert "x-api-key" not in fetching.headers


def test_a_talking_clip_is_billed_by_heygen_for_every_second_of_it(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    taking_uploads(httpserver)
    httpserver.expect_request("/v3/videos", method="POST").respond_with_json(
        {"data": {"video_id": "vid-1"}}
    )

    with use_model(heygen):
        asking_heygen()

    submitted = ModelCall.objects.get()
    # $0.05 a second, at 720p and 1080p alike.
    assert (
        submitted.provider,
        submitted.model,
        submitted.video_seconds,
        submitted.cost_usd,
        submitted.output,
    ) == ("heygen", "heygen/avatar-iv", 5.4, Decimal("0.27"), {"video_id": "vid-1"})


def test_heygen_is_never_asked_for_a_clip_with_no_audio(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    # It makes talking clips only: a silent one is B-roll's, and Boreal's to make.
    with use_model(heygen), pytest.raises(ValueError, match="HeyGen only makes talking clips"):
        submit_clip(
            job=None,
            purpose="make_talking_clip",
            picture_key=file_store.save("p.png", picture(72, 128, (1, 2, 3))),
            audio_key=None,
            seconds=5,
            motion_prompt="She talks to the camera.",
        )
    assert httpserver.log == []


def test_a_clip_heygen_couldnt_make_is_not_asked_for_again(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    httpserver.expect_oneshot_request(HEYGEN_CLIP).respond_with_json(
        {"data": {"id": "vid-1", "status": "failed", "failure_message": "No face was found."}}
    )

    with use_model(heygen), pytest.raises(ClipFailed, match=r"^No face was found\.$"):
        collecting_from_heygen()
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("video_not_found", id="as HeyGen answered on 2026-09-25"),
        pytest.param("not_found", id="as its docs say on 2026-09-29"),
    ],
)
def test_a_clip_heygen_no_longer_knows_of_counts_as_one_it_couldnt_make(
    httpserver: HTTPServer, heygen: HeyGenProvider, code: str
) -> None:
    httpserver.expect_oneshot_request(HEYGEN_CLIP).respond_with_json(
        {"error": {"code": code, "message": "Video vid-1 not found"}}, status=404
    )

    # Waited for, it would never be made.
    with (
        use_model(heygen),
        pytest.raises(
            ClipFailed, match="^HeyGen no longer knows of this clip: Video vid-1 not found$"
        ),
    ):
        collecting_from_heygen()


def test_a_not_found_from_heygen_that_isnt_about_the_clip_doesnt_count_as_the_clip_failing(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    # Such as its address having moved: the clip it was asked about may still be made, and
    # counting it as failed would pay for it again.
    httpserver.expect_oneshot_request(HEYGEN_CLIP).respond_with_data("Not Found", status=404)

    with use_model(heygen), pytest.raises(httpx.HTTPStatusError, match="404 NOT FOUND"):
        collecting_from_heygen()


@pytest.mark.parametrize(
    "failing",
    [
        pytest.param(hanging_up, id="reply lost"),
        pytest.param(too_busy, id="too busy"),
        pytest.param(broken, id="server error"),
    ],
)
def test_a_made_clip_is_still_fetched_when_heygen_fails_for_a_moment(
    httpserver: HTTPServer, heygen: HeyGenProvider, failing: Callable[[Request], Response]
) -> None:
    # Asking about a clip costs nothing, so it is asked again. Giving up would pay twice.
    httpserver.expect_oneshot_request(HEYGEN_CLIP).respond_with_handler(failing)
    httpserver.expect_oneshot_request(HEYGEN_CLIP).respond_with_json(
        {"data": {"id": "vid-1", "status": "completed", "video_url": httpserver.url_for(CLIP_LINK)}}
    )
    httpserver.expect_oneshot_request(CLIP_LINK).respond_with_data(MADE)

    with use_model(heygen):
        key = collecting_from_heygen()

    assert file_store.read(key) == MADE
    assert [(c.attempt, c.outcome) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED),
        (2, ModelCall.Outcome.SUCCEEDED),
    ]


def test_a_clip_heygen_is_too_busy_for_is_asked_for_again(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    taking_uploads(httpserver)
    # Too busy to take it, so nothing was made: asking again can't pay twice.
    httpserver.expect_oneshot_request("/v3/videos", method="POST").respond_with_data(
        "Service Unavailable", status=503
    )
    httpserver.expect_oneshot_request("/v3/videos", method="POST").respond_with_json(
        {"data": {"video_id": "vid-1"}}
    )

    with use_model(heygen):
        assert asking_heygen() == "vid-1"

    assert [(c.attempt, c.outcome, c.cost_usd) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED, None),
        (2, ModelCall.Outcome.SUCCEEDED, Decimal("0.27")),
    ]


def test_a_clip_whose_reply_from_heygen_is_lost_isnt_asked_for_again(
    httpserver: HTTPServer, heygen: HeyGenProvider, settings: Settings
) -> None:
    settings.HEYGEN_TIMEOUT_SECONDS = 0.2
    impatient = HeyGenProvider()
    taking_uploads(httpserver)
    taken: list[str] = []

    def answering_too_late(request: Request) -> Response:
        taken.append(request.path)
        time.sleep(0.5)
        return Response(json.dumps({"data": {"video_id": "vid-1"}}))

    httpserver.expect_request("/v3/videos", method="POST").respond_with_handler(answering_too_late)

    with (
        use_model(impatient),
        pytest.raises(ClipFailed, match="^HeyGen's reply was lost after the clip was asked for"),
    ):
        asking_heygen()

    # HeyGen may have taken it, and charged for it: asking again could pay twice.
    assert taken == ["/v3/videos"]
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


@pytest.fixture
def real_services(httpserver: HTTPServer, settings: Settings) -> Iterator[None]:
    """The gateway's own services, not one handed to it, each pointed at the stand-in."""
    settings.HEYGEN_API_KEY = "heygen-test-key"
    settings.HEYGEN_BASE_URL = httpserver.url_for("")
    settings.FAL_KEY = "fal-test-key"
    settings.FAL_QUEUE_URL = httpserver.url_for("")
    # Made once and kept: made again here with the stand-in's address, and after, so no
    # other test is sent to it.
    gateway._heygen.cache_clear()
    gateway._boreal.cache_clear()
    yield
    gateway._heygen.cache_clear()
    gateway._boreal.cache_clear()


def test_a_talking_clip_is_made_by_heygen_and_a_b_roll_clip_by_boreal(
    httpserver: HTTPServer, real_services: None
) -> None:
    taking_uploads(httpserver)
    httpserver.expect_request("/v3/videos", method="POST").respond_with_json(
        {"data": {"video_id": "vid-1"}}
    )
    httpserver.expect_request("/creatify/boreal", method="POST").respond_with_json(QUEUED)
    httpserver.expect_request(HEYGEN_CLIP).respond_with_json(
        {"data": {"id": "vid-1", "status": "completed", "video_url": httpserver.url_for(CLIP_LINK)}}
    )
    httpserver.expect_request(STATUS).respond_with_json(
        {"request_id": "req-1", "status": "COMPLETED"}
    )
    httpserver.expect_request(RESULT).respond_with_json(
        {"video": {"url": httpserver.url_for(CLIP_LINK), "content_type": "video/mp4"}}
    )
    httpserver.expect_request(CLIP_LINK).respond_with_data(MADE)

    assert asking_heygen() == "vid-1"
    assert submitting(audio_key=None) == "req-1"
    # Each clip is waited for at the service that was asked for it.
    collecting_from_heygen()
    collect_clip(job=None, purpose="collect_broll_clip", video_id="req-1")

    assert [
        (call.purpose, call.provider, call.model)
        for call in ModelCall.objects.order_by("created_at", "id")
    ] == [
        ("make_talking_clip", "heygen", "heygen/avatar-iv"),
        ("make_broll_clip", "fal", "creatify/boreal"),
        ("collect_talking_clip", "heygen", "heygen/avatar-iv"),
        ("collect_broll_clip", "fal", "creatify/boreal"),
    ]
