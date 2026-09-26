"""Putting the finished ad together with ffmpeg, called directly on tiny generated clips
and music: what the chat can't easily set up, such as music that runs out before the ad."""

import subprocess
from pathlib import Path

import pytest
from django.conf import settings

from gateway.fake import FakeModel
from jobs import assembly

from .conftest import loudness

# The fake's pitches: a test hears the voice and the music apart by them.
VOICE_HERTZ = FakeModel.VOICE_HERTZ
MUSIC_HERTZ = FakeModel.MUSIC_HERTZ


def generate(into: Path, *inputs: str, seconds: float) -> Path:
    """A real tiny media file made by ffmpeg from generated `inputs`, lasting `seconds`."""
    lavfi = [arg for source in inputs for arg in ("-f", "lavfi", "-i", source)]
    subprocess.run(
        [settings.FFMPEG, "-hide_banner", "-loglevel", "error", *lavfi, "-t", str(seconds)]
        + (["-pix_fmt", "yuv420p"] if into.suffix == ".mp4" else [])
        + [str(into)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return into


def clip(folder: Path, name: str, *, seconds: float, colour: str = "red") -> Path:
    """A clip of one colour whose voice is a steady tone all the way through."""
    return generate(
        folder / f"{name}.mp4",
        f"color=c={colour}:s=72x128:r=25",
        f"sine=frequency={VOICE_HERTZ}:sample_rate=8000",
        seconds=seconds,
    )


def music(folder: Path, *, seconds: float) -> Path:
    """Music that hums steadily for `seconds`."""
    return generate(
        folder / "music.wav", f"sine=frequency={MUSIC_HERTZ}:sample_rate=8000", seconds=seconds
    )


def one_scene(seconds: float) -> assembly.Cut:
    """An ad of one scene whose clip is kept whole."""
    return assembly.Cut(
        scene=1, clip=1, clip_start=0.0, clip_end=seconds, start=0.0, end=seconds, overlay=""
    )


def test_music_that_runs_out_before_the_ad_ends_fades_out_rather_than_stopping_dead(
    tmp_path: Path,
) -> None:
    parts = [(clip(tmp_path, "scene-1", seconds=9), one_scene(9.0))]
    ad = tmp_path / "ad.mp4"

    assembly.join(parts, ad, music=music(tmp_path, seconds=5), drawn=[])

    heard = ad.read_bytes()
    playing = loudness(heard, "music", between=(1, 2))
    fading = loudness(heard, "music", between=(4, 5))
    gone = loudness(heard, "music", between=(6, 9))
    # Full volume, then quieter but still there over its last 2 seconds, then nothing: as
    # near nothing as the voice's tone leaking faintly into the music's band allows.
    assert playing > -40
    assert playing - 20 < fading < playing - 6
    assert gone < playing - 25
    # The voice is untouched: heard as loud at the end as at the start.
    assert loudness(heard, "voice", between=(7, 9)) == pytest.approx(
        loudness(heard, "voice", between=(0, 2)), abs=1
    )


def test_captions_are_timed_from_where_each_scene_plays_in_the_ad() -> None:
    # Scene 2's clip has a second of silence before its words, and is kept from 0.9 seconds
    # in; that part plays from 4.2 seconds into the ad.
    cuts = [
        assembly.Cut(scene=1, clip=1, clip_start=0.0, clip_end=4.2, start=0.0, end=4.2, overlay=""),
        assembly.Cut(scene=2, clip=2, clip_start=0.9, clip_end=3.1, start=4.2, end=6.4, overlay=""),
    ]
    words = [
        [
            {"text": "Meet", "start": 0.1, "end": 0.5},
            {"text": "the", "start": 0.5, "end": 0.8},
            {"text": "mug.", "start": 0.8, "end": 4.1},
        ],
        [
            {"text": "Yours", "start": 1.0, "end": 1.5},
            {"text": "for", "start": 1.5, "end": 2.0},
            {"text": "twenty", "start": 2.0, "end": 2.5},
            {"text": "dollars.", "start": 2.5, "end": 3.0},
        ],
    ]

    assert assembly.captions(cuts, words) == [
        assembly.Caption(text="Meet the mug.", start=0.1, end=4.1),
        # A caption never runs on from one scene into the next: 4 words are split 2 and 2.
        assembly.Caption(text="Yours for", start=4.3, end=5.3),
        assembly.Caption(text="twenty dollars.", start=5.3, end=6.3),
    ]


@pytest.mark.parametrize(
    ("seconds", "written"), [(0.0, "0:00:00.00"), (59.996, "0:01:00.00"), (3661.5, "1:01:01.50")]
)
def test_a_time_is_written_as_the_subtitle_format_reads_it(seconds: float, written: str) -> None:
    assert assembly._timestamp(seconds) == written
