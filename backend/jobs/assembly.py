"""Join scene clips with their own sound, written captions, overlays and music, each cut
short of the silence before the ad's first word and after a clip's last."""

import difflib
import io
import json
import math
import subprocess
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import PIL.Image
from django.conf import settings

# How loud the music plays under the voice: a fifth of its own volume, about 14 dB down, so
# the voice is clearly heard over it. Fixed, the same for every ad.
MUSIC_VOLUME = 0.2

# Music that runs out before the ad ends fades out over this long rather than stopping dead.
MUSIC_FADE_SECONDS = 2

# How many words a caption shows at once, at most: few enough to read at a glance.
CAPTION_WORDS = 3

# Shared portrait frame and playback rate for every stored and newly made clip.
FRAME_WIDTH, FRAME_HEIGHT = 1080, 1920
FRAME_RATE = 25

# The part of the frame TikTok and Reels leave uncovered (spec #401): their buttons and text
# cover the bottom 380 px and the top 220 px. Captions sit above the one and the overlay
# below the other, and the end card fits between them.
SAFE_TOP = 220
SAFE_BOTTOM = FRAME_HEIGHT - 380

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


@dataclass(frozen=True)
class EndCard:
    """The card the ad ends on, after its last scene (spec #401): the product's name, one
    line in the page's own words, its price (blank for none) and its website, drawn under
    the product photo from `start` to `end`, in seconds, over the music alone."""

    name: str
    line: str
    price: str
    website: str
    start: float
    end: float


@dataclass(frozen=True)
class Placed:
    """One row of the end card's text: its words, size in px, whether bold, and the y of
    its top edge, centred across the frame."""

    text: str
    size: int
    bold: bool
    top: int

    @property
    def bottom(self) -> int:
        return self.top + round(self.size * ROW_HEIGHT)


# The end card is laid out from the bottom of the safe area up: the website, the price, the
# line and the name, each wrapped to fit the frame's width, and the photo as large as fits
# in what is left above them. Sizes are for a FRAME_WIDTH by FRAME_HEIGHT frame. A row's
# width is guessed from its characters at CHARACTER_WIDTH of its size each, a little over a
# DejaVu Sans Bold letter's average width.
END_CARD_PADDING = 40
END_CARD_GAP = 24
END_CARD_TEXT_WIDTH = FRAME_WIDTH - 2 * 60
CHARACTER_WIDTH = 0.66
ROW_HEIGHT = 1.25
# Each part of the text, top to bottom: its size, whether bold, and most rows.
NAME_TEXT = (64, True, 2)
LINE_TEXT = (50, False, 3)
PRICE_TEXT = (60, True, 1)
WEBSITE_TEXT = (44, False, 1)
# A part whose words need more rows than it has is drawn smaller, never below this size.
SMALLEST_TEXT = 28


def end_card_after(cuts: Sequence[Cut], seconds: float, **text: str) -> EndCard:
    """The end card, played for `seconds` after the last of `cuts`, with `text` (its name,
    line, price and website), each written on one line."""
    start = cuts[-1].end
    return EndCard(
        **{part: " ".join(words.split()) for part, words in text.items()},
        start=start,
        end=round(start + seconds, 3),
    )


def flat_photo(data: bytes) -> bytes:
    """A product photo as the end card draws it: its first frame as a PNG with no
    transparency, see-through parts white like the card, which every photo format can be
    turned into and ffmpeg loops alike (it can't loop a GIF). Raises ValueError for a file
    that isn't a picture."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as opened:
            picture = opened.convert("RGBA")
    except (OSError, PIL.Image.DecompressionBombError, ValueError) as unreadable:
        raise ValueError(f"the photo can't be read as a picture: {unreadable}") from unreadable
    flat = PIL.Image.new("RGB", picture.size, (255, 255, 255))
    flat.paste(picture, mask=picture.getchannel("A"))
    written = io.BytesIO()
    flat.save(written, format="PNG")
    return written.getvalue()


def website_of(url: str) -> str:
    """The website a product's link is on, as an ad writes it: its host, without "www."."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def end_card_text(card: EndCard) -> list[Placed]:
    """The end card's rows of text, top to bottom, inside the safe area: its name, line,
    price (when it has one) and website, each part wrapped to the frame's width."""
    parts = [
        (card.name, NAME_TEXT),
        (card.line, LINE_TEXT),
        (card.price, PRICE_TEXT),
        (card.website, WEBSITE_TEXT),
    ]
    stacks = [_wrapped(text, *how) for text, how in parts if text.strip()]
    placed: list[Placed] = []
    bottom = SAFE_BOTTOM - END_CARD_PADDING
    for rows, size, bold in reversed(stacks):
        height = round(size * ROW_HEIGHT)
        top = bottom - height * len(rows)
        placed = [
            Placed(row, size, bold, top + height * number) for number, row in enumerate(rows)
        ] + placed
        bottom = top - END_CARD_GAP
    return placed


