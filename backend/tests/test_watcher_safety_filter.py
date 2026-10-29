"""Requirement 22 for the watcher: a page video the safety filter refuses is asked again as
it is, then without its sound, then halved in time, each half probed with a cheap one-word
call, the halves that pass kept and the ones that fail halved again down to 5 s, at most 16
probes a video. Each kept part is watched for real, told which seconds of the video it is,
and what was seen is merged in the video's own seconds. Every refusal is told in red, with
what was dropped and why. Driven through the chat from the shop owner's link, with the
watcher faked at the gateway; ffmpeg real on tiny files."""

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import AnswerTookTooLong, BlockedBySafetyFilter, VideoRequest
from jobs.library import LookFrame, library_of, watch_of
from jobs.models import Job
from jobs.work import plan_handoff

from .conftest import FakeFirecrawl, a_video, has_sound, paid_for, results_of
from .test_producer_page import notices
from .test_product_videos import picks_videos, player, read  # noqa: F401
from .test_video_watcher import look_frame, moment, serve, watched

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module")
def half_minute() -> bytes:
    """A real 30-second video, 32 by 32 px, with a tone for its sound."""
    return a_video(32, 32, sound=True, seconds=30)


@pytest.fixture(scope="module")
def minute() -> bytes:
    """A real 60-second video, 32 by 32 px, with a tone for its sound."""
    return a_video(32, 32, sound=True, seconds=60)


def seconds_of(request: VideoRequest[Any]) -> tuple[float, float]:
    """Which seconds of the page's video a watch or a probe was sent."""
    handoff = request.handoff.model_dump()
    part = handoff.get("part")
    return (part["start"], part["end"]) if part else (0.0, handoff["seconds"])


def refusing(
    bad: Callable[[float, float], bool], answer: Callable[[float, float], dict[str, Any]]
) -> tuple[Callable[[Any], dict[str, Any]], Callable[[Any], dict[str, Any]]]:
    """A fake watcher and its probe, refusing any seconds `bad` says, with or without sound;
    a watch it lets through is answered with what `answer` gives for its seconds."""

    def watch(request: VideoRequest[Any]) -> dict[str, Any]:
        start, end = seconds_of(request)
        if bad(start, end):
            raise BlockedBySafetyFilter("The video was blocked: PROHIBITED_CONTENT.")
        return answer(start, end)

    def probe(request: VideoRequest[Any]) -> dict[str, Any]:
        start, end = seconds_of(request)
        if bad(start, end):
            raise BlockedBySafetyFilter("The video was blocked: PROHIBITED_CONTENT.")
        return {"word": "bottle"}

    return watch, probe


def overlapping(*spans: tuple[float, float]) -> Callable[[float, float], bool]:
    return lambda start, end: any(start < to and end > since for since, to in spans)


def length_of(data: bytes) -> float:
    """How many seconds long a video sent to the watcher is, as ffprobe reads it, to 0.1 s."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "sent.mp4"
        file.write_bytes(data)
        probed = subprocess.run(
            [settings.FFPROBE, "-v", "error", "-show_entries", "format=duration"]
            + ["-of", "csv=p=0", str(file)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    return round(float(probed.stdout), 1)


def watcher_calls() -> list[str]:
    """The watcher's paid calls, its watches and its probes, oldest first."""
    return [purpose for purpose in paid_for() if purpose in {"watch_video", "probe_video"}]


