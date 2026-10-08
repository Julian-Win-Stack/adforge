"""Putting the finished ad together: each scene's clip is cut to where its words are said,
so there is no silence between scenes, and the parts are joined with ffmpeg, with the music
under the voice, captions of the script's words, timed as they were spoken, along the
bottom, and each scene's overlay along the top while it plays. A clip made with no sound
gets its voice laid over it here first.

A clip's picture and its voice are placed apart (the early cut): the voice never stops, each
line starting where the last one ends, while a scene showing the product plays its whole
picture, past the end of its line. The next line is said over that end, and a talking scene
after it skips the start of its picture by as much, so its lips match its words."""

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

# The frame every ad is made at, and the captions and overlays laid out for. Talking clips
# come this size from HeyGen; B-roll clips come at 768p from Boreal-H3 and are scaled up,
# the same 9:16 shape, since ffmpeg won't join clips of different sizes. The text keeps to
# bands along the top and bottom: the face and the product are in the middle, and the
# starting picture is asked for with room above the head.
FRAME_WIDTH, FRAME_HEIGHT = 1080, 1920
# HeyGen's clips are 25 frames a second, and B-roll clips may not be: joined as they come,
# the ad's rate would change partway, which some players and upload sites handle badly.
FRAME_RATE = 25

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
    """The part of a scene's clip's picture the ad keeps and where it plays in the ad, the
    same for its voice, in seconds, and the scene's overlay, drawn while its picture plays:
    blank for none. A picture that plays longer than the part kept holds its last frame; one
    that plays not at all (start and end the same) is said entirely over the scene before,
    its overlay drawn while it is said.
    """

    scene: int
    clip: int
    clip_start: float
    clip_end: float
    start: float
    end: float
    overlay: str
    voice_clip_start: float
    voice_clip_end: float
    voice_start: float
    voice_end: float


@dataclass(frozen=True)
class Caption:
    """A few words drawn along the bottom of the ad, and when they show, in seconds."""

    text: str
    start: float
    end: float


class AssemblyFailed(Exception):
    """ffmpeg couldn't put the ad together. Says what ffmpeg said."""