def end_card_photo_box(card: EndCard) -> tuple[int, int]:
    """The top and height of the box the end card's photo is fitted into: the safe area
    above its text, the full width less margins."""
    rows = end_card_text(card)
    top = SAFE_TOP + END_CARD_PADDING
    bottom = (rows[0].top if rows else SAFE_BOTTOM - END_CARD_PADDING) - END_CARD_GAP
    # Never empty, so ffmpeg can always fit the photo into it.
    return top, max(2, bottom - top)


def _wrapped(text: str, size: int, bold: bool, most_rows: int) -> tuple[list[str], int, bool]:
    """`text` in rows that fit END_CARD_TEXT_WIDTH at `size`, at most `most_rows` of them:
    smaller, down to SMALLEST_TEXT, when the words need more rows or a word is too long for
    one."""
    while True:
        across = max(1, math.floor(END_CARD_TEXT_WIDTH / (size * CHARACTER_WIDTH)))
        rows = textwrap.wrap(text, across, break_long_words=size <= SMALLEST_TEXT)
        fits = len(rows) <= most_rows and all(len(row) <= across for row in rows)
        if fits or size <= SMALLEST_TEXT:
            return rows, size, bold
        size -= 2


class AssemblyFailed(Exception):
    """ffmpeg couldn't put the ad together. Says what ffmpeg said."""


class AssemblyTimedOut(AssemblyFailed):
    """ffmpeg or ffprobe took too long: a later try may finish."""


# The ad's first word is said within this many seconds of its start: a longer silence at the
# start of the first clip is cut, leaving OPENING_BREATH_SECONDS before the word (spec #401).
FIRST_WORD_WITHIN_SECONDS = 0.3
OPENING_BREATH_SECONDS = 0.05


def opening_silence(first_word_start: float | None, clip_seconds: float) -> float:
    """How much of the first clip's start to cut so the ad's first word comes within
    FIRST_WORD_WITHIN_SECONDS: none when it already does, or when no word time is known."""
    if first_word_start is None or not math.isfinite(first_word_start):
        return 0.0
    if first_word_start <= FIRST_WORD_WITHIN_SECONDS or first_word_start >= clip_seconds:
        return 0.0
    return round(first_word_start - OPENING_BREATH_SECONDS, 3)


# A clip plays at most this long after its last word, picture and sound alike: longer is dead
# air, a silent stall after she's done talking (#442; round 12's AeroPress scene 3 held 1.6 s).
# Every clip Julian passed in rounds 11 and 12 kept 0.45 s or less, so they play as before.
AFTER_LAST_WORD_SECONDS = 0.5


def closing_silence_end(last_word_end: float | None, clip_seconds: float) -> float:
    """Where a clip's kept part ends: AFTER_LAST_WORD_SECONDS after its last word, or at the
    clip's end when that comes sooner or no usable word time is known."""
    if last_word_end is None or not math.isfinite(last_word_end):
        return clip_seconds
    if last_word_end <= 0 or last_word_end >= clip_seconds:
        return clip_seconds
    return round(min(clip_seconds, last_word_end + AFTER_LAST_WORD_SECONDS), 3)


def last_word_end(words: Sequence[Mapping[str, Any]]) -> float | None:
    """When a transcript's last word ends, in seconds from its clip's start: None when it has
    no words or the last has no number for its end."""
    end = words[-1].get("end") if words else None
    return float(end) if isinstance(end, (int, float)) and not isinstance(end, bool) else None


def usable_last_word(end: float | None, clip_seconds: float) -> bool:
    """Whether a last word's end is a time inside its clip, give or take the frame or two a
    clip's length is counted short by."""
    return (
        end is not None
        and math.isfinite(end)
        and 0 < end <= clip_seconds + CLIP_SHORT_BY_AT_MOST_SECONDS
    )


def planned_cuts(
    scenes: Sequence[tuple[int, int, float, str]],
    *,
    opening_cut: float = 0.0,
    last_words: Sequence[float | None] = (),
) -> list[Cut]:
    """Each scene's number, clip, duration and overlay, played with its own sound one after
    another: the first clip from `opening_cut` seconds in, and each clip up to
    AFTER_LAST_WORD_SECONDS after its last word, from `last_words` (the end of each clip's
    last word, None when unknown), picture and sound alike. A clip with no word time plays to
    its end."""
    planned = []
    start = 0.0
    for number, (scene, clip, seconds, overlay) in enumerate(scenes):
        cut_from = opening_cut if number == 0 else 0.0
        last_word = last_words[number] if number < len(last_words) else None
        cut_to = closing_silence_end(last_word, seconds)
        end = round(start + cut_to - cut_from, 3)
        planned.append(
            Cut(
                scene=scene,
                clip=clip,
                clip_start=cut_from,
                clip_end=cut_to,
                start=start,
                end=end,
                overlay=overlay,
                voice_clip_start=cut_from,
                voice_clip_end=cut_to,
                voice_start=start,
                voice_end=end,
            )
        )
        start = end
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
        if not heard:
            continue
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