def video_notices(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    return [notice for notice in notices(api, session_id) if "video" in notice[1]]


def test_seconds_the_filter_refuses_are_dropped_and_the_rest_is_watched_in_its_own_seconds(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    parts = {
        # The first part's last action runs on past its edge: it ends at the edge.
        (0.0, 10.0): watched(
            moments=[moment(0.0, 4.0, "The cap comes off."), moment(4.0, 10.5, "It is poured.")],
            look_frames=[
                look_frame(2.0, "The bottle, cap off."),
                look_frame(10.4, "The bottle, poured."),
            ],
            colour="black",
            transcript="Twist it open.",
        ),
        (15.0, 30.0): watched(
            moments=[moment(6.0, 15.0, "It is rinsed off."), moment(0.0, 6.0, "It is rubbed in.")],
            look_frames=[
                look_frame(3.0, "The lather in wet hair.", kind="in-use", side="none", alone="no"),
                # Past this part's end, which is the video's: its last frame.
                look_frame(15.4, "The rinsed hair."),
            ],
            colour="green",
            transcript="Rinse well.",
        ),
    }
    watch, probe = refusing(overlapping((10.0, 15.0)), lambda start, end: parts[(start, end)])
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url)

    job = Job.objects.get()
    (video,) = job.videos.all()
    # Asked as it is twice, then without its sound; then 0-15 s refused, 15-30 s passed,
    # 0-10 s passed and 10-15 s, at 5 s, refused and dropped; then the two parts watched.
    assert watcher_calls() == [
        *["watch_video"] * 3,
        *["probe_video"] * 4,
        *["watch_video"] * 2,
    ]
    sent = [seconds_of(request) for request in fake_model.videos_watched]
    assert sent[-2:] == [(0.0, 10.0), (15.0, 30.0)]
    # Each part is sent as its own video, with its sound, told which seconds it is.
    parts_watched = fake_model.videos_watched[-2:]
    assert [request.handoff.model_dump()["seconds"] for request in parts_watched] == [10.0, 15.0]
    assert all(has_sound(request.video.data) for request in parts_watched)
    assert [length_of(request.video.data) for request in parts_watched] == [10.0, 15.0]
    # Each probe is sent its own part of the video too.
    probes = [request for request in fake_model.videos_watched if request.purpose == "probe_video"]
    assert [length_of(request.video.data) for request in probes] == [15.0, 15.0, 10.0, 5.0]
    seen = watch_of(video)
    assert seen is not None
    assert [(m.number, m.start, m.end, m.happens) for m in seen.moments] == [
        (1, 0.0, 4.0, "The cap comes off."),
        (2, 4.0, 10.0, "It is poured."),
        (3, 15.0, 21.0, "It is rubbed in."),
        (4, 21.0, 30.0, "It is rinsed off."),
    ]
    # The look frame the watcher put at the first part's edge would be pulled from the refused
    # seconds after it, which it never saw: it is dropped.
    assert seen.look_frames == [2.0, 18.0, 29.5]
    # The colour of the most seconds; what is said, joined in time order.
    assert seen.colour == "green"
    assert seen.transcript == "Twist it open. Rinse well."
    assert video.transcript == "Twist it open. Rinse well."
    frames = [item.source for item in library_of(job) if isinstance(item.source, LookFrame)]
    assert [(frame.video, frame.second) for frame in frames] == [(1, 2.0), (1, 18.0), (1, 29.5)]
    # The refusal is kept with the watch: why, what was dropped and how many probes it took.
    assert seen.refused is not None
    assert (seen.refused.reason, seen.refused.probes) == ("pictures", 4)
    assert [(span.start, span.end) for span in seen.refused.dropped] == [(10.0, 15.0)]
    assert ModelCall.objects.filter(purpose="probe_video").count() == 4
    told = (
        "The AI model's safety filter refused some of your page's video 1's pictures "
        "(reason: pictures), so seconds 10-15 of it are dropped and the rest was watched."
    )
    assert video_notices(api, session_id) == [("problem", told)]
    assert [warning["text"] for warning in job.warnings if "video" in warning["text"]] == [told]
    assert "4 probes" in results_of("read_page")[0]


def test_a_video_refused_only_for_its_sound_is_kept_without_its_words(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))

    def watch(request: VideoRequest[Any]) -> dict[str, Any]:
        if request.handoff.model_dump()["sound"]:
            raise BlockedBySafetyFilter("The video was blocked: PROHIBITED_CONTENT.")
        return watched(
            moments=[moment(0.0, 12.0, "The cap comes off.")],
            transcript="Words it couldn't have heard.",
        )

    fake_model.answer_unscripted("watch_video", watch)

    read(fake_model, say, shampoo_page_url)

    job = Job.objects.get()
    (video,) = job.videos.all()
    assert watcher_calls() == ["watch_video"] * 3
    # The third watch was of the video with its sound taken out.
    assert [has_sound(request.video.data) for request in fake_model.videos_watched] == [
        True,
        True,
        False,
    ]
    seen = watch_of(video)
    assert seen is not None
    assert [(m.start, m.end) for m in seen.moments] == [(0.0, 12.0)]
    # No transcript: neither the watch's nor "music only", and none for the planner.
    assert seen.transcript == video.transcript == ""
    assert plan_handoff(job, "", job.photos.count()).video_transcripts == []
    assert seen.refused is not None
    assert (seen.refused.reason, seen.refused.dropped, seen.refused.probes) == ("sound", [], 0)
    told = (
        "The AI model's safety filter refused your page's video 1 for its sound (reason: "
        "sound), so it was watched without it: its sound, seconds 0-30, is dropped and nothing "
        "said in it is kept."
    )
    assert video_notices(api, session_id) == [("problem", told)]
    assert [warning["text"] for warning in job.warnings if "video" in warning["text"]] == [told]


