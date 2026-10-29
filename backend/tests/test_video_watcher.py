"""The watcher: every page video with a file is watched by the video model, all at once, as
the page is read. It writes the video's moments (one action each, at most the video API's
longest piece), a look frame per product state, the product's colour and what is said. The
moments are kept on the video, the look frames are pulled with ffmpeg into the library, and
the planner keeps getting the transcript and the video pictures as before. Driven through the
chat from the shop owner's link, with the picker's picked players set by the test and the
watcher faked at the gateway; ffmpeg real on tiny files."""

import io
import subprocess
import threading
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest
from django.conf import settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import VideoRequest
from jobs import product_videos
from jobs.library import LookFrame, library_of, watch_of
from jobs.models import Job
from jobs.work import plan_handoff

from .conftest import SHAMPOO_PHOTO_PATH, FakeFirecrawl, paid_for, results_of
from .test_producer_page import notices
from .test_product_videos import picks_videos, player, read, tiny_mp4  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def made_video(
    tmp_path_factory: pytest.TempPathFactory, name: str, source: str, *more: str
) -> bytes:
    made = tmp_path_factory.mktemp("video") / name
    subprocess.run(
        [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", source, *more, str(made)],
        check=True,
    )
    return made.read_bytes()


@pytest.fixture(scope="module")
def tiny_webm(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real three-second WebM, 96 by 64 px, of a moving pattern unlike tiny_mp4's."""
    return made_video(
        tmp_path_factory, "tiny.webm", "mandelbrot=size=96x64:rate=10", "-t", "3", "-c:v", "libvpx"
    )


@pytest.fixture(scope="module")
def long_mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real 24-second video, 32 by 32 px, long enough for an action over 15 s."""
    return made_video(
        tmp_path_factory, "long.mp4", "testsrc=size=32x32:rate=2", "-t", "24", "-pix_fmt", "yuv420p"
    )


def look_frame(second: float, label: str, **fields: str) -> dict[str, Any]:
    return {
        "second": second,
        "kind": "state",
        "side": "front",
        "alone": "yes",
        "colour": "black",
        "target": "",
        "label": label,
        **fields,
    }


def moment(start: float, end: float, happens: str) -> dict[str, Any]:
    return {"start": start, "end": end, "happens": happens, "on_screen": f"Hands. {happens}"}


def watched(**fields: Any) -> dict[str, Any]:
    return {
        "shows_this_product": True,
        "moments": [],
        "look_frames": [],
        "colour": "black",
        "transcript": "",
        **fields,
    }


# The 2 s MP4: two moments, written out of time order, one look frame, words said.
DEMO = watched(
    moments=[moment(1.0, 2.0, "The lid is put back on."), moment(0.0, 1.0, "The lid comes off.")],
    look_frames=[look_frame(0.5, "The bottle, lid off, pump showing.")],
    transcript="Twist the lid off and pump once.",
)
# The 3 s WebM: one moment, one look frame of it in use, nothing said.
HOW_TO = watched(
    moments=[moment(0.0, 3.0, "Two pumps are worked into wet hair.")],
    look_frames=[
        look_frame(2.0, "The shampoo lathered in wet hair.", kind="in-use", side="none", alone="no")
    ],
    colour="green",
)


def serve(httpserver: HTTPServer, path: str, data: bytes, content_type: str) -> None:
    httpserver.expect_request(path).respond_with_data(data, content_type=content_type)


def picture_of(key: str) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as shown:
        shown.load()
        return shown


def test_two_videos_in_different_players_are_watched_at_once_into_moments_and_the_library(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
    tiny_webm: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/demo.mp4", tiny_mp4, "video/mp4")
    serve(httpserver, "/cdn/videos/how-to.webm", tiny_webm, "video/webm")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/demo.mp4"))
    picks_videos.append(
        {**player(2, f"{firecrawl.shop}/cdn/videos/how-to.webm"), "box": 7, "controls": True}
    )
    # Neither answer comes until both videos are being watched: they are watched at once.
    both_asked = threading.Barrier(2, timeout=10)

    def answer(request: VideoRequest[Any]) -> dict[str, Any]:
        both_asked.wait()
        return DEMO if request.handoff.model_dump()["seconds"] < 2.5 else HOW_TO

    fake_model.answer_unscripted("watch_video", answer)

    read(fake_model, say, shampoo_page_url)

    job = Job.objects.get()
    demo, how_to = job.videos.order_by("position")
    assert paid_for().count("watch_video") == 2
    # The old reader and the separate transcription are gone from the flow.
    assert not {"read_video", "transcribe_video"} & set(paid_for())
    # Each video's moments, numbered from 1 in time order, with the watcher's seconds.
    demo_watch, how_to_watch = watch_of(demo), watch_of(how_to)
    assert demo_watch is not None and how_to_watch is not None
    assert [(m.number, m.start, m.end, m.happens) for m in demo_watch.moments] == [
        (1, 0.0, 1.0, "The lid comes off."),
        (2, 1.0, 2.0, "The lid is put back on."),
    ]
    assert [(m.number, m.start, m.end) for m in how_to_watch.moments] == [(1, 0.0, 3.0)]
    assert (demo_watch.colour, how_to_watch.colour) == ("black", "green")
    # What is said is kept as the video's transcript; nothing said is music only.
    assert (demo.transcript, how_to.transcript) == (
        "Twist the lid off and pump once.",
        "music only",
    )
    # Each look frame is in the library with its video and second and the photo's fields,
    # pulled from the video file at its full size.
    frames = [item for item in library_of(job) if isinstance(item.source, LookFrame)]
    assert [
        (item.source.video, item.source.second, item.kind, item.side, item.alone, item.label)
        for item in frames
        if isinstance(item.source, LookFrame)
    ] == [
        (1, 0.5, "state", "front", "yes", "The bottle, lid off, pump showing."),
        (2, 2.0, "in-use", "none", "no", "The shampoo lathered in wet hair."),
    ]
    assert [picture_of(item.file).size for item in frames] == [(64, 96), (96, 64)]
    # The frame at the look frame's own second, not the video's opening.
    pulled = file_store.read(frames[0].file)
    assert [pulled] == product_videos.frames_of(demo, [0.5])
    assert product_videos.frames_of(demo, [0.5]) != product_videos.frames_of(demo, [0.0])
    # The planner still gets both through the old fields: the transcripts and the video
    # pictures, which are the look frames.
    handoff = plan_handoff(job, "", job.photos.count())
    assert handoff.video_transcripts == ["Twist the lid off and pump once.", "music only"]
    pictures = job.photos.exclude(from_video=None).order_by("position")
    assert [(p.from_video_id, p.at_second, p.note) for p in pictures] == [
        (demo.pk, 0.5, "The bottle, lid off, pump showing."),
        (how_to.pk, 2.0, "The shampoo lathered in wet hair."),
    ]
    assert [file_store.read(p.file) for p in pictures] == [
        file_store.read(item.file) for item in frames
    ]


def test_the_watcher_is_handed_the_page_and_the_videos_length(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    serve(httpserver, "/cdn/videos/demo.mp4", tiny_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/demo.mp4"))

    read(fake_model, say, shampoo_page_url)

    (call,) = ModelCall.objects.filter(purpose="watch_video")
    assert call.handoff["product_url"] == Job.objects.get().product_url
    assert "Detox" in call.handoff["page_text"]
    assert call.handoff["seconds"] == pytest.approx(2.0, abs=0.1)
    assert call.video.endswith(".watched-480px.mp4")


@pytest.mark.parametrize(
    "refused",
    [
        watched(moments=[moment(0.0, 20.0, "The whole routine, start to finish.")]),
        watched(moments=[moment(20.0, 30.0, "Past the end of the video.")]),
        watched(moments=[moment(24.0, 24.5, "Starts at the end of the video.")]),
        watched(look_frames=[look_frame(25.5, "Past the end of the video.")]),
    ],
    ids=[
        "a 20 s moment",
        "a moment past the end",
        "a moment starting at the end",
        "a look frame past the end",
    ],
)
def test_an_answer_the_schema_refuses_is_asked_again_once(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    long_mp4: bytes,
    refused: dict[str, Any],
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", long_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    split = watched(
        moments=[moment(0.0, 12.0, "The cap comes off."), moment(12.0, 20.0, "It is used.")]
    )
    fake_model.respond("watch_video", refused, split)

    read(fake_model, say, shampoo_page_url)

    calls = list(ModelCall.objects.filter(purpose="watch_video").order_by("id"))
    assert [call.outcome for call in calls] == ["failed", "succeeded"]
    assert "UnusableReply" in calls[0].error
    (video,) = Job.objects.get().videos.all()
    watch = watch_of(video)
    assert watch is not None
    assert [(m.start, m.end) for m in watch.moments] == [(0.0, 12.0), (12.0, 20.0)]


def test_a_video_the_watcher_cant_answer_for_twice_is_told_in_red_and_the_page_is_read(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    long_mp4: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", long_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    too_long = watched(moments=[moment(0.0, 20.0, "The whole routine.")])
    fake_model.respond("watch_video", too_long, too_long)

    read(fake_model, say, shampoo_page_url)

    assert paid_for().count("watch_video") == 2
    job = Job.objects.get()
    (video,) = job.videos.all()
    assert video.watched is None
    assert video.transcript == ""
    assert job.library.count() == 0
    (result,) = results_of("read_page")
    assert "Kept 1 product photo." in result
    assert "The product's video 1 couldn't be watched (" in result
    assert [notice for notice in notices(api, session_id) if "video" in notice[1]] == [
        (
            "problem",
            "Your page's video 1 couldn't be watched, so the ad is planned without it.",
        )
    ]
    assert any("video 1 couldn't be watched" in warning["text"] for warning in job.warnings)


def test_a_video_that_doesnt_show_this_product_is_thrown_out_with_a_grey_notice(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    serve(httpserver, "/cdn/videos/other.mp4", tiny_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/other.mp4"))
    fake_model.respond(
        "watch_video",
        watched(
            shows_this_product=False,
            moments=[moment(0.0, 1.0, "Another product is poured.")],
            look_frames=[look_frame(0.5, "Another product's bottle.")],
            transcript="Our other range.",
        ),
    )

    read(fake_model, say, shampoo_page_url)

    job = Job.objects.get()
    (video,) = job.videos.all()
    assert watch_of(video) is None
    assert video.thrown_out
    assert job.library.count() == 0
    assert not job.photos.exclude(from_video=None).exists()
    assert plan_handoff(job, "", 1).video_transcripts == []
    assert [notice for notice in notices(api, session_id) if "video" in notice[1]] == [
        ("info", "Your page's video 1 doesn't show this product, so it is left out of the ad.")
    ]
    assert (
        "The product's video 1 doesn't show this product: left out." in (results_of("read_page")[0])
    )


def test_a_video_over_three_minutes_isnt_watched_and_the_owner_is_told_which(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
    long_mp4: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cap scaled down so a tiny file stands in for a long video: 3 minutes is 180 s.
    assert settings.WATCH_VIDEO_MOST_SECONDS == 180
    monkeypatch.setattr(settings, "WATCH_VIDEO_MOST_SECONDS", 10)
    serve(httpserver, "/cdn/videos/short.mp4", tiny_mp4, "video/mp4")
    serve(httpserver, "/cdn/videos/film.mp4", long_mp4, "video/mp4")
    film = f"{firecrawl.shop}/cdn/videos/film.mp4"
    picks_videos += [player(1, f"{firecrawl.shop}/cdn/videos/short.mp4"), player(2, film)]

    read(fake_model, say, shampoo_page_url)

    assert paid_for().count("watch_video") == 1
    short, long = Job.objects.get().videos.order_by("position")
    assert watch_of(short) is not None
    assert long.watched is None
    assert long.thrown_out
    told = [notice for notice in notices(api, session_id) if "video" in notice[1]]
    assert told == [
        (
            "info",
            f"Your page's video 2 ({film}) is 24 s long, over the 10 s the app watches, so it "
            "is left out of the ad.",
        )
    ]
    assert "The product's video 2 is 24 s long, over the 10 s" in results_of("read_page")[0]


def test_a_page_read_again_doesnt_watch_its_videos_again(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
    tiny_webm: bytes,
) -> None:
    # The page's photo can't be used, so the page isn't kept and is read again when asked.
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    serve(httpserver, "/cdn/videos/demo.mp4", tiny_mp4, "video/mp4")
    serve(httpserver, "/cdn/videos/other.webm", tiny_webm, "video/webm")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/demo.mp4"))
    picks_videos.append(player(2, f"{firecrawl.shop}/cdn/videos/other.webm"))
    # The 2 s video is watched; the 3 s one shows another product and is thrown out.
    fake_model.answer_unscripted(
        "watch_video",
        lambda request: (
            DEMO
            if request.handoff.model_dump()["seconds"] < 2.5
            else watched(shows_this_product=False)
        ),
    )
    link = httpserver.url_for("/products/detox-shampoo")

    read(fake_model, say, link, times=2)

    assert paid_for().count("watch_video") == 2
    job = Job.objects.get()
    assert job.library.count() == 1
    assert job.photos.exclude(from_video=None).count() == 1


def test_a_look_frame_and_a_moment_at_the_videos_last_second_are_kept_at_its_end(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    serve(httpserver, "/cdn/videos/demo.mp4", tiny_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/demo.mp4"))
    # Watched a frame a second, the 2 s video's last moment and frame are given as second 3
    # and second 2, where no frame is left to pull.
    fake_model.respond(
        "watch_video",
        watched(
            moments=[moment(1.0, 3.0, "The serum is smoothed in.")],
            look_frames=[look_frame(2.0, "The result on the skin.", kind="result", side="none")],
        ),
    )

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    job = Job.objects.get()
    (video,) = job.videos.all()
    watch = watch_of(video)
    assert watch is not None
    duration = video.duration or 0
    assert [(m.start, m.end) for m in watch.moments] == [(1.0, duration)]
    (item,) = library_of(job)
    assert isinstance(item.source, LookFrame)
    # Pulled half a second before the end, where the last frames are.
    assert item.source.second == pytest.approx(duration - 0.5)
    assert picture_of(item.file).size == (64, 96)


def test_a_watched_video_whose_keeping_fails_keeps_nothing_and_is_told_in_red(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    serve(httpserver, "/cdn/videos/demo.mp4", tiny_mp4, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/demo.mp4"))
    fake_model.respond("watch_video", DEMO)
    # The video picture's own face note fails, after its look frame entered the library.
    fake_model.respond("note_face", RuntimeError("the face note failed"))

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    job = Job.objects.get()
    (video,) = job.videos.all()
    # Nothing kept, so the next read of the page watches it again.
    assert video.watched is None and video.transcript == ""
    assert job.library.count() == 0
    assert not job.photos.exclude(from_video=None).exists()
    assert (
        "problem",
        "Your page's video 1 couldn't be watched, so the ad is planned without it.",
    ) in notices(api, session_id)