# How far captions sit above the bottom of the frame and the overlay below its top: inside
# the safe area, with a little room to spare.
CAPTION_MARGIN = FRAME_HEIGHT - SAFE_BOTTOM + 20
OVERLAY_MARGIN = SAFE_TOP + 20

# How the text on the ad is drawn, in the ASS format ffmpeg draws with libass. Captions and
# overlays are white and bold so they read over any picture. A caption is outlined (border
# style 1) and centred along the bottom (alignment 2) above a margin; an overlay sits in a
# half-dark box (border style 3, whose box takes the outline colour) centred along the top
# (alignment 8) below one. The end card's text is dark on its white card, each row placed
# where end_card_text puts it. Sizes are for a FRAME_WIDTH by FRAME_HEIGHT frame.
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
100,100,0,0,1,5,0,2,60,60,{CAPTION_MARGIN},1
Style: Overlay,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,-1,0,0,0,\
100,100,0,0,3,18,0,8,60,60,{OVERLAY_MARGIN},1
Style: EndCard,DejaVu Sans,50,&H00202020,&H00202020,&H00FFFFFF,&H00FFFFFF,0,0,0,0,\
100,100,0,0,1,0,0,8,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_text_to_draw(
    drawn: Sequence[Caption], parts: Sequence[Cut], into: Path, end_card: EndCard | None = None
) -> None:
    """Write the text on the ad as an ASS file for ffmpeg to draw, at `into`: the captions,
    each part's overlay for as long as its picture plays, or, for a part whose picture
    plays not at all, while its line is said, and the end card's text while it shows."""
    lines = [TEXT_STYLES]
    lines += [_shown("Caption", caption.text, caption.start, caption.end) for caption in drawn]
    lines += [
        _shown("Overlay", part.overlay, part.start, part.end)
        if part.end > part.start
        else _shown("Overlay", part.overlay, part.voice_start, part.voice_end)
        for part in parts
        if part.overlay
    ]
    if end_card is not None:
        lines += [
            _shown(
                "EndCard",
                row.text,
                end_card.start,
                end_card.end,
                styled=f"\\an8\\pos({FRAME_WIDTH // 2},{row.top})\\fs{row.size}"
                f"\\b{int(row.bold)}\\q2",
            )
            for row in end_card_text(end_card)
        ]
    into.write_text("".join(lines), encoding="utf-8")


def _shown(style: str, text: str, start: float, end: float, styled: str = "") -> str:
    """One piece of text as the ASS format lists it: in `style`, from `start` to `end`, with
    any `styled` override tags first."""
    # Braces and backslashes would be read as styling, not shown.
    shown = text.replace("\\", "").replace("{", "(").replace("}", ")")
    tags = f"{{{styled}}}" if styled else ""
    return f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},{style},,0,0,0,,{tags}{shown}\n"


def _timestamp(seconds: float) -> str:
    """Seconds as the ASS format writes a time: hours, minutes, seconds and centiseconds,
    counted in whole centiseconds so a time never rounds up to 60 seconds."""
    hours, rest = divmod(round(seconds * 100), 360_000)
    minutes, rest = divmod(rest, 6_000)
    whole, centi = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centi:02d}"


def seconds_of(media: Path) -> float:
    """How long an audio or video file lasts, as ffprobe reads it."""
    probed = _probe("-show_entries", "format=duration", "-of", "json", str(media))
    return float(json.loads(probed)["format"]["duration"])


