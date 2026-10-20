"""Transcripts of product videos: once the page is read, every gallery video kept as a file
has the words said in it written down through the app's speech-to-text, or "music only"
when nothing is said. Through the chat, with the picker's picked players set by the test."""

import io
import subprocess
import wave
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import Job

from .conftest import SHAMPOO_PHOTO_PATH, FakeFirecrawl, results_of
from .test_product_videos import picks_videos, player, read, tiny_mp4  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

GALLERY_MP4 = "/cdn/videos/gallery.mp4"


@pytest.fixture(scope="module")
def mp4_with_sound(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A real two-second video, 64 by 96 px, with a sound track."""
    made = tmp_path_factory.mktemp("video") / "sound.mp4"
    subprocess.run(
        [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x96:rate=25"]
        + ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "2"]
        + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(made)],
        check=True,
    )
    return made.read_bytes()


def gallery_video(
    httpserver: HTTPServer, firecrawl: FakeFirecrawl, players: list[dict[str, Any]], data: bytes
) -> None:
    httpserver.expect_request(GALLERY_MP4).respond_with_data(data, content_type="video/mp4")
    players.append(player(1, f"{firecrawl.shop}{GALLERY_MP4}"))


def test_a_gallery_video_with_speech_gets_its_words_as_its_transcript(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    mp4_with_sound: bytes,
) -> None:
    gallery_video(httpserver, firecrawl, picks_videos, mp4_with_sound)
    fake_model.respond("transcribe_video", {"text": "Two pumps are all you need."})

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert video.transcript == "Two pumps are all you need."


def test_a_gallery_video_where_nothing_is_said_is_music_only_and_its_hearing_is_paid_once(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    mp4_with_sound: bytes,
) -> None:
    gallery_video(httpserver, firecrawl, picks_videos, mp4_with_sound)

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert video.transcript == "music only"
    # The video's sound went to the speech-to-text as a WAV, its seconds billed.
    (heard,) = ModelCall.objects.filter(purpose="transcribe_video")
    assert (heard.model, heard.outcome) == ("scribe_v2", "succeeded")
    with wave.open(io.BytesIO(file_store.read(heard.handoff["audio"]))) as sound:
        assert sound.getnframes() / sound.getframerate() == pytest.approx(2.0, abs=0.1)
    assert heard.audio_seconds == pytest.approx(2.0, abs=0.1)
    assert heard.cost_usd is not None and heard.cost_usd > 0


def test_a_gallery_video_with_no_sound_track_is_music_only_without_a_call(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    gallery_video(httpserver, firecrawl, picks_videos, tiny_mp4)

    read(fake_model, say, shampoo_page_url)

    (video,) = Job.objects.get().videos.all()
    assert video.transcript == "music only"
    assert not ModelCall.objects.filter(purpose="transcribe_video").exists()


def test_a_video_without_a_file_gets_no_transcript_and_no_call(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    httpserver.expect_request("/cdn/videos/gone.mp4").respond_with_data("Not found", status=404)
    picks_videos += [
        player(1, "https://player.vimeo.com/video/123", kind="iframe"),
        player(2, f"{firecrawl.shop}/cdn/videos/gone.mp4"),
    ]

    read(fake_model, say, shampoo_page_url)

    assert [video.transcript for video in Job.objects.get().videos.all()] == ["", ""]
    assert not ModelCall.objects.filter(purpose="transcribe_video").exists()
    # Not tried and failed: never tried.
    assert not [record for record in caplog.records if record.name == "jobs.product_videos"]


def test_a_page_read_again_replaces_its_videos_transcripts(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    mp4_with_sound: bytes,
) -> None:
    # The page's photo can't be used, so the page isn't read yet and is read again when asked.
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    gallery_video(httpserver, firecrawl, picks_videos, mp4_with_sound)
    fake_model.respond(
        "transcribe_video", {"text": "Lathers in seconds."}, {"text": "Rinses clean."}
    )
    link = httpserver.url_for("/products/detox-shampoo")

    read(fake_model, say, link, times=2)

    (video,) = Job.objects.get().videos.all()
    assert video.transcript == "Rinses clean."
    assert ModelCall.objects.filter(purpose="transcribe_video").count() == 2


def test_a_video_whose_sound_cant_be_written_down_leaves_the_page_read(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811
    mp4_with_sound: bytes,
) -> None:
    gallery_video(httpserver, firecrawl, picks_videos, mp4_with_sound)
    fake_model.respond("transcribe_video", RuntimeError("speech-to-text refused the audio"))

    read(fake_model, say, shampoo_page_url)

    assert "Kept 1 product photo." in results_of("read_page")[0]
    (video,) = Job.objects.get().videos.all()
    assert video.transcript == ""
    (heard,) = ModelCall.objects.filter(purpose="transcribe_video")
    assert heard.outcome == "failed"
