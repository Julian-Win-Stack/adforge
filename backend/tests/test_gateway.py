import base64
import io
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import PIL.Image
import pytest
from pydantic import ValidationError
from pytest_django import Settings
from pytest_httpserver import HTTPServer

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.gateway import UnreadableImage, call_model, collect_clip, speak, submit_clip
from gateway.models import ModelCall
from gateway.types import Image, UnusableReply
from jobs.work import PageCheck, PageCheckHandoff

from .conftest import READABLE, openai_answer, openai_reply, picture

pytestmark = pytest.mark.django_db


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")
def test_a_bad_handoff_is_refused_before_the_model_is_called(fake_model: FakeModel) -> None:
    # model_construct skips validation, like a caller that built the handoff carelessly.
    bad = PageCheckHandoff.model_construct(
        product_url="https://shop.example/products/mug",
        page_url="https://shop.example/products/mug",
        page_text="Stoneware Mug",
        photo_count=2.0,  # type: ignore[arg-type]  # A decimal where a whole number belongs.
    )

    # The fake has nothing scripted, so reaching it would raise AssertionError instead.
    with pytest.raises(ValidationError):
        call_model(
            job=None,
            purpose="check_page",
            instructions="Check the page.",
            handoff=bad,
            output=PageCheck,
        )
    assert not ModelCall.objects.exists()


def test_a_voice_line_with_no_voice_is_refused_before_the_voice_service_is_called(
    fake_model: FakeModel,
) -> None:
    with pytest.raises(ValidationError, match="voice_id"):
        speak(job=None, purpose="measure_voice", voice_id="", text="Yours for $24.00.")
    assert not ModelCall.objects.exists()


def _check_page(images: list[Image]) -> None:
    call_model(
        job=None,
        purpose="check_page",
        instructions="Check the page.",
        handoff=PageCheckHandoff(
            product_url="https://shop.example/products/mug",
            page_url="https://shop.example/products/mug",
            page_text="Stoneware Mug",
            photo_count=len(images),
        ),
        output=PageCheck,
        images=images,
    )


@pytest.mark.parametrize(
    ("key", "content", "why"),
    [
        pytest.param(
            "photos/side.bmp",
            picture(40, 30, (143, 170, 140), "BMP"),
            "is image/bmp, which models can't read",
            id="another format",
        ),
        pytest.param(
            "photos/side.png",
            b"\x89PNG cut off",
            "can't be opened as a picture",
            id="not a picture",
        ),
        pytest.param(
            # Named for what the shop said it was, not what it is.
            "photos/side.png",
            picture(40, 30, (143, 170, 140), "BMP"),
            "is image/bmp, which models can't read",
            id="another format under a PNG name",
        ),
    ],
)
def test_an_image_models_cant_read_is_refused_before_the_model_is_called(
    fake_model: FakeModel, key: str, content: bytes, why: str
) -> None:
    file_store.save(key, content)

    # The fake has nothing scripted, so reaching it would raise AssertionError instead.
    with pytest.raises(UnreadableImage) as refused:
        _check_page([Image(label="Photo 1", key=key)])
    assert str(refused.value) == f"Photo 1 ({key}) {why}"
    assert not ModelCall.objects.exists()


def test_a_photo_a_phone_saved_sideways_is_shown_the_right_way_up(
    httpserver: HTTPServer, openai_server: Callable[..., None]
) -> None:
    # Phones store a portrait photo as landscape pixels plus a note saying "turn it 90 degrees
    # clockwise to view". The model is shown the photo as a person would see it: tall, with
    # the stored left half, black here, on top. Turned the other way, black would be at the
    # bottom and the photo upside down.
    sideways = PIL.Image.new("RGB", (48, 24), "white")
    sideways.paste("black", (0, 0, 24, 24))
    note = PIL.Image.Exif()
    note[0x0112] = 6  # Orientation: turn 90 degrees clockwise to view.
    file = io.BytesIO()
    sideways.save(file, format="JPEG", exif=note)
    key = file_store.save("photos/portrait.jpg", file.getvalue())
    openai_server(openai_answer(READABLE))

    _check_page([Image(label="Photo 1", key=key)])

    [request] = [request for request, _ in httpserver.log if request.path == "/v1/responses"]
    [message] = request.get_json()["input"]
    image_url = message["content"][2]["image_url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    shown = PIL.Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1])))
    # Sent as the JPEG it is: the bytes, not only the label.
    assert shown.format == "JPEG"
    assert shown.size == (24, 48)
    # JPEG blurs colours a little, so each half is read as nearer black or nearer white.
    greys = shown.convert("L")
    top, bottom = (greys.getpixel((12, y)) for y in (6, 42))
    assert isinstance(top, int) and isinstance(bottom, int)
    assert ["black" if shade < 128 else "white" for shade in (top, bottom)] == ["black", "white"]


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            openai_reply({"type": "refusal", "refusal": "I can't help with that."}),
            id="refused",
        ),
        pytest.param(
            openai_reply(
                {"type": "output_text", "text": '{"decision": "read', "annotations": []},
                status="incomplete",
            ),
            id="cut off mid-answer",
        ),
    ],
)
def test_a_reply_that_cant_be_used_still_records_what_it_cost(
    openai_server: Callable[[Any], None], reply: dict[str, Any]
) -> None:
    openai_server(reply)

    with pytest.raises(UnusableReply):
        call_model(
            job=None,
            purpose="check_page",
            instructions="Check the page.",
            handoff=PageCheckHandoff(
                product_url="https://shop.example/products/mug",
                page_url="https://shop.example/products/mug",
                page_text="Stoneware Mug",
                photo_count=2,
            ),
            output=PageCheck,
        )

    call = ModelCall.objects.get()
    assert call.outcome == "failed"
    assert (call.input_tokens, call.output_tokens) == (1_200, 300)
    # gpt-5-mini: 1,200 x $0.25/M in + 300 x $2.00/M out = $0.0003 + $0.0006.
    assert call.cost_usd == Decimal("0.0009")


