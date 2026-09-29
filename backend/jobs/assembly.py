"""Putting the finished ad together: each scene's clip is cut to where its words are said,
so there is no silence between scenes, and the parts are joined with ffmpeg, with the music
under the voice, captions of the script's words, timed as they were spoken, along the
bottom, and each scene's overlay along the top while it plays. A clip made with no sound
gets its voice laid over it here first."""

import difflib
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

# Kept after a scene's last word instead when it has a digit in it. The transcriber writes a
# number as one token, such as "$60", timed only to the end of its first spoken word, so the
# rest ("dollars", "ninety-nine") is still being said when it ends. A guess at how long that
# takes: the first run lost at most 0.51 seconds of it.
NUMBER_MARGIN_SECONDS = 0.6

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

# How every video made here is encoded.
ENCODED = (
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
)


# A silent clip may come back this much shorter than the voice laid over it, about a frame
# or two, as a video's length is counted in frames: its last frame is held to make it up.
CLIP_SHORT_BY_AT_MOST_SECONDS = 0.1


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


def cuts(
    scenes: Sequence[tuple[int, int, float, list[dict[str, Any]], str, bool]],
) -> list[Cut]:
    """Where to cut each scene's clip, and where each part plays once they are joined, from
    each scene's number, its clip's id, how long the clip lasts, the words heard in it, its
    overlay, and whether it shows the product rather than the person talking. A talking
    scene's clip is kept from its first word to its last, with MARGIN_SECONDS either side,
    or NUMBER_MARGIN_SECONDS after a last word with a digit in it. A scene showing the
    product keeps its whole clip, so its motion plays out after the words."""
    planned: list[Cut] = []
    for scene, clip, seconds, words, overlay, broll in scenes:
        if broll:
            clip_start, clip_end = 0.0, seconds
        else:
            last = words[-1]
            said_a_number = any(character.isdigit() for character in last["text"])
            margin = NUMBER_MARGIN_SECONDS if said_a_number else MARGIN_SECONDS
            clip_start = round(max(0.0, words[0]["start"] - MARGIN_SECONDS), 3)
            clip_end = round(min(seconds, last["end"] + margin), 3)
        start = planned[-1].end if planned else 0.0
        end = round(start + clip_end - clip_start, 3)
        planned.append(Cut(scene, clip, clip_start, clip_end, start, end, overlay))
    return planned


def timed_script(line: str, heard: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The line's own words, as the script writes them, each timed from the words heard: the
    transcriber may write a word differently ("Beard Brand" for "Beardbrand", "$14.99" for
    "fourteen ninety-nine"), but it knows when each was said. The voice is trusted to say
    the line as written."""
    written = line.split()
    matched = difflib.SequenceMatcher(
        None,
        [_plain(word) for word in written],
        [_plain(word["text"]) for word in heard],
        autojunk=False,
    )
    timed: list[dict[str, Any]] = []
    for kind, first, past, heard_first, heard_past in matched.get_opcodes():
        if kind == "equal":
            timed += [
                {"text": word, "start": said["start"], "end": said["end"]}
                for word, said in zip(
                    written[first:past], heard[heard_first:heard_past], strict=True
                )
            ]
        elif kind == "replace":
            # Heard differently: the written words share the time the heard ones took.
            timed += _spread(
                written[first:past], heard[heard_first]["start"], heard[heard_past - 1]["end"]
            )
        elif kind == "delete":
            # Not heard at all: the written words go in the gap between their neighbours.
            gap_start = timed[-1]["end"] if timed else heard[0]["start"]
            gap_end = heard[heard_first]["start"] if heard_first < len(heard) else gap_start
            timed += _spread(written[first:past], gap_start, max(gap_start, gap_end))
        # Words heard that the line doesn't have ("insert") aren't shown.
    return _none_left_untimed(timed)


def _none_left_untimed(timed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`timed`, with any words given no time, such as one not heard between two heard back
    to back, sharing the time of the word before them, or of the word after them at the
    start of the line, so their caption is seen."""
    shared: list[dict[str, Any]] = []
    at = 0
    while at < len(timed):
        past = at
        while past < len(timed) and timed[past]["start"] == timed[past]["end"]:
            past += 1
        if past == at:
            shared.append(timed[at])
            at += 1
            continue
        if shared:
            together = [shared.pop(), *timed[at:past]]
        elif past < len(timed):
            together = [*timed[at:past], timed[past]]
            past += 1
        else:
            # Not one word of the line has any time: there is none to share.
            together = timed[at:past]
        shared += _spread(
            [word["text"] for word in together],
            min(word["start"] for word in together),
            max(word["end"] for word in together),
        )
        at = past
    return shared


def _plain(word: str) -> str:
    """A word as it is compared: lowercased, without punctuation."""
    return "".join(character for character in word.lower() if character.isalnum())


def _spread(words: Sequence[str], start: float, end: float) -> list[dict[str, Any]]:
    """`words` timed one after another from `start` to `end`, each given a share of the time
    as long as the word."""
    lengths = [max(1, len(_plain(word))) for word in words]
    total = sum(lengths)
    timed = []
    for number, word in enumerate(words):
        timed.append(
            {
                "text": word,
                "start": round(start + (end - start) * sum(lengths[:number]) / total, 3),
                "end": round(start + (end - start) * sum(lengths[: number + 1]) / total, 3),
            }
        )
    return timed


def captions(cuts: Sequence[Cut], words: Sequence[list[dict[str, Any]]]) -> list[Caption]:
    """The ad's captions, from the timed words of each cut's clip: up to CAPTION_WORDS at a
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
    _ffmpeg(
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "[a]",
        *ENCODED,
        str(into),
    )


def lay_voice_over(clip: Path, voice: Path, into: Path, *, seconds: float) -> None:
    """Replace a clip's sound with `voice` and cut it to `seconds`, into the clip at `into`.
    A clip a little shorter than that holds its last frame to the end."""
    _ffmpeg(
        "-i",
        str(clip),
        "-i",
        str(voice),
        "-filter_complex",
        f"[0:v]tpad=stop_mode=clone:stop_duration={CLIP_SHORT_BY_AT_MOST_SECONDS}[v]",
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-t",
        str(seconds),
        *ENCODED,
        str(into),
    )


def _ffmpeg(*arguments: str) -> None:
    """Run ffmpeg with `arguments`. Raises AssemblyFailed, saying what ffmpeg said, if it
    fails."""
    ffmpeg = subprocess.run(
        [settings.FFMPEG, "-hide_banner", "-loglevel", "error", *arguments],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if ffmpeg.returncode != 0:
        raise AssemblyFailed(ffmpeg.stderr.strip()[-2000:])
