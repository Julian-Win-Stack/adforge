import base64
import io
import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import PIL.Image
import pytest
from django.conf import settings as django_settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from pydantic import BaseModel, ValidationError
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway import catalog
from gateway.fake import FakeModel
from gateway.gateway import (
    UnreadableImage,
    VideoNotWatchable,
    call_model,
    collect_clip,
    speak,
    submit_broll_clip,
    submit_clip,
    watch_video,
)
from gateway.models import ModelCall
from gateway.types import AnswerTookTooLong, Handoff, Image, ReferenceVideo, UnusableReply
from jobs.product_videos import PieceWanted, cut_pieces
from jobs.work import PageCheck, PageCheckHandoff

from .conftest import READABLE, a_video, has_sound, openai_answer, openai_reply, picture, video

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


class Moments(BaseModel):
    moments: list[str]


class Told(Handoff):
    product_name: str


def _watch(video_key: str) -> Moments:
    return watch_video(
        job=None,
        purpose="watch_video",
        instructions="Watch the product's video.",
        handoff=Told(product_name="Stoneware Mug"),
        video_key=video_key,
        output=Moments,
    )


def test_a_video_is_watched_at_480_px_with_its_sound_and_the_call_records_it(
    fake_model: FakeModel,
) -> None:
    key = file_store.save("videos/page-video.mp4", a_video(540, 960, sound=True))
    fake_model.respond("watch_video", {"moments": ["The lid is twisted off."]})

    watched = _watch(key)

    assert watched == Moments(moments=["The lid is twisted off."])
    (asked,) = fake_model.videos_watched
    # The short side cut to 480 px, the long side as it was in proportion, the sound kept.
    width, height, _ = video(asked.video.data)
    assert (width, height) == (480, 854)
    assert has_sound(asked.video.data)
    assert asked.frames_per_second == 1
    assert asked.instructions == "Watch the product's video."
    call = ModelCall.objects.get()
    assert (call.purpose, call.model, call.outcome) == (
        "watch_video",
        "gemini-3.8-flash",
        "succeeded",
    )
    assert call.handoff == {"product_name": "Stoneware Mug"}
    assert call.video == asked.video.key
    assert file_store.read(call.video) == asked.video.data
    assert call.output == {"moments": ["The lid is twisted off."]}
    # The fake's 1,000 tokens in and 100 out, at Gemini Flash's $0.75 and $3.75 a million.
    assert call.cost_usd == Decimal("0.001125")


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        pytest.param((960, 540), (854, 480), id="on its side: its height is cut to 480"),
        pytest.param((64, 96), (64, 96), id="already smaller upright: never made bigger"),
        pytest.param((96, 64), (96, 64), id="already smaller on its side: never made bigger"),
    ],
)
def test_a_video_is_watched_with_its_short_side_at_most_480_px_and_no_sound_if_it_has_none(
    fake_model: FakeModel, given: tuple[int, int], sent: tuple[int, int]
) -> None:
    key = file_store.save("videos/page-video.mp4", a_video(*given, sound=False))
    fake_model.respond("watch_video", {"moments": []})

    _watch(key)

    (asked,) = fake_model.videos_watched
    assert video(asked.video.data)[:2] == sent
    assert not has_sound(asked.video.data)


def test_a_video_watched_again_is_sent_as_cut_the_first_time(fake_model: FakeModel) -> None:
    key = file_store.save("videos/page-video.mp4", a_video(540, 960, sound=True))
    fake_model.respond("watch_video", {"moments": []}, {"moments": []})

    _watch(key)
    _watch(key)

    first, second = ModelCall.objects.all()
    assert first.video == second.video