def test_a_video_refused_everywhere_down_to_5_s_is_dropped_as_all_of_it(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    watch, probe = refusing(lambda start, end: True, lambda start, end: watched())
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url, times=2)

    # 0-30 s halved to 15 s parts, then to 10 and 5 s parts, then the 5 s parts dropped: ten
    # probes; and the page read again doesn't search it again.
    assert watcher_calls() == ["watch_video"] * 3 + ["probe_video"] * 10
    probed = [
        seconds_of(request)
        for request in fake_model.videos_watched
        if request.purpose == "probe_video"
    ]
    assert probed == [
        (0.0, 15.0),
        (15.0, 30.0),
        (0.0, 10.0),
        (10.0, 15.0),
        (15.0, 25.0),
        (25.0, 30.0),
        (0.0, 5.0),
        (5.0, 10.0),
        (15.0, 20.0),
        (20.0, 25.0),
    ]
    job = Job.objects.get()
    (video,) = job.videos.all()
    assert video.watched is None
    # The record is kept with the video it threw out.
    assert video.thrown_out == (
        "The AI model's safety filter refused all of it: seconds 0-30 dropped, after 10 probes."
    )
    assert video.transcript == ""
    # No look frame entered the library.
    assert not [item for item in library_of(job) if isinstance(item.source, LookFrame)]
    told = (
        "The AI model's safety filter refused all of your page's video 1 (reason: all), so "
        "seconds 0-30 of it are dropped and the ad is planned without it."
    )
    assert video_notices(api, session_id) == [("problem", told)]
    assert [warning["text"] for warning in job.warnings if "video" in warning["text"]] == [told]
    assert "10 probes" in results_of("read_page")[0]


def test_the_search_stops_at_16_probes_and_drops_the_parts_not_probed(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/film.mp4", minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/film.mp4"))
    # Only 5-10 s and 50-55 s pass. 5-10 s is the sixteenth probe; 50-55 s is never probed.
    passing = [(5.0, 10.0), (50.0, 55.0)]
    watch, probe = refusing(
        lambda start, end: not any(since <= start and end <= to for since, to in passing),
        lambda start, end: watched(moments=[moment(1.0, 4.0, "The cap comes off.")]),
    )
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url)

    assert watcher_calls() == ["watch_video"] * 3 + ["probe_video"] * 16 + ["watch_video"]
    job = Job.objects.get()
    (video,) = job.videos.all()
    seen = watch_of(video)
    assert seen is not None
    assert [(m.start, m.end) for m in seen.moments] == [(6.0, 9.0)]
    assert seen.refused is not None
    assert (seen.refused.reason, seen.refused.probes) == ("pictures", 16)
    assert [(span.start, span.end) for span in seen.refused.dropped] == [(0.0, 5.0), (10.0, 60.0)]
    assert video_notices(api, session_id) == [
        (
            "problem",
            "The AI model's safety filter refused some of your page's video 1's pictures "
            "(reason: pictures), so seconds 0-5 and 10-60 of it are dropped and the rest was "
            "watched.",
        )
    ]


def test_both_halves_passing_are_both_watched_and_nothing_is_dropped(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    # Refused only whole: each half on its own passes.
    watch, probe = refusing(
        lambda start, end: end - start > 15,
        lambda start, end: watched(
            moments=[moment(0.0, 15.0, f"Step from {start:g} s.")],
            colour="black" if start == 0 else "green",
        ),
    )
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url)

    assert watcher_calls() == ["watch_video"] * 3 + ["probe_video"] * 2 + ["watch_video"] * 2
    seen = watch_of(Job.objects.get().videos.get())
    assert seen is not None
    assert [(m.start, m.end, m.happens) for m in seen.moments] == [
        (0.0, 15.0, "Step from 0 s."),
        (15.0, 30.0, "Step from 15 s."),
    ]
    # As many seconds of each colour: the first seen.
    assert seen.colour == "black"
    assert seen.refused is not None
    assert (seen.refused.reason, seen.refused.dropped) == ("pictures", [])
    assert video_notices(api, session_id) == [
        (
            "problem",
            "The AI model's safety filter refused your page's video 1 whole (reason: pictures), "
            "then let each part of it through, so nothing of it is dropped.",
        )
    ]


