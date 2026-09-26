"""Putting the finished ad together: each scene's clip is cut to where its words are said,
so there is no silence between scenes, and the parts are joined with ffmpeg, with the music
under the voice, captions of the words as they were spoken along the bottom, and each
scene's overlay along the top while it plays."""

import json
import math
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

# How many words a caption shows at once, at most: few enough to read at a glance.
CAPTION_WORDS = 3

# The captions and overlays are laid out for a frame this size, and ffmpeg scales them to
# the clips'. They keep to bands along the top and bottom: the face and the product are in
# the middle, and the starting picture is asked for with room above the head.
FRAME_WIDTH, FRAME_HEIGHT = 1080, 1920

# Assembly re-encodes the whole ad, which takes a while for a long one at full size.
TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Cut:
    """The part of a scene's clip the ad keeps, where it plays in the ad, in seconds, and
    the scene's overlay, drawn while it plays: blank for none."""

    scene: int
    clip: int
    clip_start: float
    clip_end: float
    start: float
    end: float
    overlay: str


@dataclass(frozen=True)
class Caption:
    """A few words drawn along the bottom of the ad, and when they show, in seconds."""

    text: str
    start: float
    end: float


class AssemblyFailed(Exception):
    """ffmpeg couldn't put the ad together. Says what ffmpeg said."""


def cuts(scenes: Sequence[tuple[int, int, float, list[dict[str, Any]], str]]) -> list[Cut]:
    """Where to cut each scene's clip, and where each part plays once they are joined, from
    each scene's number, its clip's id, how long the clip lasts, the words heard in it, and
    its overlay. Each clip is kept from its first word to its last, with MARGIN_SECONDS
    either side."""
    planned: list[Cut] = []
    for scene, clip, seconds, words, overlay in scenes:
        clip_start = round(max(0.0, words[0]["start"] - MARGIN_SECONDS), 3)
        clip_end = round(min(seconds, words[-1]["end"] + MARGIN_SECONDS), 3)
        start = planned[-1].end if planned else 0.0
        end = round(start + clip_end - clip_start, 3)
        planned.append(Cut(scene, clip, clip_start, clip_end, start, end, overlay))
    return planned


def captions(cuts: Sequence[Cut], words: Sequence[list[dict[str, Any]]]) -> list[Caption]:
    """The ad's captions, from the words heard in each cut's clip: up to CAPTION_WORDS at a
    time, split as evenly as that allows, never running from one scene into the next, and
    timed from where the cut plays in the ad rather than from the start of its clip."""
    drawn: list[Caption] = []
    for cut, heard in zip(cuts, words, strict=True):
        # A word's time is measured from the start of its clip; the ad's clock differs from
        # that by however much of the clip was cut away, and by when the cut plays.
        shift = cut.start - cut.clip_start
        groups = math.ceil(len(heard) / CAPTION_WORDS)
        size, extra = divmod(len(heard), groups)
        at = 0
        for group in range(groups):
            said = heard[at : at + size + (1 if group < extra else 0)]
            at += len(said)
            drawn.append(
                Caption(
                    text=" ".join(word["text"] for word in said),
                    start=round(max(cut.start, said[0]["start"] + shift), 3),
                    end=round(min(cut.end, said[-1]["end"] + shift), 3),
                )
            )
    return drawn


# How the text on the ad is drawn, in the ASS format ffmpeg draws with libass. Both
# styles are white and bold so they read over any picture. A caption is outlined (border
# style 1) and centred along the bottom (alignment 2) above a margin; an overlay sits in a
# half-dark box (border style 3, whose box takes the outline colour) centred along the top
# (alignment 8) below one. Sizes are for a FRAME_WIDTH by FRAME_HEIGHT frame.
TEXT_STYLES = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {FRAME_WIDTH}
PlayResY: {FRAME_HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,\
100,100,0,0,1,5,0,2,60,60,120,1
Style: Overlay,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,-1,0,0,0,\
100,100,0,0,3,18,0,8,60,60,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_text_to_draw(drawn: Sequence[Caption], parts: Sequence[Cut], into: Path) -> None:
    """Write the text on the ad as an ASS file for ffmpeg to draw, at `into`: the captions,
    and each part's overlay for as long as the part plays."""
    lines = [TEXT_STYLES]
    lines += [_shown("Caption", caption.text, caption.start, caption.end) for caption in drawn]
    lines += [
        _shown("Overlay", part.overlay, part.start, part.end) for part in parts if part.overlay
    ]
    into.write_text("".join(lines), encoding="utf-8")


def _shown(style: str, text: str, start: float, end: float) -> str:
    """One piece of text as the ASS format lists it: in `style`, from `start` to `end`."""
    # Braces and backslashes would be read as styling, not shown.
    shown = text.replace("\\", "").replace("{", "(").replace("}", ")")
    return f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},{style},,0,0,0,,{shown}\n"


def _timestamp(seconds: float) -> str:
    """Seconds as the ASS format writes a time: hours, minutes, seconds and centiseconds,
    counted in whole centiseconds so a time never rounds up to 60 seconds."""
    hours, rest = divmod(round(seconds * 100), 360_000)
    minutes, rest = divmod(rest, 6_000)
    whole, centi = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centi:02d}"


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


def join(
    parts: Sequence[tuple[Path, Cut]], into: Path, *, music: Path, drawn: Sequence[Caption]
) -> None:
    """Cut each clip file to its part and join the parts, in order, with the music under
    the voice, the captions drawn along the bottom and each part's overlay along the top,
    into the finished ad at `into`."""
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
    filters.append(f"{joined}concat=n={len(parts)}:v=1:a=1[joined][voice]")
    # ffmpeg reads the text to draw from a file beside the ad, whose path has nothing to
    # escape.
    text = into.parent / "text.ass"
    write_text_to_draw(drawn, [cut for _, cut in parts], text)
    filters.append(f"[joined]subtitles={text}[v]")
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
