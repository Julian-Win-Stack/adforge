"""Product videos: the gallery videos the photo picker picks off the page are kept with the
job, as files with a few frames, or as links with a first frame when no file can be had.
Through the chat, with the picker's picked players set by the test."""

import dataclasses
import io
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import PIL.Image
import pytest
import yt_dlp
from django.conf import settings
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from adforge import file_store
from gateway.fake import FakeModel, turn
from jobs import product_videos, work
from jobs.models import Job, ProductVideo
from jobs.work import PageRead, Picked

from .conftest import (
    PUBLIC_ADDRESS,
    READABLE,
    SHAMPOO_PHOTO_PATH,
    FakeDns,
    FakeFirecrawl,
    picture,
    results_of,
)

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


def warnings() -> list[str]:
    """What the job's notices said, oldest first."""
    return [warning["text"] for warning in Job.objects.get().warnings]


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
    monkeypatch.setattr(product_videos, "NEVER_ENDING_BYTES", len(tiny_mp4) - 1)
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
        # Never ending: the poster stands in for its first frame.
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
    assert all(video.file == "" and video.frames == [] for video in videos)
    assert all(video.duration is None for video in videos)
    assert file_store.read(videos[0].first_frame) == POSTER
    assert file_store.read(videos[1].first_frame) == POSTER
    assert [video.first_frame for video in videos[2:]] == ["", "", ""]
    # The photos were kept as ever.
    assert "Kept 1 product photo." in results_of("read_page")[0]
    # The YouTube player and the one left out for never ending are told; a dead link or a
    # playlist isn't a video.
    told = [text for text in warnings() if "video" in text]
    assert told == [
        f"Your page's video 1 ({youtube}) is a YouTube player, which isn't downloaded, so it "
        "is kept as its link and thumbnail.",
        f"Your page's video 2 ({shop}/cdn/videos/big.mp4) couldn't be fetched whole, so it is "
        f"left out of the ad: {shop}/cdn/videos/big.mp4 is too big to be a product video, at "
        f"over {len(tiny_mp4) - 1:,} bytes.",
    ]


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


def test_a_video_that_takes_too_long_to_fetch_is_left_out_and_the_owner_is_told(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_videos, "NEVER_ENDING_SECONDS", 0.5)

    def trickle(_: Request) -> Response:
        def chunks() -> Iterator[bytes]:
            # About 3 s in all, well past the half second allowed.
            for start in range(0, len(tiny_mp4), 200):
                time.sleep(0.1)
                yield tiny_mp4[start : start + 200]

        return Response(chunks(), content_type="video/mp4")

    httpserver.expect_request("/cdn/videos/live.mp4").respond_with_handler(trickle)
    live = f"{firecrawl.shop}/cdn/videos/live.mp4"
    picks_videos.append(player(1, live))

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert (video.source_url, video.file) == (live, "")
    assert [text for text in warnings() if "video" in text] == [
        f"Your page's video 1 ({live}) couldn't be fetched whole, so it is left out of the ad: "
        f"{live} took over 0.5 s to fetch as a product video."
    ]
    assert "Kept 1 product photo." in results_of("read_page")[0]


def test_a_pages_videos_are_fetched_at_the_same_time(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
) -> None:
    # Neither video is sent until both are asked for.
    both_asked = threading.Barrier(2, timeout=10)

    def video(_: Request) -> Response:
        both_asked.wait()
        return Response(tiny_mp4, content_type="video/mp4")

    for name in ("one", "two"):
        httpserver.expect_request(f"/cdn/videos/{name}.mp4").respond_with_handler(video)
        picks_videos.append(
            player(len(picks_videos) + 1, f"{firecrawl.shop}/cdn/videos/{name}.mp4")
        )

    read(fake_model, say, shampoo_page_url)

    videos = Job.objects.get().videos.order_by("position")
    # Fetched one after the other, neither would come and both would be kept as links.
    assert [bool(video.file) for video in videos] == [True, True]
    assert [file_store.read(video.file) for video in videos] == [tiny_mp4, tiny_mp4]


class FakeYtDlp:
    """yt-dlp, as `keep_video` drives it: hands back the video for each link in `serves`,
    and fails as a private video for any other; a link in `trickles` comes a piece every
    tenth of a second for 3 s. Keeps every link it was handed and the options it was
    handed with."""

    def __init__(self, serves: dict[str, bytes], *, trickles: frozenset[str] = frozenset()) -> None:
        self.serves = serves
        self.trickles = trickles
        self.asked: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, options: dict[str, Any]) -> FakeYtDlp._Run:
        return FakeYtDlp._Run(self, options)

    class _Run:
        def __init__(self, fake: FakeYtDlp, options: dict[str, Any]) -> None:
            self.fake, self.options = fake, options

        def __enter__(self) -> FakeYtDlp._Run:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, Any]:
            self.fake.asked.append((url, self.options))
            if url not in self.fake.serves:
                raise yt_dlp.utils.DownloadError("ERROR: [Wistia] abc123: This video is private")
            if url in self.fake.trickles:
                for piece in range(30):
                    time.sleep(0.1)
                    for hook in self.options["progress_hooks"]:
                        hook({"status": "downloading", "downloaded_bytes": piece * 200})
            file = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            file.write_bytes(self.fake.serves[url])
            return {"requested_downloads": [{"filepath": str(file)}]}