def test_a_call_that_fails_still_records_which_photos_it_showed(
    openai_server: Callable[[Any], None],
) -> None:
    key = file_store.save("photos/front.png", picture(40, 30, (143, 170, 140)))
    openai_server(openai_reply({"type": "refusal", "refusal": "I can't help with that."}))

    with pytest.raises(UnusableReply):
        _check_page([Image(label="Photo 1", key=key)])

    call = ModelCall.objects.get()
    assert (call.outcome, call.images) == (
        "failed",
        [{"label": "Photo 1", "key": "photos/front.png"}],
    )


def _submit(picture_key: str = "picture.png", audio_key: str = "line.wav") -> str:
    return submit_clip(
        job=None,
        purpose="make_clip",
        picture_key=picture_key,
        audio_key=audio_key,
        audio_seconds=4.0,
        motion_prompt="She talks to the camera.",
    )


def test_a_clip_with_no_picture_is_refused_before_the_video_service_is_called(
    fake_model: FakeModel,
) -> None:
    with pytest.raises(ValidationError, match="picture"):
        _submit(picture_key="")
    assert fake_model.clips_submitted == []
    assert not ModelCall.objects.exists()


def test_a_clip_is_asked_for_again_while_the_video_service_is_down(
    fake_model: FakeModel,
) -> None:
    fake_model.respond("make_clip", OutsideServiceDown("HeyGen answered 503"))
    picture_key = file_store.save("picture.png", picture(72, 128, (1, 2, 3)))
    audio_key = file_store.save("line.wav", b"RIFF a line")

    assert _submit(picture_key, audio_key) == "video-1"

    failed, submitted = ModelCall.objects.all()
    assert (failed.attempt, failed.outcome) == (1, ModelCall.Outcome.FAILED)
    assert (submitted.attempt, submitted.outcome) == (2, ModelCall.Outcome.SUCCEEDED)
    assert fake_model.clips_submitted == ["video-1"]


def test_a_clip_being_made_is_waited_for_then_kept(fake_model: FakeModel) -> None:
    fake_model.respond("collect_clip", {"state": "working"}, {"state": "working"})

    key = collect_clip(job=None, purpose="collect_clip", video_id="video-1")

    assert file_store.read(key) == b"fake clip video-1"
    collected = ModelCall.objects.get()
    assert (collected.outcome, collected.output) == (ModelCall.Outcome.SUCCEEDED, {"file": key})


class FakeClock:
    """Time that passes only when slept through, so waiting takes no real time."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    return clock


def test_a_slow_clip_is_waited_for_until_it_is_made(
    fake_model: FakeModel, settings: Settings, clock: FakeClock
) -> None:
    settings.CLIP_POLL_SECONDS = 60
    # Still being made after an hour of looking once a minute: it's paid for, so it's
    # never given up on.
    fake_model.respond("collect_clip", *[{"state": "working"}] * 60)

    key = collect_clip(job=None, purpose="collect_clip", video_id="video-1")

    assert clock.now == 3600
    assert file_store.read(key) == b"fake clip video-1"
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.SUCCEEDED


def test_a_slow_clip_is_told_of_once_counted_from_the_first_look_even_if_the_service_went_down(
    fake_model: FakeModel, settings: Settings, clock: FakeClock
) -> None:
    settings.CLIP_POLL_SECONDS = 10
    settings.CLIP_SLOW_AFTER_SECONDS = 30
    working = {"state": "working"}
    down = OutsideServiceDown("HeyGen answered 503")
    # Looked at 0, down at 10 and looked again at once, then at 20 and 30, down at 40 and
    # looked again at once, then made by 50.
    fake_model.respond("collect_clip", working, down, working, working, working, down, working)
    told_slow_at: list[float] = []

    collect_clip(
        job=None,
        purpose="collect_clip",
        video_id="video-1",
        when_slow=lambda: told_slow_at.append(clock.now),
    )

    # The first look once 30 seconds have been waited, counted from the start, and only that one.
    assert told_slow_at == [30]
    assert [(c.attempt, c.outcome) for c in ModelCall.objects.all()] == [
        (1, ModelCall.Outcome.FAILED),
        (2, ModelCall.Outcome.FAILED),
        (3, ModelCall.Outcome.SUCCEEDED),
    ]