def test_a_cut_left_half_saved_is_cut_again_before_it_is_sent(fake_model: FakeModel) -> None:
    key = file_store.save("videos/page-video.mp4", a_video(540, 960, sound=True))
    fake_model.respond("watch_video", {"moments": []}, {"moments": []})
    _watch(key)
    cut = ModelCall.objects.get().video
    whole = file_store.read(cut)
    # A worker stopped while saving the cut.
    default_storage.delete(cut)
    assert file_store.save(cut, whole[: len(whole) // 2]) == cut

    _watch(key)

    _, again = fake_model.videos_watched
    assert again.video.data == whole


def test_a_video_ffmpeg_cant_cut_fails_saying_what_ffmpeg_said(fake_model: FakeModel) -> None:
    key = file_store.save("videos/page-video.mp4", b"not a video at all")

    with pytest.raises(VideoNotWatchable, match="couldn't cut the video .*Invalid data"):
        _watch(key)


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


def _submit(
    picture_key: str = "picture.png", audio_key: str | None = "line.wav", seconds: float = 4.0
) -> str:
    return submit_clip(
        job=None,
        purpose="make_talking_clip",
        picture_key=picture_key,
        audio_key=audio_key,
        seconds=seconds,
        motion_prompt="She talks to the camera.",
    )


def test_a_clip_with_no_picture_is_refused_before_the_video_service_is_called(
    fake_model: FakeModel,
) -> None:
    with pytest.raises(ValidationError, match="picture"):
        _submit(picture_key="")
    assert fake_model.clips_submitted == []
    assert not ModelCall.objects.exists()


# A talking clip is asked for as 1 to 20 seconds.
@pytest.mark.parametrize("seconds", [0.5, 20.5])
def test_a_clip_of_a_length_the_video_model_cant_make_is_refused_before_it_is_called(
    fake_model: FakeModel, seconds: float
) -> None:
    picture_key = file_store.save("picture.png", picture(72, 128, (1, 2, 3)))
    audio_key = file_store.save("line.wav", b"RIFF a line")

    with pytest.raises(ValidationError, match="seconds"):
        _submit(picture_key, audio_key, seconds=seconds)
    assert fake_model.clips_submitted == []
    assert ModelCall.objects.count() == 0


@pytest.mark.parametrize("seconds", [1, 20])
def test_a_clip_at_either_end_of_the_lengths_the_video_model_makes_is_asked_for(
    fake_model: FakeModel, seconds: float
) -> None:
    picture_key = file_store.save("picture.png", picture(72, 128, (1, 2, 3)))
    audio_key = file_store.save("line.wav", b"RIFF a line")

    _submit(picture_key, audio_key, seconds=seconds)

    assert fake_model.clips_asked == [(seconds, True, "She talks to the camera.")]


def _broll(
    *,
    start: str | None = "start.png",
    examples: Sequence[str] = (),
    seconds: int = 5,
) -> str:
    return submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=start,
        example_picture_keys=list(examples),
        seconds=seconds,
        prompt="Tea is poured into the mug.",
    )


@pytest.fixture
def pictures_kept() -> list[str]:
    """Six different pictures in the file store, by their keys."""
    return [file_store.save(f"photo-{n}.png", picture(72, 128, (n, 2, 3))) for n in range(1, 7)]


def test_a_b_roll_clip_from_a_starting_picture_is_sent_its_picture_seconds_and_prompt(
    fake_model: FakeModel, pictures_kept: list[str]
) -> None:
    _broll(start=pictures_kept[0], seconds=7)

    assert fake_model.broll_clips_asked == [
        {
            "starting_picture": file_store.read(pictures_kept[0]),
            "example_pictures": [],
            "reference_videos": [],
            "seconds": 7,
            "prompt": "Tea is poured into the mug.",
        }
    ]
    assert ModelCall.objects.get().handoff == {
        "starting_picture": pictures_kept[0],
        "example_pictures": [],
        "seconds": 7,
        "prompt": "Tea is poured into the mug.",
    }


def test_a_b_roll_clip_from_example_pictures_is_sent_each_in_order(
    fake_model: FakeModel, pictures_kept: list[str]
) -> None:
    _broll(start=None, examples=pictures_kept[:5])

    (asked,) = fake_model.broll_clips_asked
    assert (asked["starting_picture"], asked["example_pictures"]) == (
        None,
        [file_store.read(key) for key in pictures_kept[:5]],
    )


@pytest.mark.parametrize("seconds", [5, 7, 15])
def test_a_b_roll_clip_is_billed_for_every_second_asked_for(
    fake_model: FakeModel, seconds: int
) -> None:
    _broll(start=file_store.save("start.png", picture(72, 128, (1, 2, 3))), seconds=seconds)

    submitted = ModelCall.objects.get()
    # 0.4 credits a second at 768p, at $99 for 500 credits.
    assert (submitted.model, submitted.video_seconds, submitted.cost_usd) == (
        "creatify/boreal-h3",
        seconds,
        seconds * Decimal("0.0792"),
    )


@pytest.mark.parametrize(
    ("asked", "why"),
    [
        pytest.param(
            {"start": "photo", "examples": ["photo"]},
            "never both",
            id="a starting picture and example pictures",
        ),
        pytest.param(
            {"start": None, "examples": ["photo"] * 6},
            "at most 5 items",
            id="6 example pictures",
        ),
        pytest.param({"start": None}, "needs a starting picture or example", id="no picture"),
        pytest.param({"seconds": 4}, "greater than or equal to 5", id="4 seconds"),
        pytest.param({"seconds": 16}, "less than or equal to 15", id="16 seconds"),
        pytest.param({"seconds": 5.5}, "valid integer", id="part of a second"),
    ],
)
def test_a_b_roll_clip_boreal_h3_cant_make_is_refused_before_anything_is_sent(
    fake_model: FakeModel, pictures_kept: list[str], asked: dict[str, Any], why: str
) -> None:
    named = {
        "start": pictures_kept[0] if asked.get("start", "photo") else None,
        "examples": pictures_kept[: len(asked.get("examples", []))],
        "seconds": asked.get("seconds", 5),
    }

    with pytest.raises(ValidationError, match=why):
        _broll(**named)

    assert fake_model.broll_clips_asked == []
    assert not ModelCall.objects.exists()


# --- A piece of a page video, sent beside the example pictures --------------------------------
# ffmpeg cuts it for real from a tiny video; the fake video model keeps what it was sent.


@pytest.fixture(scope="module")
def page_video_file(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real 24-second video, 32 by 48 px at 5 frames a second, with sound, that brightens
    as it plays: about 10 brightness a second (see `brightness_at`)."""
    made = tmp_path_factory.mktemp("page-video") / "page.mp4"
    brightening = "nullsrc=s=32x48:r=5,format=yuv420p,geq=lum='16+T*9':cb=128:cr=128"
    subprocess.run(
        [django_settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", brightening]
        + ["-f", "lavfi", "-i", "sine=frequency=440"]
        + ["-t", "24", "-pix_fmt", "yuv420p", "-shortest", str(made)],
        check=True,
    )
    return made.read_bytes()


def brightness_at(data: bytes, seconds: float) -> float:
    """How bright a video is `seconds` in, 0 to 255."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video.mp4"
        file.write_bytes(data)
        frame = subprocess.run(
            [django_settings.FFMPEG, "-v", "error", "-i", str(file), "-ss", f"{seconds:g}"]
            + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    return sum(frame) / len(frame)


@pytest.fixture
def page_video(page_video_file: bytes) -> str:
    """The page video kept in the file store, by its key."""
    return file_store.save("jobs/1/videos/1.mp4", page_video_file)


def test_a_piece_cut_from_a_page_video_is_sent_with_the_seconds_it_was_cut_at(
    fake_model: FakeModel, pictures_kept: list[str], page_video: str, page_video_file: bytes
) -> None:
    cut = cut_pieces([PieceWanted(video=page_video, start=2, end=5)])
    submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=None,
        example_picture_keys=pictures_kept[:2],
        reference_videos=cut.sent,
        seconds=5,
        prompt="Tea is poured into the mug.",
    )

    (asked,) = fake_model.broll_clips_asked
    (piece,) = asked["reference_videos"]
    assert (piece["start"], piece["end"], piece["seconds"], piece["trim"]) == (
        2,
        5,
        3,
        None,
    )
    # The file sent is the cut piece itself: the page video's seconds 2 to 5.
    assert video(piece["file"])[2] == pytest.approx(3, abs=0.25)
    assert brightness_at(piece["file"], 0) == pytest.approx(
        brightness_at(page_video_file, 2), abs=3
    )
    assert brightness_at(piece["file"], 2.8) == pytest.approx(
        brightness_at(page_video_file, 4.8), abs=3
    )
    assert asked["example_pictures"] == [file_store.read(key) for key in pictures_kept[:2]]


def test_a_piece_cut_again_is_the_same_file_so_its_clip_is_found_by_its_handoff(
    page_video: str,
) -> None:
    wanted = [PieceWanted(video=page_video, start=2, end=5)]

    first, again = cut_pieces(wanted).sent, cut_pieces(wanted).sent

    assert first == again


def test_a_piece_left_half_written_is_cut_again(page_video: str) -> None:
    wanted = [PieceWanted(video=page_video, start=2, end=5)]
    (piece,) = cut_pieces(wanted).sent
    # A worker stopped while the piece was being saved.
    half = file_store.read(piece.file)[:100]
    default_storage.delete(piece.file)
    default_storage.save(piece.file, ContentFile(half))

    (again,) = cut_pieces(wanted).sent

    assert video(file_store.read(again.file))[2] == pytest.approx(3, abs=0.25)


def test_a_moment_over_15_seconds_is_cut_to_its_first_15_and_says_it_was_trimmed(
    fake_model: FakeModel, pictures_kept: list[str], page_video: str
) -> None:
    submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=None,
        example_picture_keys=pictures_kept[:1],
        reference_videos=cut_pieces([PieceWanted(video=page_video, start=3, end=23)]).sent,
        seconds=5,
        prompt="Tea is poured into the mug.",
    )

    (piece,) = fake_model.broll_clips_asked[-1]["reference_videos"]
    assert (piece["start"], piece["end"], piece["seconds"], piece["trim"]) == (
        3,
        18,
        15,
        "moment_over_limit",
    )
    # Cut, never sped up: fifteen seconds of the page video play in fifteen.
    assert video(piece["file"])[2] == pytest.approx(15, abs=0.25)


def test_pieces_over_15_seconds_in_all_have_the_last_trimmed_to_fit(
    fake_model: FakeModel, pictures_kept: list[str], page_video: str, settings: Settings
) -> None:
    settings.BROLL_MOST_PIECES = 3
    cut = cut_pieces(
        [
            PieceWanted(video=page_video, start=0, end=10),
            PieceWanted(video=page_video, start=12, end=22),
        ]
    )
    submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=None,
        example_picture_keys=pictures_kept[:1],
        reference_videos=cut.sent,
        seconds=5,
        prompt="Tea is poured into the mug.",
    )

    sent = fake_model.broll_clips_asked[-1]["reference_videos"]
    assert [(piece["start"], piece["end"], piece["trim"]) for piece in sent] == [
        (0, 10, None),
        (12, 17, "pieces_over_limit"),
    ]
    assert [video(piece["file"])[2] for piece in sent] == [
        pytest.approx(10, abs=0.25),
        pytest.approx(5, abs=0.25),
    ]
    assert cut.left_out == []


