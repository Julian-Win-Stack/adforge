"""The tests' ffmpeg replay: a run is replayed only when every file it can read is the same."""

import os
import tempfile
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
        f'#!/bin/sh\necho run >> "{runs}"\ncat "$1" > "$4"\n'
        f'cat "${{3#subtitles=}}" >> "$4" 2>/dev/null || true\n'
    )
    script.chmod(0o755)
    settings.FFMPEG = str(script)
    return runs


def made(folder: Path, clip: bytes, subtitles: bytes) -> bytes:
    """A clip in `folder` with subtitles from a folder of their own, which only the filter
    names."""
    for each in (folder, subs_of(folder)):
        each.mkdir(exist_ok=True)
    (folder / "clip.mp4").write_bytes(clip)
    (subs_of(folder) / "line.ass").write_bytes(subtitles)
    return made_again(folder)


def subs_of(folder: Path) -> Path:
    return folder.parent / f"{folder.name}-subs"


def made_again(folder: Path) -> bytes:
    out = folder / "out.mp4"
    out.unlink(missing_ok=True)
    ran = ffmpeg_replay.run(
        [
            settings.FFMPEG,
            str(folder / "clip.mp4"),
            "-vf",
            f"subtitles={subs_of(folder) / 'line.ass'}",
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
    assert made(tmp_path / "b", clip, subtitles) == clip + subtitles
    assert runs(ffmpeg) == 2


def test_a_file_rewritten_with_its_size_and_time_kept_runs_ffmpeg_again(
    tmp_path: Path, ffmpeg: Path
) -> None:
    made(tmp_path / "a", b"red ", b"hi")
    clip = tmp_path / "a" / "clip.mp4"
    when = clip.stat().st_mtime_ns
    clip.write_bytes(b"tan ")
    os.utime(clip, ns=(when, when))
    assert made_again(tmp_path / "a") == b"tan hi"
    assert runs(ffmpeg) == 2


def test_a_changed_file_a_list_names_runs_ffmpeg_again(tmp_path: Path, ffmpeg: Path) -> None:
    clips, lists = tmp_path / "clips", tmp_path / "lists"
    for each in (clips, lists):
        each.mkdir()
    listed = lists / "clips.txt"
    listed.write_text(f"file '{clips / 'one.mp4'}'\n")
    for i, clip in enumerate((b"red", b"blue")):
        (clips / "one.mp4").write_bytes(clip)
        out = tmp_path / f"out-{i}"
        out.mkdir()
        ran = ffmpeg_replay.run(
            [settings.FFMPEG, str(listed), "-c", "copy", str(out / "out.mp4")],
            capture_output=True,
        )
        assert ran.returncode == 0
    assert runs(ffmpeg) == 2


def test_a_run_reading_the_shared_temporary_folder_is_never_replayed(
    ffmpeg: Path, settings: Settings
) -> None:
    with tempfile.NamedTemporaryFile() as clip:
        for _ in range(2):
            ffmpeg_replay.run([settings.FFMPEG, clip.name, "-c", "copy", clip.name + ".out"])
            Path(clip.name + ".out").unlink()
    assert runs(ffmpeg) == 2


def test_a_failed_run_is_never_replayed(tmp_path: Path, settings: Settings) -> None:
    settings.FFMPEG = "false"
    for _ in range(2):
        ran = ffmpeg_replay.run([settings.FFMPEG, str(tmp_path)], capture_output=True)
        assert ran.returncode == 1