def test_a_refusal_the_second_ask_gets_past_is_still_told_in_red(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    fake_model.respond(
        "watch_video",
        BlockedBySafetyFilter("The video was blocked: OTHER."),
        watched(moments=[moment(0.0, 12.0, "The cap comes off.")], transcript="Open it."),
    )

    read(fake_model, say, shampoo_page_url)

    assert watcher_calls() == ["watch_video"] * 2
    seen = watch_of(Job.objects.get().videos.get())
    assert seen is not None
    assert seen.transcript == "Open it."
    assert seen.refused is not None
    assert seen.refused.reason == "asked again"
    assert video_notices(api, session_id) == [
        (
            "problem",
            "The AI model's safety filter refused your page's video 1 once, then let it "
            "through when it was asked again, so nothing of it is dropped.",
        )
    ]


@pytest.mark.parametrize(
    ("failure", "calls", "kept"),
    [
        (OutsideServiceDown("429 Too Many Requests"), 2, True),
        (AnswerTookTooLong("The watcher took over 600 s."), 1, False),
    ],
    ids=["a rate limit, retried", "a timeout, not retried"],
)
def test_a_rate_limit_or_a_timeout_is_never_a_refusal(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
    failure: Exception,
    calls: int,
    kept: bool,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    fake_model.respond("watch_video", failure, watched())

    read(fake_model, say, shampoo_page_url)

    assert watcher_calls() == ["watch_video"] * calls
    (video,) = Job.objects.get().videos.all()
    assert (watch_of(video) is not None) == kept
    assert not video.thrown_out
    told = video_notices(api, session_id)
    assert not any("safety filter" in text for _, text in told)
    assert told == (
        []
        if kept
        else [
            (
                "problem",
                "Your page's video 1 couldn't be watched, so the ad is planned without it.",
            )
        ]
    )


def test_a_part_refused_when_watched_is_dropped_and_a_probe_answer_that_cant_be_read_passes(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))

    def watch(request: VideoRequest[Any]) -> dict[str, Any]:
        if seconds_of(request) in {(0.0, 30.0), (0.0, 15.0)}:
            raise BlockedBySafetyFilter("The video was blocked: PROHIBITED_CONTENT.")
        return watched(moments=[moment(0.0, 5.0, "It is rinsed off.")])

    # The probe of 0-15 s answers with nothing readable: it wasn't refused.
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted(
        "probe_video", lambda request: {} if seconds_of(request)[0] == 0 else {"word": "bottle"}
    )

    read(fake_model, say, shampoo_page_url)

    assert watcher_calls() == ["watch_video"] * 3 + ["probe_video"] * 2 + ["watch_video"] * 2
    seen = watch_of(Job.objects.get().videos.get())
    assert seen is not None
    assert [(m.start, m.end) for m in seen.moments] == [(15.0, 20.0)]
    assert seen.refused is not None
    assert (seen.refused.reason, seen.refused.probes) == ("pictures", 2)
    assert [(span.start, span.end) for span in seen.refused.dropped] == [(0.0, 15.0)]
    assert [text for _, text in video_notices(api, session_id)] == [
        "The AI model's safety filter refused some of your page's video 1's pictures "
        "(reason: pictures), so seconds 0-15 of it are dropped and the rest was watched."
    ]


def test_a_part_that_doesnt_show_this_product_is_left_out_of_what_is_kept(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    half_minute: bytes,
) -> None:
    serve(httpserver, "/cdn/videos/routine.mp4", half_minute, "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/routine.mp4"))
    watch, probe = refusing(
        lambda start, end: end - start > 15,
        lambda start, end: watched(
            shows_this_product=start > 0,
            moments=[moment(0.0, 15.0, f"Step from {start:g} s.")],
            look_frames=[look_frame(1.0, f"A frame from {start:g} s.")],
            colour="black" if start == 0 else "green",
            transcript=f"Said from {start:g} s.",
        ),
    )
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    seen = watch_of(video)
    assert seen is not None
    assert not video.thrown_out
    assert [(m.start, m.end, m.happens) for m in seen.moments] == [(15.0, 30.0, "Step from 15 s.")]
    assert seen.look_frames == [16.0]
    assert (seen.colour, seen.transcript) == ("green", "Said from 15 s.")


def test_a_refused_part_over_5_s_with_no_whole_5_s_cut_in_it_is_halved_at_its_middle(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
) -> None:
    serve(httpserver, "/cdn/videos/short.mp4", a_video(32, 32, sound=True, seconds=12), "video/mp4")
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/short.mp4"))
    # 0-12 s is cut at 5 s; 5-12 s has no cut at a whole 5 s leaving 2.5 s each side, so it
    # is halved at 8.5 s, and 5-8.5 s, under 5 s, is dropped.
    watch, probe = refusing(
        overlapping((6.0, 7.0)),
        lambda start, end: watched(moments=[moment(0.0, 3.0, f"Step from {start:g} s.")]),
    )
    fake_model.answer_unscripted("watch_video", watch)
    fake_model.answer_unscripted("probe_video", probe)

    read(fake_model, say, shampoo_page_url)

    probed = [
        seconds_of(request)
        for request in fake_model.videos_watched
        if request.purpose == "probe_video"
    ]
    assert probed == [(0.0, 5.0), (5.0, 12.0), (5.0, 8.5), (8.5, 12.0)]
    seen = watch_of(Job.objects.get().videos.get())
    assert seen is not None
    assert [(m.start, m.end) for m in seen.moments] == [(0.0, 3.0), (8.5, 11.5)]
    assert seen.refused is not None
    assert [(span.start, span.end) for span in seen.refused.dropped] == [(5.0, 8.5)]