def test_one_piece_is_sent_and_one_more_is_left_out_saying_why(page_video: str) -> None:
    first = PieceWanted(video=page_video, start=0, end=4)
    second = PieceWanted(video=page_video, start=6, end=9)

    cut = cut_pieces([first, second])

    assert [(piece.start, piece.end) for piece in cut.sent] == [(0, 4)]
    assert [(left.wanted, left.reason) for left in cut.left_out] == [
        (second, "a clip is sent at most 1 of the pieces wanted")
    ]


def test_a_piece_with_under_2_seconds_of_room_left_is_left_out_saying_why(
    page_video: str, settings: Settings
) -> None:
    settings.BROLL_MOST_PIECES = 3
    last = PieceWanted(video=page_video, start=16, end=20)

    cut = cut_pieces([PieceWanted(video=page_video, start=0, end=14), last])

    assert [(piece.start, piece.end) for piece in cut.sent] == [(0, 14)]
    assert [(left.wanted, left.reason) for left in cut.left_out] == [
        (last, "the pieces before it fill the 15 seconds of video a clip is sent")
    ]


def test_a_moment_under_2_seconds_is_left_out_saying_why(page_video: str) -> None:
    wanted = PieceWanted(video=page_video, start=7, end=8)

    cut = cut_pieces([wanted])

    assert cut.sent == []
    assert [(left.wanted, left.reason) for left in cut.left_out] == [
        (wanted, "it holds 1 s of video, and the video model takes pieces of at least 2 s")
    ]