def _probe(*arguments: str) -> str:
    """What ffprobe says about a file, run with `arguments`. Raises AssemblyFailed, saying
    what ffprobe said, if it fails or takes over a minute."""
    try:
        probed = subprocess.run(
            [settings.FFPROBE, "-v", "error", *arguments],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as late:
        raise AssemblyTimedOut(f"ffprobe took over {late.timeout:g} seconds") from late
    if probed.returncode != 0:
        raise AssemblyFailed(probed.stderr.strip()[-2000:])
    return probed.stdout


def has_sound(media: Path) -> bool:
    """Whether a video has a sound track, as ffprobe reads it."""
    probed = _probe(
        "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(media)
    )
    return bool(json.loads(probed)["streams"])


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
    parts: Sequence[tuple[Path, Cut]],
    into: Path,
    *,
    music: Path,
    drawn: Sequence[Caption],
    end_card: EndCard | None = None,
    photo: Path | None = None,
) -> None:
    """Cut each clip file to its part's picture and voice, join the pictures, in order, and
    the voices, in order, apart, as each plays at its own time, and put them together with
    the music under the voice, the captions drawn along the bottom and each part's overlay
    along the top, into the finished ad at `into`, ending on the `end_card`, if given, with
    the product `photo` on it (a plain card without one), over the music alone. The ad lasts
    as long as its picture."""
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
                # Pixels are made square after the scale: clips a few pixels apart in size
                # would each be left a different hair off square, and concat refuses to join
                # pictures whose pixel shapes differ (fix triage AC2).
                f"scale={FRAME_WIDTH}:{FRAME_HEIGHT},setsar=1,fps={FRAME_RATE},"
                f"trim=end_frame={frames}[v{number}]"
            )
            pictures.append(f"[v{number}]")
        # A voice whose clip's sound runs out early is made up with silence, so the voices
        # after it still start on time; a clip with no sound at all plays silence for its
        # voice's time.
        voice_seconds = round(cut.voice_end - cut.voice_start, 3)
        if has_sound(clip):
            filters.append(
                f"[{number}:a]atrim=start={cut.voice_clip_start}:end={cut.voice_clip_end},"
                f"asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={voice_seconds}[a{number}]"
            )
        else:
            filters.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=end={voice_seconds}[a{number}]"
            )
        voices.append(f"[a{number}]")
    if end_card is not None:
        pictures.append("[card]")
        filters.append(_end_card_picture(end_card, photo, len(parts)))
        if photo is not None:
            inputs += ["-loop", "1", "-framerate", str(FRAME_RATE), "-i", str(photo)]
    filters.append(f"{''.join(pictures)}concat=n={len(pictures)}:v=1:a=0[joined]")
    filters.append(f"{''.join(voices)}concat=n={len(voices)}:v=0:a=1[said]")
    # ffmpeg reads the text to draw from a file beside the ad, whose path has nothing to
    # escape.
    text = into.parent / "text.ass"
    write_text_to_draw(drawn, [cut for _, cut in parts], text, end_card)
    filters.append(f"[joined]subtitles={text}[v]")
    music_input = len(parts) + (1 if end_card is not None and photo is not None else 0)
    inputs += ["-i", str(music)]
    ad_seconds = end_card.end if end_card is not None else parts[-1][1].end
    filters.append(f"[{music_input}:a]{music_filters(seconds_of(music), ad_seconds)}[music]")
    # A picture that plays on past the last line plays over the music only. The voice is
    # padded without end and the mix cut at the ad's end: amix drops the voice it still holds
    # when the voice, its first input, ends while the longer music lags behind, which cut the
    # last line off about 1.2 s early in every graded test ad (08 Oct).
    filters.append("[said]apad[voice]")
    # The ad lasts as long as its picture; the levels set above are kept, not evened out.
    filters.append(
        f"[voice][music]amix=inputs=2:duration=first:normalize=0,atrim=end={ad_seconds}[a]"
    )
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


def _end_card_picture(card: EndCard, photo: Path | None, input_number: int) -> str:
    """The filter that draws the end card's picture, as [card]: the product `photo`, ffmpeg's
    input `input_number`, as large as fits in its box on a white card, or the white card
    alone with no photo. Its text is drawn over it with the rest of the ad's."""
    frames = round(card.end * FRAME_RATE) - round(card.start * FRAME_RATE)
    blank = f"color=c=white:s={FRAME_WIDTH}x{FRAME_HEIGHT}:r={FRAME_RATE}"
    if photo is None:
        return f"{blank},setsar=1,format=yuv420p,trim=end_frame={frames}[card]"
    top, height = end_card_photo_box(card)
    return (
        f"[{input_number}:v]scale=w={END_CARD_TEXT_WIDTH}:h={height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={FRAME_WIDTH}:{FRAME_HEIGHT}:(ow-iw)/2:{top}+({height}-ih)/2:color=white,"
        f"setsar=1,fps={FRAME_RATE},format=yuv420p,trim=end_frame={frames}[card]"
    )


def _ffmpeg(*arguments: str) -> None:
    """Run ffmpeg with `arguments`. Raises AssemblyFailed, saying what ffmpeg said, if it
    fails or takes over TIMEOUT_SECONDS."""
    try:
        ffmpeg = subprocess.run(
            [settings.FFMPEG, "-hide_banner", "-loglevel", "error", *arguments],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as late:
        raise AssemblyTimedOut(f"ffmpeg took over {late.timeout:g} seconds") from late
    if ffmpeg.returncode != 0:
        raise AssemblyFailed(ffmpeg.stderr.strip()[-2000:])
