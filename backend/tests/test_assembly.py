"""Putting the finished ad together with ffmpeg, called directly on tiny generated clips
and music: what the chat can't easily set up, such as music that runs out before the ad."""

import subprocess
from pathlib import Path

import pytest
from django.conf import settings

from jobs import assembly

from .conftest import loudness

# The fake's pitches: a test hears the voice and the music apart by them.
VOICE_HERTZ = 440
MUSIC_HERTZ = 110


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
    return assembly.Cut(scene=1, clip=1, clip_start=0.0, clip_end=seconds, start=0.0, end=seconds)


def test_music_that_runs_out_before_the_ad_ends_fades_out_rather_than_stopping_dead(
    tmp_path: Path,
) -> None:
    parts = [(clip(tmp_path, "scene-1", seconds=9), one_scene(9.0))]
    ad = tmp_path / "ad.mp4"

    assembly.join(parts, music(tmp_path, seconds=5), ad)

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