def test_a_moment_past_the_videos_end_is_cut_at_the_end_and_says_so(page_video: str) -> None:
    (piece,) = cut_pieces([PieceWanted(video=page_video, start=20, end=26)]).sent

    # The page video is 24 seconds long: the piece holds, and says, only the seconds it has.
    assert (piece.start, piece.trim) == (20, "video_ended")
    assert piece.end == pytest.approx(24, abs=0.1)
    assert video(file_store.read(piece.file))[2] == pytest.approx(4, abs=0.25)


@pytest.mark.parametrize(
    ("start", "end", "holds"),
    [
        pytest.param(23, 28, "1", id="1 second left in the video"),
        pytest.param(25, 29, "0", id="starting past the end"),
    ],
)
def test_a_moment_with_under_2_seconds_left_in_its_video_is_left_out_saying_why(
    page_video: str, start: float, end: float, holds: str
) -> None:
    wanted = PieceWanted(video=page_video, start=start, end=end)

    cut = cut_pieces([wanted])

    assert cut.sent == []
    assert [(left.wanted, left.reason) for left in cut.left_out] == [
        (
            wanted,
            f"it holds {holds} s of video, and the video model takes pieces of at least 2 s",
        )
    ]


def test_a_page_video_ffmpeg_cant_read_is_never_sent_as_a_piece() -> None:
    unreadable = file_store.save("jobs/1/videos/2.mp4", b"\0\0\0\x18ftypisom not a video")

    with pytest.raises(RuntimeError, match="couldn't read the length"):
        cut_pieces([PieceWanted(video=unreadable, start=0, end=4)])


