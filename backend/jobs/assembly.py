"""Putting the finished ad together: each scene's clip is cut to where its words are said,
so there is no silence between scenes, and the parts are joined with ffmpeg, with the music
under the voice."""

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

# Kept either side of a scene's words, so the first and last sounds aren't clipped.
MARGIN_SECONDS = 0.1

# How loud the music plays under the voice: a fifth of its own volume, about 14 dB down, so
# the voice is clearly heard over it. Fixed, the same for every ad.
MUSIC_VOLUME = 0.2

# Music that runs out before the ad ends fades out over this long rather than stopping dead.
MUSIC_FADE_SECONDS = 2

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


def seconds_of(media: Path) -> float:
    """How long an audio or video file lasts, as ffprobe reads it."""
    probed = subprocess.run(
        [settings.FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json"]
        + [str(media)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probed.returncode != 0:
        raise AssemblyFailed(probed.stderr.strip()[-2000:])
    return float(json.loads(probed.stdout)["format"]["duration"])


def music_filters(music_seconds: float, ad_seconds: float) -> str:
    """How the music is fitted under the ad: turned down to MUSIC_VOLUME, and cut to the
    ad's length when it is longer, or faded out over its last MUSIC_FADE_SECONDS when it
    runs out before the ad ends."""
    fitted = [f"volume={MUSIC_VOLUME}"]
    if music_seconds > ad_seconds:
        fitted.append(f"atrim=end={ad_seconds}")
    elif music_seconds < ad_seconds:
        fade_from = round(max(0.0, music_seconds - MUSIC_FADE_SECONDS), 3)
        fitted.append(f"afade=t=out:st={fade_from}:d={MUSIC_FADE_SECONDS}")
    return ",".join(fitted)


def join(parts: Sequence[tuple[Path, Cut]], music: Path, into: Path) -> None:
    """Cut each clip file to its part and join the parts, in order, with the music under
    the voice, into the finished ad at `into`."""
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
    filters.append(f"{joined}concat=n={len(parts)}:v=1:a=1[v][voice]")
    inputs += ["-i", str(music)]
    ad_seconds = parts[-1][1].end
    filters.append(f"[{len(parts)}:a]{music_filters(seconds_of(music), ad_seconds)}[music]")
    # The ad lasts as long as the voice; the levels set above are kept, not evened out.
    filters.append("[voice][music]amix=inputs=2:duration=first:normalize=0[a]")
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
