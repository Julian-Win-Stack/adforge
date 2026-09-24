"""Our real picture and voice code, talking to stand-in services on this machine that reply
the way the real ones did on 2026-09-19 (docs/real-api-replies.md)."""

import base64
import io
import json
import wave
from decimal import Decimal

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from gateway.gateway import design_voice, draw_picture, edit_picture, speak, use_model
from gateway.inworld_adapter import InworldProvider
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider

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
    photo_content = picture(1600, 1200, (143, 170, 140))
    photo = file_store.save("photo.png", photo_content)
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