def test_a_clip_sent_a_piece_is_billed_for_the_pieces_seconds_too(
    fake_model: FakeModel, pictures_kept: list[str], page_video: str
) -> None:
    submit_broll_clip(
        job=None,
        purpose="make_broll_clip",
        starting_picture_key=None,
        example_picture_keys=pictures_kept[:1],
        reference_videos=cut_pieces([PieceWanted(video=page_video, start=2, end=5)]).sent,
        seconds=7,
        prompt="Tea is poured into the mug.",
    )

    submitted = ModelCall.objects.get()
    # 0.4 credits a second made, and 0.4 a second of reference video sent (spec #210).
    assert (submitted.video_seconds, submitted.cost_usd) == (7, 10 * Decimal("0.0792"))
    # The record says which piece was sent, and from which seconds of which video.
    (piece,) = submitted.handoff["reference_videos"]
    assert (piece["video"], piece["start"], piece["end"], piece["trim"]) == (
        page_video,
        2,
        5,
        None,
    )


def _piece(start: float, end: float) -> ReferenceVideo:
    return ReferenceVideo.model_construct(
        file="piece.mp4", video="page.mp4", start=start, end=end, trim=None
    )


@pytest.mark.parametrize(
    ("start", "pieces", "why"),
    [
        pytest.param(True, [(0, 5)], "takes no reference videos", id="with a starting picture"),
        pytest.param(False, [(0, 10), (10, 16)], "at most 15 seconds", id="16 seconds in all"),
        pytest.param(False, [(0, 1)], "2 to 15 seconds long", id="a 1-second piece"),
        pytest.param(False, [(0, 16)], "2 to 15 seconds long", id="a 16-second piece"),
    ],
)
def test_reference_videos_boreal_h3_cant_take_are_refused_before_anything_is_sent(
    fake_model: FakeModel,
    pictures_kept: list[str],
    start: bool,
    pieces: list[tuple[float, float]],
    why: str,
) -> None:
    with pytest.raises(ValidationError, match=why):
        submit_broll_clip(
            job=None,
            purpose="make_broll_clip",
            starting_picture_key=pictures_kept[0] if start else None,
            example_picture_keys=[] if start else pictures_kept[:1],
            reference_videos=[_piece(*piece) for piece in pieces],
            seconds=5,
            prompt="Tea is poured into the mug.",
        )

    assert fake_model.broll_clips_asked == []
    assert not ModelCall.objects.exists()