def test_an_embedded_player_is_downloaded_unless_it_is_youtube_or_wont_download(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
    dns: FakeDns,
) -> None:
    for host in ("localhost", "player.vimeo.com", "fast.wistia.net"):
        dns.records[host] = PUBLIC_ADDRESS
    shop = firecrawl.shop
    monkeypatch.setattr(product_videos, "YOUTUBE_THUMBNAIL", f"{shop}/youtube/{{id}}/hqdefault.jpg")
    httpserver.expect_request("/youtube/abcDEF12345/hqdefault.jpg").respond_with_data(
        POSTER, content_type="image/jpeg"
    )
    httpserver.expect_request("/wistia-poster.jpg").respond_with_data(
        POSTER, content_type="image/jpeg"
    )
    vimeo = "https://player.vimeo.com/video/134695542?h=1a2b"
    wistia = "https://fast.wistia.net/embed/iframe/abc123"
    youtube = "https://www.youtube.com/embed/abcDEF12345?autoplay=1"
    fake = FakeYtDlp({vimeo: tiny_mp4})
    monkeypatch.setattr(yt_dlp, "YoutubeDL", fake)
    picks_videos += [
        player(1, vimeo, kind="iframe"),
        player(2, wistia, kind="iframe", poster=f"{shop}/wistia-poster.jpg"),
        player(3, youtube, kind="iframe"),
    ]

    read(fake_model, say, shampoo_page_url)

    one, two, three = Job.objects.get().videos.order_by("position")
    # Vimeo: a normal page video, kept with its file, length and frames, and watched.
    assert (one.kind, one.source_url, bool(one.file)) == ("iframe", vimeo, True)
    assert file_store.read(one.file) == tiny_mp4
    assert one.duration == pytest.approx(2.0, abs=0.1)
    assert is_jpeg_picture(one.first_frame)
    assert len(one.frames) == 4
    assert one.watched is not None
    # Wistia wouldn't download: kept as its link and still.
    assert (two.kind, two.source_url, two.file, two.frames) == ("iframe", wistia, "", [])
    assert file_store.read(two.first_frame) == POSTER
    # YouTube is never handed to yt-dlp: its link and thumbnail.
    assert (three.kind, three.source_url, three.file) == ("iframe", youtube, "")
    assert file_store.read(three.first_frame) == POSTER
    assert [url for url, _ in fake.asked] == [vimeo, wistia] or [url for url, _ in fake.asked] == [
        wistia,
        vimeo,
    ]
    # Fetched as the page shows it: from the product page, at most 720p, as an mp4.
    for _, options in fake.asked:
        assert options["http_headers"]["Referer"] == shampoo_page_url
        assert "height<=720" in options["format"]
        assert options["merge_output_format"] == "mp4"
        assert options["max_filesize"] == product_videos.NEVER_ENDING_BYTES
    told = [text for text in warnings() if "video" in text]
    assert sorted(told) == sorted(
        [
            f"Your page's video 2 ({wistia}) couldn't be downloaded from its player, so it is "
            "kept as its link: ERROR: [Wistia] abc123: This video is private",
            f"Your page's video 3 ({youtube}) is a YouTube player, which isn't downloaded, so "
            "it is kept as its link and thumbnail.",
        ]
    )


def test_a_player_that_takes_too_long_to_download_is_kept_as_its_link_and_told(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
    dns: FakeDns,
) -> None:
    for host in ("localhost", "fast.wistia.net"):
        dns.records[host] = PUBLIC_ADDRESS
    monkeypatch.setattr(product_videos, "NEVER_ENDING_SECONDS", 0.5)
    live = "https://fast.wistia.net/embed/iframe/live1"
    monkeypatch.setattr(
        yt_dlp, "YoutubeDL", FakeYtDlp({live: tiny_mp4}, trickles=frozenset({live}))
    )
    picks_videos.append(player(1, live, kind="iframe"))

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert (video.source_url, video.file) == (live, "")
    assert [text for text in warnings() if "video" in text] == [
        f"Your page's video 1 ({live}) couldn't be downloaded from its player, so it is kept "
        f"as its link: {live} took over 0.5 s to download as a product video."
    ]
    assert "Kept 1 product photo." in results_of("read_page")[0]


def test_a_player_at_a_private_address_is_never_downloaded(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],
    tiny_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
    dns: FakeDns,
) -> None:
    dns.records["localhost"] = PUBLIC_ADDRESS
    dns.records["player.inside.example"] = "10.0.0.5"
    inside = "http://player.inside.example/embed/1"
    fake = FakeYtDlp({inside: tiny_mp4})
    monkeypatch.setattr(yt_dlp, "YoutubeDL", fake)
    picks_videos.append(player(1, inside, kind="iframe"))

    read(fake_model, say, shampoo_page_url)

    assert fake.asked == []
    (video,) = Job.objects.get().videos.all()
    assert (video.source_url, video.file) == (inside, "")
    assert [text for text in warnings() if "video" in text] == [
        f"Your page's video 1 ({inside}) couldn't be downloaded from its player, so it is kept "
        f"as its link: {inside} leads to a private network address, which is never fetched."
    ]
