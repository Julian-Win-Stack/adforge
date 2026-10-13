"""The tests' ffmpeg replay: a run is replayed only when every file it can read is the same."""

from pathlib import Path

import pytest
from django.conf import settings
from pytest_django import Settings

from . import ffmpeg_replay

pytestmark = pytest.mark.real_ffmpeg


@pytest.fixture
def ffmpeg(tmp_path: Path, settings: Settings) -> Path:
    """A stand-in ffmpeg that writes its input and the file a filter names into its output,
    and counts its runs."""
    runs = tmp_path / "runs"
    script = tmp_path / "ffmpeg"
    script.write_text(
        f'#!/bin/sh\necho run >> "{runs}"\ncat "$1" > "$4"\ncat "${{3#subtitles=}}" >> "$4"\n'
    )
    script.chmod(0o755)
    settings.FFMPEG = str(script)
    return runs


def made(folder: Path, clip: bytes, subtitles: bytes) -> bytes:
    folder.mkdir(exist_ok=True)
    (folder / "clip.mp4").write_bytes(clip)
    (folder / "line.ass").write_bytes(subtitles)
    out = folder / "out.mp4"
    ran = ffmpeg_replay.run(
        [
            settings.FFMPEG,
            str(folder / "clip.mp4"),
            "-vf",
            f"subtitles={folder / 'line.ass'}",
            str(out),
        ],
        capture_output=True,
    )
    assert ran.returncode == 0
    return out.read_bytes()


def runs(counted: Path) -> int:
    return len(counted.read_text().split())


def test_the_same_inputs_in_another_folder_are_replayed(tmp_path: Path, ffmpeg: Path) -> None:
    assert made(tmp_path / "a", b"red ", b"hi") == b"red hi"
    assert made(tmp_path / "b", b"red ", b"hi") == b"red hi"
    assert runs(ffmpeg) == 1


@pytest.mark.parametrize(
    ("clip", "subtitles"), [(b"blue ", b"hi"), (b"red ", b"bye")], ids=["input", "named file"]
)
def test_a_changed_file_runs_ffmpeg_again(
    tmp_path: Path, ffmpeg: Path, clip: bytes, subtitles: bytes
) -> None:
    made(tmp_path / "a", b"red ", b"hi")
    assert made(tmp_path / "a", clip, subtitles) == clip + subtitles
    assert runs(ffmpeg) == 2


def test_a_failed_run_is_never_replayed(tmp_path: Path, settings: Settings) -> None:
    settings.FFMPEG = "false"
    for _ in range(2):
        ran = ffmpeg_replay.run([settings.FFMPEG, str(tmp_path)], capture_output=True)
        assert ran.returncode == 1