def test_a_clip_is_asked_for_again_while_the_video_service_is_down(
    fake_model: FakeModel,
) -> None:
    fake_model.respond("make_clip", OutsideServiceDown("fal answered 503"))
    picture_key = file_store.save("picture.png", picture(72, 128, (1, 2, 3)))
    audio_key = file_store.save("line.wav", b"RIFF a line")

    assert _submit(picture_key, audio_key) == "video-1"

    failed, submitted = ModelCall.objects.all()
    assert (failed.attempt, failed.outcome) == (1, ModelCall.Outcome.FAILED)
    assert (submitted.attempt, submitted.outcome) == (2, ModelCall.Outcome.SUCCEEDED)
    assert fake_model.clips_submitted == ["video-1"]


def test_a_clip_being_made_is_waited_for_then_kept(fake_model: FakeModel) -> None:
    fake_model.respond("collect_clip", {"state": "working"}, {"state": "working"})

    key = collect_clip(job=None, purpose="collect_broll_clip", video_id="video-1")

    assert file_store.read(key) == fake_model.clips["video-1"]
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

    key = collect_clip(job=None, purpose="collect_broll_clip", video_id="video-1")

    assert clock.now == 3600
    assert file_store.read(key) == fake_model.clips["video-1"]
    assert ModelCall.objects.get().outcome == ModelCall.Outcome.SUCCEEDED


def test_a_slow_clip_is_told_of_once_counted_from_the_first_look_even_if_the_service_went_down(
    fake_model: FakeModel, settings: Settings, clock: FakeClock
) -> None:
    settings.CLIP_POLL_SECONDS = 10
    settings.CLIP_SLOW_AFTER_SECONDS = 30
    working = {"state": "working"}
    down = OutsideServiceDown("fal answered 503")
    # Looked at 0, down at 10 and looked again at once, then at 20 and 30, down at 40 and
    # looked again at once, then made by 50.
    fake_model.respond("collect_clip", working, down, working, working, working, down, working)
    told_slow_at: list[float] = []

    collect_clip(
        job=None,
        purpose="collect_broll_clip",
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


# How long a slow purpose is given to answer below, and how long the stand-in server takes
# over it: longer, by enough that the client gives up first however loaded the machine is.
SLOW_ANSWER_SECONDS = 0.3
STALLED_SECONDS = 0.6


def _answers_after(httpserver: HTTPServer, seconds: float) -> None:
    """The stand-in OpenAI server answers its next request only after `seconds`."""

    def slowly(_: Request) -> Response:
        time.sleep(seconds)
        return Response(json.dumps(openai_answer(READABLE)), content_type="application/json")

    httpserver.expect_oneshot_request("/v1/responses", method="POST").respond_with_handler(slowly)


def test_a_slow_purpose_that_takes_too_long_is_given_up_on_and_not_asked_again(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Such as the clip judge: asked again, a stalled ask would hold the clip step for each try.
    settings.RETRY_DELAYS_SECONDS = [0, 0]
    monkeypatch.setattr(catalog, "SLOW_ANSWER_SECONDS", {"check_page": SLOW_ANSWER_SECONDS})
    _answers_after(httpserver, STALLED_SECONDS)

    with pytest.raises(AnswerTookTooLong):
        _check_page([])

    assert ModelCall.objects.get().outcome == "failed"


def test_only_the_purposes_named_slow_get_their_own_time_to_answer(
    httpserver: HTTPServer, openai_server: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog, "SLOW_ANSWER_SECONDS", {"check_clip_sides": SLOW_ANSWER_SECONDS})
    _answers_after(httpserver, STALLED_SECONDS)

    _check_page([])

    assert ModelCall.objects.get().outcome == "succeeded"
