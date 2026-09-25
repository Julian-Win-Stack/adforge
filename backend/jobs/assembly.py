"""Putting the finished ad together: each scene's clip is cut to where its words are said,
so there is no silence between scenes, and the parts are joined with ffmpeg."""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

# Kept either side of a scene's words, so the first and last sounds aren't clipped.
MARGIN_SECONDS = 0.1

# Assembly re-encodes the whole ad, which takes a while for a long one at full size.
TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Cut:
    """The part of a scene's clip the ad keeps, and where it plays in the ad, in seconds."""

    scene: int
    clip: int
    clip_start: float
    clip_end: float
    start: float
    end: float


class AssemblyFailed(Exception):
    """ffmpeg couldn't put the ad together. Says what ffmpeg said."""


def cuts(scenes: Sequence[tuple[int, int, float, list[dict[str, Any]]]]) -> list[Cut]:
    """Where to cut each scene's clip, and where each part plays once they are joined, from
    each scene's number, its clip's id, how long the clip lasts, and the words heard in it.
    Each clip is kept from its first word to its last, with MARGIN_SECONDS either side."""
    planned: list[Cut] = []
    for scene, clip, seconds, words in scenes:
        clip_start = round(max(0.0, words[0]["start"] - MARGIN_SECONDS), 3)
        clip_end = round(min(seconds, words[-1]["end"] + MARGIN_SECONDS), 3)
        start = planned[-1].end if planned else 0.0
        end = round(start + clip_end - clip_start, 3)
        planned.append(Cut(scene, clip, clip_start, clip_end, start, end))
    return planned


def join(parts: Sequence[tuple[Path, Cut]], into: Path) -> None:
    """Cut each clip file to its part and join the parts, in order, into the finished ad
    at `into`."""
    inputs: list[str] = []
    filters: list[str] = []
    joined = ""
    for number, (clip, cut) in enumerate(parts):
        inputs += ["-i", str(clip)]
        keep = f"start={cut.clip_start}:end={cut.clip_end}"
        # Each part's clock starts again from 0, so the parts play one after another.
        filters.append(f"[{number}:v]trim={keep},setpts=PTS-STARTPTS[v{number}]")
        filters.append(f"[{number}:a]atrim={keep},asetpts=PTS-STARTPTS[a{number}]")
        joined += f"[v{number}][a{number}]"
    filters.append(f"{joined}concat=n={len(parts)}:v=1:a=1[v][a]")
    ffmpeg = subprocess.run(
        [
            settings.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            # So the browser can start playing it before all of it has arrived.
            "-movflags",
            "+faststart",
            str(into),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if ffmpeg.returncode != 0:
        raise AssemblyFailed(ffmpeg.stderr.strip()[-2000:])