def cuts(
    scenes: Sequence[tuple[int, int, float, list[dict[str, Any]], str, bool, float]],
) -> list[Cut]:
    """Where to cut each scene's clip, and where its picture and its voice play once they
    are joined, from each scene's number, its clip's id, how long the clip's picture lasts,
    the words heard in it, its overlay, whether it shows the product rather than the person
    talking, and how long its line's audio lasts.

    The voices play one after another with no gap. A talking scene's voice is kept from its
    first word to its last, with MARGIN_SECONDS either side, or NUMBER_MARGIN_SECONDS after
    a last word with a digit in it. A scene showing the product keeps its whole voice, and
    its whole picture, so its motion plays out after the words, from where the last picture
    ended: the next line is said over that end. A talking scene's picture plays alongside
    its voice, so its lips match its words: it skips as much of its start as the pictures
    before it run past their voices, all of it if that is more than it lasts, the rest then
    skipped by the scene after it. A clip whose picture lasts as long as its voice plays
    them together."""
    planned: list[Cut] = []
    # Where the next voice starts in the ad, and the next picture: never before the voice.
    voice_at = picture_at = 0.0
    for scene, clip, seconds, words, overlay, broll, said_for in scenes:
        if broll:
            voice_clip_start, voice_clip_end = 0.0, said_for
            clip_start, clip_end = 0.0, seconds
            voice_end = round(voice_at + said_for, 3)
            # A clip a little shorter than its line holds its last frame to the line's end.
            end = round(max(picture_at + seconds, voice_end), 3)
        else:
            last = words[-1]
            said_a_number = any(character.isdigit() for character in last["text"])
            margin = NUMBER_MARGIN_SECONDS if said_a_number else MARGIN_SECONDS
            voice_clip_start = round(max(0.0, words[0]["start"] - MARGIN_SECONDS), 3)
            voice_clip_end = round(min(seconds, last["end"] + margin), 3)
            voice_end = round(voice_at + voice_clip_end - voice_clip_start, 3)
            behind = round(picture_at - voice_at, 3)
            clip_start = round(min(voice_clip_start + behind, voice_clip_end), 3)
            clip_end = voice_clip_end
            end = max(picture_at, voice_end)
        planned.append(
            Cut(
                scene=scene,
                clip=clip,
                clip_start=clip_start,
                clip_end=clip_end,
                start=picture_at,
                end=end,
                overlay=overlay,
                voice_clip_start=voice_clip_start,
                voice_clip_end=voice_clip_end,
                voice_start=voice_at,
                voice_end=voice_end,
            )
        )
        voice_at, picture_at = voice_end, end
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
    timed from where the cut's voice plays in the ad rather than from the start of its clip:
    captions follow the voice, not the picture."""
    drawn: list[Caption] = []
    for cut, heard in zip(cuts, words, strict=True):
        # A word's time is measured from the start of its clip; the ad's clock differs from
        # that by however much of the clip's voice was cut away, and by when the voice plays.
        shift = cut.voice_start - cut.voice_clip_start
        groups = math.ceil(len(heard) / CAPTION_WORDS)
        size, extra = divmod(len(heard), groups)
        at = 0
        for group in range(groups):
            said = heard[at : at + size + (1 if group < extra else 0)]
            at += len(said)
            drawn.append(
                Caption(
                    text=" ".join(word["text"] for word in said),
                    start=round(max(cut.voice_start, said[0]["start"] + shift), 3),
                    end=round(min(cut.voice_end, said[-1]["end"] + shift), 3),
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
    and each part's overlay for as long as its picture plays, or, for a part whose picture
    plays not at all, while its line is said."""
    lines = [TEXT_STYLES]
    lines += [_shown("Caption", caption.text, caption.start, caption.end) for caption in drawn]
    lines += [
        _shown("Overlay", part.overlay, part.start, part.end)
        if part.end > part.start
        else _shown("Overlay", part.overlay, part.voice_start, part.voice_end)
        for part in parts
        if part.overlay
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
    """Cut each clip file to its part's picture and voice, join the pictures, in order, and
    the voices, in order, apart, as each plays at its own time, and put them together with
    the music under the voice, the captions drawn along the bottom and each part's overlay
    along the top, into the finished ad at `into`. The ad lasts as long as its picture."""
    inputs: list[str] = []
    filters: list[str] = []
    pictures: list[str] = []
    voices: list[str] = []
    for number, (clip, cut) in enumerate(parts):
        inputs += ["-i", str(clip)]
        # Each part's clock starts again from 0, so the parts play one after another, at the
        # one size and rate the ad is made at. Each is cut to the frames between where it
        # starts and ends in the ad, counted from the ad's start, so the pictures never drift
        # from the voices, which are joined apart: its last frame is held for any it lacks.
        frames = round(cut.end * FRAME_RATE) - round(cut.start * FRAME_RATE)
        if frames > 0:
            held = max(0.0, cut.end - cut.start - (cut.clip_end - cut.clip_start))
            filters.append(
                f"[{number}:v]trim=start={cut.clip_start}:end={cut.clip_end},"
                f"setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={round(held + 2 / FRAME_RATE, 3)},"
                f"scale={FRAME_WIDTH}:{FRAME_HEIGHT},fps={FRAME_RATE},"
                f"trim=end_frame={frames}[v{number}]"
            )
            pictures.append(f"[v{number}]")
        # A voice whose clip's sound runs out early is made up with silence, so the voices
        # after it still start on time.
        filters.append(
            f"[{number}:a]atrim=start={cut.voice_clip_start}:end={cut.voice_clip_end},"
            f"asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={round(cut.voice_end - cut.voice_start, 3)}[a{number}]"
        )
        voices.append(f"[a{number}]")
    filters.append(f"{''.join(pictures)}concat=n={len(pictures)}:v=1:a=0[joined]")
    filters.append(f"{''.join(voices)}concat=n={len(voices)}:v=0:a=1[said]")
    # ffmpeg reads the text to draw from a file beside the ad, whose path has nothing to
    # escape.
    text = into.parent / "text.ass"
    write_text_to_draw(drawn, [cut for _, cut in parts], text)
    filters.append(f"[joined]subtitles={text}[v]")
    inputs += ["-i", str(music)]
    ad_seconds = parts[-1][1].end
    filters.append(f"[{len(parts)}:a]{music_filters(seconds_of(music), ad_seconds)}[music]")
    # A picture that plays on past the last line plays over the music only.
    filters.append(f"[said]apad=whole_dur={ad_seconds}[voice]")
    # The ad lasts as long as its picture; the levels set above are kept, not evened out.
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


def lay_voice_over(clip: Path, voice: Path, into: Path) -> float:
    """Replace a clip's sound with `voice`, then silence to the clip's end, into the clip at
    `into`: the clip is kept whole. A clip a little shorter than its voice holds its last
    frame until the voice ends. Gives how long the clip at `into` lasts."""
    seconds = max(seconds_of(clip), seconds_of(voice))
    _ffmpeg(
        "-i",
        str(clip),
        "-i",
        str(voice),
        "-filter_complex",
        f"[0:v]tpad=stop_mode=clone:stop_duration={CLIP_SHORT_BY_AT_MOST_SECONDS}[v];[1:a]apad[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        str(seconds),
        *ENCODED,
        str(into),
    )
    return seconds


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
