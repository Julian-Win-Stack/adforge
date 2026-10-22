"""Product videos: the gallery videos the photo picker picks off the page are kept with the
job, as files with a few frames, or as links with a first frame when no file can be had.
Through the chat, with the picker's picked players set by the test."""

import dataclasses
import io
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import PIL.Image
import pytest
from django.conf import settings
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import FakeModel, turn
from jobs import product_videos, video_frames, work
from jobs.models import Job, ProductVideo
from jobs.work import PageRead, Picked

from .conftest import READABLE, SHAMPOO_PHOTO_PATH, FakeFirecrawl, picture, results_of

pytestmark = pytest.mark.django_db(transaction=True)

POSTER = picture(320, 180, (30, 90, 160), format="JPEG")


@pytest.fixture(scope="module")
def tiny_mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real two-second video, 64 by 96 px."""
    made = tmp_path_factory.mktemp("video") / "tiny.mp4"
    subprocess.run(
        [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x96:rate=25"]
        + ["-t", "2", "-pix_fmt", "yuv420p", str(made)],
        check=True,
    )
    return made.read_bytes()


@pytest.fixture(scope="module")
def three_shot_mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real 24-second video of three 8-second shots: a slow zoom, a still picture and a
    busy moving pattern, cut straight from one to the next."""
    made = tmp_path_factory.mktemp("video") / "three-shots.mp4"
    size = "size=160x120:rate=25"
    still = f"color=c=0x3366aa:{size},drawbox=x=40:y=30:w=60:h=50:color=yellow:t=fill"
    trims = ";".join(f"[{n}]trim=0:8,setpts=PTS-STARTPTS[s{n}]" for n in range(3))
    subprocess.run(
        [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", f"mandelbrot={size}"]
        + ["-f", "lavfi", "-i", still, "-f", "lavfi", "-i", f"testsrc2={size}"]
        + ["-filter_complex", f"{trims};[s0][s1][s2]concat=n=3:v=1[v]", "-map", "[v]"]
        + ["-pix_fmt", "yuv420p", str(made)],
        check=True,
    )
    return made.read_bytes()


@pytest.fixture(scope="module")
def changing_mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real two-second video whose every frame is a new picture (snow), and then a second
    of one picture held."""
    made = tmp_path_factory.mktemp("video") / "changing.mp4"
    snow = "nullsrc=size=64x64:rate=5,geq=lum='random(1)*255':cb=128:cr=128,trim=0:2"
    held = "color=c=0x3366aa:size=64x64:rate=5,trim=0:1"
    subprocess.run(
        [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", snow, "-f", "lavfi"]
        + ["-i", held, "-filter_complex", "[0][1]concat=n=2:v=1[v]", "-map", "[v]"]
        + ["-pix_fmt", "yuv420p", str(made)],
        check=True,
    )
    return made.read_bytes()


@pytest.fixture
def picks_videos(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """The video players the picker picks, as the page script lists them: a test fills it."""
    players: list[dict[str, Any]] = []
    picking = work.pick_photos

    def picks(job: Job, read: PageRead) -> Picked:
        return dataclasses.replace(picking(job, read), videos=tuple(players))

    monkeypatch.setattr(work, "pick_photos", picks)
    yield players


def player(n: int, src: str, *, kind: str = "video", poster: str = "") -> dict[str, Any]:
    """A video player as the page script numbers it."""
    return {
        "n": n,
        "kind": kind,
        "src": src,
        "poster": poster,
        "x": 0,
        "y": 400,
        "w": 600,
        "h": 600,
        "vis": 1.0,
        "shown": True,
        "box": 3,
        "href": "",
        "aria": "",
        "autoplay": True,
        "loop": True,
        "muted": True,
        "controls": False,
    }


def read(fake_model: FakeModel, say: Callable[..., None], link: str, *, times: int = 1) -> None:
    for _ in range(times):
        fake_model.respond(
            "produce",
            turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
            turn(says="Done."),
        )
        fake_model.respond("check_page", READABLE)
        say(f"Make an ad for {link}")


def is_jpeg_picture(key: str) -> bool:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as shown:
        shown.load()
        return shown.width > 0


def test_a_picked_gallery_video_is_kept_with_its_first_frame_four_frames_and_length(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
) -> None:
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    src = f"{firecrawl.shop}/cdn/videos/gallery.mp4"
    picks_videos.append(player(1, src))

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    (video,) = Job.objects.get().videos.all()
    assert (video.position, video.source_url, video.kind) == (1, src, "video")
    assert file_store.read(video.file) == tiny_mp4
    assert video.duration == pytest.approx(2.0, abs=0.1)
    assert is_jpeg_picture(video.first_frame)
    assert len(video.frames) == product_videos.FRAMES == 4
    assert all(is_jpeg_picture(frame) for frame in video.frames)
    # Spread over the video, so no two frames are the same picture.
    assert len({file_store.read(frame) for frame in video.frames}) == 4


@pytest.mark.parametrize("breaks", ["thinning", "every frame"])
def test_a_video_that_cant_be_thinned_is_kept_with_its_file_and_frames_but_not_read(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
    breaks: str,
) -> None:
    def broken(*_: Any) -> Any:
        raise subprocess.TimeoutExpired("ffmpeg", 120)

    if breaks == "thinning":
        monkeypatch.setattr(video_frames, "thin", broken)
    else:
        monkeypatch.setattr(product_videos, "_every_frame", broken)
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/gallery.mp4"))

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    (video,) = Job.objects.get().videos.all()
    assert file_store.read(video.file) == tiny_mp4
    assert video.duration == pytest.approx(2.0, abs=0.1)
    assert len(video.frames) == product_videos.FRAMES
    assert video.frames_picked == []
    assert video.read is None


def test_videos_with_no_file_to_keep_are_kept_as_links_with_a_first_frame_when_one_is_had(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = firecrawl.shop
    monkeypatch.setattr(product_videos, "MAX_VIDEO_BYTES", len(tiny_mp4) - 1)
    monkeypatch.setattr(product_videos, "YOUTUBE_THUMBNAIL", f"{shop}/youtube/{{id}}/hqdefault.jpg")
    httpserver.expect_request("/youtube/abcDEF12345/hqdefault.jpg").respond_with_data(
        POSTER, content_type="image/jpeg"
    )
    httpserver.expect_request("/cdn/videos/big.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    httpserver.expect_request("/cdn/videos/big-poster.jpg").respond_with_data(
        POSTER, content_type="image/jpeg"
    )
    httpserver.expect_request("/cdn/videos/gone.mp4").respond_with_data("Not found", status=404)
    httpserver.expect_request("/cdn/videos/stream.m3u8").respond_with_data(
        "#EXTM3U\n", content_type="application/vnd.apple.mpegurl"
    )
    youtube = "https://www.youtube.com/embed/abcDEF12345?autoplay=1"
    picks_videos += [
        player(1, youtube, kind="iframe"),
        # Over the size cap: the poster stands in for its first frame.
        player(2, f"{shop}/cdn/videos/big.mp4", poster=f"{shop}/cdn/videos/big-poster.jpg"),
        player(3, f"{shop}/cdn/videos/gone.mp4"),
        player(4, f"{shop}/cdn/videos/stream.m3u8"),
        player(5, "blob:https://shop.example/1f2e3d"),
        # The same player listed twice is kept once.
        player(6, youtube, kind="iframe"),
    ]

    read(fake_model, say, shampoo_page_url)

    videos = list(Job.objects.get().videos.all())
    assert [(video.position, video.source_url) for video in videos] == [
        (1, youtube),
        (2, f"{shop}/cdn/videos/big.mp4"),
        (3, f"{shop}/cdn/videos/gone.mp4"),
        (4, f"{shop}/cdn/videos/stream.m3u8"),
        (5, "blob:https://shop.example/1f2e3d"),
    ]
    assert all(video.file == "" and video.frames == video.frames_picked == [] for video in videos)
    assert all(video.duration is None for video in videos)
    assert file_store.read(videos[0].first_frame) == POSTER
    assert file_store.read(videos[1].first_frame) == POSTER
    assert [video.first_frame for video in videos[2:]] == ["", "", ""]
    # The photos were kept as ever.
    assert "Kept 1 product photo." in results_of("read_page")[0]


def test_a_page_read_again_keeps_its_videos_once(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
) -> None:
    # The page's photo can't be used, so the page isn't read yet and is read again when asked.
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/gallery.mp4"))
    link = httpserver.url_for("/products/detox-shampoo")

    read(fake_model, say, link)
    (first,) = Job.objects.get().videos.values_list("pk", flat=True)
    read(fake_model, say, link)

    assert len(results_of("read_page")) == 2
    # Only what the job doesn't have is added: the video kept the first time stays as it is.
    (again,) = Job.objects.get().videos.all()
    assert again.pk == first
    assert file_store.read(again.file) == tiny_mp4


def test_a_file_that_isnt_a_video_is_kept_as_a_link(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
) -> None:
    # Says it is a video, and isn't one ffmpeg can read.
    httpserver.expect_request("/cdn/videos/broken.mp4").respond_with_data(
        b"not a video at all", content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/broken.mp4"))

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert (video.file, video.first_frame, video.frames, video.duration) == ("", "", [], None)
    assert video.frames_picked == []


def test_a_job_with_no_picked_videos_keeps_none(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    assert not ProductVideo.objects.exists()


def test_a_video_whose_link_cant_be_stored_is_left_out(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
) -> None:
    picks_videos += [
        player(1, "data:video/mp4;base64,AAAA"),
        player(2, f"{firecrawl.shop}/cdn/videos/{'a' * 2000}.mp4"),
        player(3, "blob:https://shop.example/1234"),
    ]

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    assert [video.source_url for video in ProductVideo.objects.all()] == [
        "blob:https://shop.example/1234"
    ]


def test_frames_never_reach_past_the_file(tmp_path: Path) -> None:
    assert product_videos.frame_times(2.0) == [0.25, 0.75, 1.25, 1.75]
    assert product_videos.first_frame_time(2.0) == 0.5
    assert product_videos.first_frame_time(0.4) == 0.2


def test_a_kept_video_is_thinned_to_its_picked_frames_with_every_shot_in_them(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    three_shot_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 24 s at 5 frames a second is 120 frames: more than the most kept, so they are shared.
    monkeypatch.setattr(video_frames, "MOST_FRAMES", 30)
    httpserver.expect_request("/cdn/videos/shots.mp4").respond_with_data(
        three_shot_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/shots.mp4"))

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    seconds = [frame["second"] for frame in video.frames_picked]
    assert 0 < len(seconds) <= 30
    assert seconds == sorted(seconds)
    assert all(second * 5 == pytest.approx(round(second * 5)) for second in seconds)
    zoom, still, pattern = ([s for s in seconds if start <= s < start + 8] for start in (0, 8, 16))
    # Each shot has one frame; the other 27 are shared by the moving shots' lengths, 8 s
    # each, so the busy pattern, whose frames are never copies, keeps 1 + 14.
    assert len(still) == 1
    assert len(pattern) == 15
    assert 1 < len(zoom) <= 14
    assert all(is_jpeg_picture(frame["key"]) for frame in video.frames_picked)
    # Full size, beside the video's other frames.
    with PIL.Image.open(io.BytesIO(file_store.read(video.frames_picked[0]["key"]))) as shown:
        assert shown.size == (160, 120)
    assert video.frames_picked[0]["key"] == f"{video.file.rsplit('.', 1)[0]}-pick-1.jpg"


def test_a_short_video_keeps_every_frame_but_its_copies(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    changing_mp4: bytes,
) -> None:
    httpserver.expect_request("/cdn/videos/short.mp4").respond_with_data(
        changing_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/short.mp4"))

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    seconds = [frame["second"] for frame in video.frames_picked]
    # Every new picture, then the held picture once.
    assert seconds[:10] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8]
    assert len(seconds) == 11 and 2.0 <= seconds[10] < 3.0
