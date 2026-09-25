"""Our real picture and voice code, talking to stand-in services on this machine that reply
the way the real ones did on 2026-09-19 (docs/real-api-replies.md)."""

import base64
import io
import json
import time
import wave
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from adforge.retry import OutsideServiceDown
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


def test_speech_that_isnt_a_wav_file_is_refused(
    httpserver: HTTPServer, inworld: InworldProvider
) -> None:
    httpserver.expect_oneshot_request("/tts/v1/voice", method="POST").respond_with_json(
        {"audioContent": base64.b64encode(b"ID3 an mp3 file").decode()}
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


# --- HeyGen -----------------------------------------------------------------------------------


@pytest.fixture
def heygen(httpserver: HTTPServer, settings: Settings) -> HeyGenProvider:
    settings.HEYGEN_API_KEY = "heygen-test-key"
    settings.HEYGEN_BASE_URL = httpserver.url_for("")
    return HeyGenProvider()


AUTHORISED = {"x-api-key": "heygen-test-key"}


def taking_uploads(httpserver: HTTPServer) -> list[tuple[str, bytes, str]]:
    """Have HeyGen take every file uploaded to it. Gives each one's name, bytes and type."""
    uploaded: list[tuple[str, bytes, str]] = []

    def upload(request: Request) -> Response:
        file = request.files["file"]
        uploaded.append((file.filename or "", file.read(), file.content_type or ""))
        asset_id = f"asset-{len(uploaded)}"
        return Response(
            json.dumps({"data": {"asset_id": asset_id}}), content_type="application/json"
        )

    httpserver.expect_request("/v3/assets", method="POST", headers=AUTHORISED).respond_with_handler(
        upload
    )
    return uploaded


def submitting(picture_key: str = "", audio_key: str = "") -> str:
    """Ask for a 5.4-second clip, of a picture and audio kept in the file store."""
    return submit_clip(
        job=None,
        purpose="make_clip",
        picture_key=picture_key
        or file_store.save("starting_picture.png", picture(72, 128, (1, 2, 3))),
        audio_key=audio_key or file_store.save("speak_line.wav", wav(5.4)),
        audio_seconds=5.4,
        motion_prompt="She talks to the camera.",
    )


def test_a_clip_is_made_from_a_picture_and_audio_through_heygen(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    starting_picture = picture(1152, 2048, (200, 180, 160))
    line = wav(5.4)
    uploaded = taking_uploads(httpserver)
    httpserver.expect_oneshot_request(
        "/v3/videos",
        method="POST",
        headers=AUTHORISED,
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
    ).respond_with_json({"data": {"video_id": "vid-1"}})
    # Still being made when first asked, then made.
    httpserver.expect_oneshot_request(
        "/v3/videos/vid-1", method="GET", headers=AUTHORISED
    ).respond_with_json({"data": {"id": "vid-1", "status": "processing"}})
    httpserver.expect_oneshot_request(
        "/v3/videos/vid-1", method="GET", headers=AUTHORISED
    ).respond_with_json(
        {
            "data": {
                "id": "vid-1",
                "status": "completed",
                "video_url": httpserver.url_for("/files/vid-1.mp4"),
            }
        }
    )
    made = b"\0\0\0\x18ftypmp42 a whole clip"
    httpserver.expect_oneshot_request("/files/vid-1.mp4").respond_with_data(made)
    picture_key = file_store.save("starting_picture.png", starting_picture)
    audio_key = file_store.save("speak_line.wav", line)

    with use_model(heygen):
        video_id = submitting(picture_key, audio_key)
        key = collect_clip(job=None, purpose="collect_clip", video_id=video_id)

    assert video_id == "vid-1"
    assert uploaded == [
        ("starting_picture.png", starting_picture, "image/png"),
        ("line.wav", line, "audio/wav"),
    ]
    assert file_store.read(key) == made
    submitted, collected = ModelCall.objects.all()
    # HeyGen bills the 5.4 seconds of video it makes, at $0.035 a second.
    assert (submitted.provider, submitted.video_seconds, submitted.cost_usd) == (
        "heygen",
        5.4,
        Decimal("0.189000"),
    )
    assert submitted.output == {"video_id": "vid-1"}
    # Waiting for the clip and fetching it costs nothing more.
    assert (collected.handoff, collected.output, collected.cost_usd) == (
        {"video_id": "vid-1"},
        {"file": key},
        Decimal(0),
    )


def test_a_clip_heygen_couldnt_make_is_not_asked_for_again(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    httpserver.expect_oneshot_request("/v3/videos/vid-1", method="GET").respond_with_json(
        {"data": {"id": "vid-1", "status": "failed", "failure_message": "No face was found."}}
    )

    with use_model(heygen), pytest.raises(ClipFailed, match="No face was found."):
        collect_clip(job=None, purpose="collect_clip", video_id="vid-1")
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_a_clip_heygen_no_longer_knows_of_counts_as_one_it_couldnt_make(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    # What HeyGen answered on 2026-09-25 when asked about a video it didn't know.
    httpserver.expect_oneshot_request("/v3/videos/vid-1", method="GET").respond_with_json(
        {
            "error": {
                "code": "video_not_found",
                "doc_url": "https://developers.heygen.com/docs/error-codes#video-not-found",
                "message": "Video vid-1 not found",
            }
        },
        status=404,
    )

    with use_model(heygen), pytest.raises(ClipFailed) as raised:
        collect_clip(job=None, purpose="collect_clip", video_id="vid-1")

    # Waited for, it would never be made.
    assert str(raised.value) == "HeyGen no longer knows of this clip: Video vid-1 not found"
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED


def test_a_not_found_from_heygen_that_isnt_about_the_clip_doesnt_count_as_the_clip_failing(
    httpserver: HTTPServer, heygen: HeyGenProvider
) -> None:
    # Such as its address having moved: the clip it was asked about may still be made, and
    # counting it as failed would pay for it again.
    httpserver.expect_oneshot_request("/v3/videos/vid-1", method="GET").respond_with_data(
        "Not Found", status=404
    )

    with use_model(heygen), pytest.raises(httpx.HTTPStatusError, match="404 NOT FOUND"):
        collect_clip(job=None, purpose="collect_clip", video_id="vid-1")


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
        assert submitting() == "vid-1"

    assert [(c.attempt, c.outcome, c.cost_usd) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED, None),
        (2, ModelCall.Outcome.SUCCEEDED, Decimal("0.189000")),
    ]


def test_a_clip_whose_reply_from_heygen_is_lost_isnt_asked_for_again(
    httpserver: HTTPServer, heygen: HeyGenProvider, settings: Settings
) -> None:
    settings.HEYGEN_TIMEOUT_SECONDS = 0.2
    impatient = HeyGenProvider()
    taking_uploads(httpserver)
    asked: list[str] = []

    def answering_too_late(request: Request) -> Response:
        asked.append(request.path)
        time.sleep(0.5)
        return Response(json.dumps({"data": {"video_id": "vid-1"}}))

    httpserver.expect_request("/v3/videos", method="POST").respond_with_handler(answering_too_late)

    with use_model(impatient), pytest.raises((ClipFailed, OutsideServiceDown)) as raised:
        submitting()

    # HeyGen may have taken it, and charged for it: asking again could pay twice.
    assert asked == ["/v3/videos"]
    assert type(raised.value) is ClipFailed
    assert str(raised.value).startswith("HeyGen's reply was lost after the clip was asked for")
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.FAILED
