"""Join scene clips with their own sound, written captions, overlays and music, each cut
short of the silence before the ad's first word and after a clip's last."""

import difflib
import functools
import io
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from django.conf import settings

from jobs.card_parts import (
    PICTURES,
    CardParts,
    Part,
    PartKind,
    Size,
    badge_colour,
    card_colour,
    rgb_of,
    sale_end_words,
)

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
    """The card the ad ends on, after its last scene (spec #783): the `card`'s parts, drawn
    from `start` to `end`, in seconds, over the music alone."""

    card: CardParts
    start: float
    end: float


def end_card_after(cuts: Sequence[Cut], seconds: float, card: CardParts) -> EndCard:
    """The end card, played for `seconds` after the last of `cuts`, showing `card`."""
    start = cuts[-1].end
    return EndCard(card=card, start=start, end=round(start + seconds, 3))


# The fonts the end card is drawn in, kept in the repo with their licence (SIL Open Font
# License, fonts/LICENSE-Inter.txt) so every machine draws the same thing with nothing to
# install: ffmpeg is pointed at the folder (join's `fontsdir`), which holds font files only,
# as it tries to load every file in it, and row widths are measured with the same files.
FONTS = Path(__file__).parent / "fonts" / "inter"
FONT_FILES = {
    "Inter ExtraBold": "Inter-ExtraBold.ttf",
    "Inter Bold": "Inter-Bold.ttf",
    "Inter SemiBold": "Inter-SemiBold.ttf",
    "Inter Medium": "Inter-Medium.ttf",
}


@functools.cache
def _font(name: str, size: int) -> PIL.ImageFont.FreeTypeFont:
    return PIL.ImageFont.truetype(FONTS / FONT_FILES[name], size)


def text_width(text: str, font: str, size: int) -> float:
    """How wide `text` is drawn in `font` at `size` px, measured with the font itself."""
    return _font(font, size).getlength(text)


@functools.cache
def _em() -> float:
    """libass draws a font so its ascent and descent together fill the size it is given, which
    for Inter is a little over its em: the size to give it for each px of a CSS font size."""
    ascent, descent = _font("Inter Medium", 1000).getmetrics()
    return (ascent + descent) / 1000


# The end card's layout, from the approved mock-up (spec #783), for a FRAME_WIDTH by
# FRAME_HEIGHT frame: inside the safe area with padding inside it and margins at the sides.
CARD_TOP = SAFE_TOP + 40
CARD_BOTTOM = SAFE_BOTTOM - 40
CARD_MARGIN = 60
CARD_WIDTH = FRAME_WIDTH - 2 * CARD_MARGIN
CARD_MIDDLE = FRAME_WIDTH // 2
PHOTOS = frozenset({PartKind.PHOTO, PartKind.SECOND_PHOTO})


@dataclass(frozen=True)
class _Look:
    """How a kind of words is drawn: font, size in px and line height, as a share of it."""

    font: str
    size: int
    line: float


HEADLINE_LOOK = _Look("Inter ExtraBold", 76, 1.12)
PRICE_LOOK = _Look("Inter ExtraBold", 122, 1.0)
OLD_PRICE_LOOK = _Look("Inter Medium", 56, 1.21)
BADGE_LOOK = _Look("Inter Bold", 46, 1.21)
SALE_END_LOOK = _Look("Inter SemiBold", 42, 1.21)
LINE_LOOK = _Look("Inter Medium", 44, 1.32)
# The badge's padding around its words, top and bottom then each side; the gap between the
# price, the old price and the badge; between photos side by side; a photo's corners.
BADGE_PADDING = (12, 28)
PRICE_ROW_GAP = 22
PHOTOS_GAP = 24
PHOTO_CORNER = 32
# The old price and the lines of words are grey and soft dark grey; the badge's words white.
OLD_PRICE_GREY = "#8b918d"
SOFT_GREY = "#4a4f4c"
WHITE = "#ffffff"
# The most rows each kind of words may take before it is drawn smaller.
MOST_ROWS = {
    PartKind.HEADLINE: 2,
    PartKind.SMALL_LINE: 2,
    PartKind.SHOP_LINE: 1,
    PartKind.EXTRA_LINE: 2,
    PartKind.SALE_END: 1,
}
LINE_LOOKS = {
    PartKind.HEADLINE: HEADLINE_LOOK,
    PartKind.SMALL_LINE: LINE_LOOK,
    PartKind.SHOP_LINE: LINE_LOOK,
    PartKind.EXTRA_LINE: LINE_LOOK,
    PartKind.SALE_END: SALE_END_LOOK,
}
# Words that need more rows than they have, or a word too wide for a row, are drawn smaller,
# never below this size.
SMALLEST_TEXT = 28
# Words that need more rows than they have even at SMALLEST_TEXT, broken across rows, are
# drawn smaller still, never below this size, rather than taking more rows.
LEAST_TEXT = 16
# What each size step does: words are drawn at this share of their size; a logo is this tall;
# a photo is given at least this share of the card's height, the text drawn smaller to make
# room, and a small photo is drawn at SMALL_PHOTO of the height it is given.
TEXT_SCALE = {Size.SMALL: 0.8, Size.NORMAL: 1.0, Size.LARGE: 1.25}
LOGO_HEIGHT = {Size.SMALL: 90, Size.NORMAL: 120, Size.LARGE: 160}
PHOTO_SHARE = {Size.SMALL: 0.25, Size.NORMAL: 0.4, Size.LARGE: 0.75}
SMALL_PHOTO = 0.75
# Text that would not fit is drawn smaller in steps of this much, down to LEAST_SCALE.
SCALE_STEP = 0.05
LEAST_SCALE = 0.3


def _gap_after(kind: PartKind, next_kind: PartKind) -> int:
    """The space between a part and the next, as the mock-up spaces them."""
    if kind in PICTURES:
        return 40
    if kind == PartKind.HEADLINE:
        return 30
    if kind == PartKind.PRICE:
        return 10 if next_kind == PartKind.SALE_END else 34
    if kind == PartKind.SALE_END:
        return 26
    # Lines of words follow one another with no more space than their line height gives.
    return 0 if next_kind in LINE_LOOKS and next_kind != PartKind.HEADLINE else 30


@dataclass(frozen=True)
class Words:
    """One row of words on the end card: its text, font, size in px and colour; its centre;
    its row's height; which of the card's parts of words it belongs to, counted from 0 in
    the order they are drawn, which sets when it pops in; whether it is crossed out; and the
    fill of the pill drawn behind it, blank for none."""

    text: str
    font: str
    size: int
    colour: str
    middle_x: float
    middle_y: float
    height: float
    order: int
    struck: bool = False
    pill: str = ""

    @property
    def width(self) -> float:
        return text_width(self.text, self.font, self.size)

    @property
    def left(self) -> float:
        return self.middle_x - self.width / 2

    @property
    def right(self) -> float:
        return self.middle_x + self.width / 2

    @property
    def top(self) -> float:
        return self.middle_y - self.height / 2

    @property
    def bottom(self) -> float:
        return self.middle_y + self.height / 2


@dataclass(frozen=True)
class PictureBox:
    """Where a picture part is drawn: the box it is fitted into, keeping its shape. A photo
    gets rounded corners; a logo is drawn as it is."""

    kind: PartKind
    file: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class PartPlace:
    """The top and bottom of the room a drawn part takes on the card."""

    kind: PartKind
    top: float
    bottom: float


@dataclass(frozen=True)
class CardLayout:
    """Where everything on the end card goes: each drawn part's room, in order, its words
    and its pictures."""

    parts: tuple[PartPlace, ...]
    words: tuple[Words, ...]
    pictures: tuple[PictureBox, ...]


@dataclass(frozen=True)
class _Block:
    """A part of words laid out from its own top: its height and its rows, each with its
    centre's height below that top."""

    kind: PartKind
    height: float
    rows: tuple[tuple[Words, float], ...]


def end_card_layout(card: CardParts) -> CardLayout:
    """Lay out the parts the `card` draws, top to bottom in its order, inside the card's
    room in the safe area: each part of words at its size, made smaller when its words need
    more rows than it has; photos next to each other in the order share a row, and the
    photos take what height the rest leave, at least their share of the card, the words
    drawn smaller to make room. A part not drawn leaves no gap."""
    drawn = card.drawn()
    # Photos next to each other in the order are drawn side by side in one row.
    rows: list[list[Part]] = []
    for part in drawn:
        if part.kind in PHOTOS and rows and rows[-1][0].kind in PHOTOS:
            rows[-1].append(part)
        else:
            rows.append([part])
    room = CARD_BOTTOM - CARD_TOP
    photo_rows = [row for row in rows if row[0].kind in PHOTOS]
    photo_room = sum(max(PHOTO_SHARE[part.size] for part in row) for row in photo_rows) * room

    def fixed(scale: float) -> tuple[list[_Block | None], float]:
        """The parts of words at `scale`, the room taken by them, the logos and the gaps."""
        blocks: list[_Block | None] = []
        order = 0
        taken = 0.0
        for number, row in enumerate(rows):
            kind = row[0].kind
            if kind in PICTURES:
                blocks.append(None)
                if kind == PartKind.LOGO:
                    taken += LOGO_HEIGHT[row[0].size] * scale
            else:
                block = _block(row[0], scale, card.accent, order)
                order += 1
                blocks.append(block)
                taken += block.height
            if number + 1 < len(rows):
                taken += _gap_after(kind, rows[number + 1][0].kind) * scale
        return blocks, taken

    scale = 1.0
    blocks, taken = fixed(scale)
    while taken + photo_room > room and scale - SCALE_STEP >= LEAST_SCALE:
        scale = round(scale - SCALE_STEP, 2)
        blocks, taken = fixed(scale)
    photo_height = max(2.0, (room - taken) / len(photo_rows)) if photo_rows else 0.0
    top = float(CARD_TOP) if photo_rows else CARD_TOP + max(0.0, room - taken) / 2
    places: list[PartPlace] = []
    words: list[Words] = []
    pictures: list[PictureBox] = []
    for number, (row, block) in enumerate(zip(rows, blocks, strict=True)):
        kind = row[0].kind
        if block is not None:
            height = block.height
            words += [replace(word, middle_y=top + below) for word, below in block.rows]
        elif kind == PartKind.LOGO:
            height = LOGO_HEIGHT[row[0].size] * scale
            pictures.append(
                PictureBox(kind, row[0].file, CARD_MARGIN, round(top), CARD_WIDTH, round(height))
            )
        else:
            height = photo_height
            pictures += _photo_boxes(row, top, height)
        places += [PartPlace(part.kind, top, top + height) for part in row]
        top += height
        if number + 1 < len(rows):
            top += _gap_after(kind, rows[number + 1][0].kind) * scale
    return CardLayout(tuple(places), tuple(words), tuple(pictures))


def _photo_boxes(row: Sequence[Part], top: float, height: float) -> list[PictureBox]:
    """The boxes of photos side by side in a row `height` tall from `top`, sharing its width;
    a small photo drawn smaller in the middle of its box."""
    width = (CARD_WIDTH - PHOTOS_GAP * (len(row) - 1)) / len(row)
    boxes = []
    for number, part in enumerate(row):
        left = CARD_MARGIN + number * (width + PHOTOS_GAP)
        share = SMALL_PHOTO if part.size == Size.SMALL else 1.0
        boxes.append(
            PictureBox(
                part.kind,
                part.file,
                round(left + width * (1 - share) / 2),
                round(top + height * (1 - share) / 2),
                max(2, round(width * share)),
                max(2, round(height * share)),
            )
        )
    return boxes


def _block(part: Part, scale: float, accent: str, order: int) -> _Block:
    """`part`'s words laid out from its own top, at `scale` of their size."""
    if part.kind == PartKind.PRICE:
        return _price_block(part, scale, accent, order)
    look = LINE_LOOKS[part.kind]
    text = sale_end_words(part.ends) if part.kind == PartKind.SALE_END and part.ends else part.text
    colour = {
        PartKind.HEADLINE: accent,
        PartKind.SALE_END: badge_colour(accent),
    }.get(part.kind, SOFT_GREY)
    size = max(1, round(look.size * TEXT_SCALE[part.size] * scale))
    rows, size = _wrapped(" ".join(text.split()), look.font, size, MOST_ROWS[part.kind])
    height = round(size * look.line)
    return _Block(
        part.kind,
        height * len(rows),
        tuple(
            (
                Words(row, look.font, size, colour, CARD_MIDDLE, 0.0, height, order),
                height * (number + 0.5),
            )
            for number, row in enumerate(rows)
        ),
    )


def _price_block(part: Part, scale: float, accent: str, order: int) -> _Block:
    """The price row: the price, then the old price crossed out and the "% off" badge when it
    has them, side by side and centred, all drawn smaller together if they'd be too wide."""
    pieces = [(" ".join(part.text.split()), PRICE_LOOK, accent, False, "")]
    if part.old_price.strip():
        pieces.append((" ".join(part.old_price.split()), OLD_PRICE_LOOK, OLD_PRICE_GREY, True, ""))
    if part.percent is not None:
        pieces.append((f"{part.percent}% off", BADGE_LOOK, WHITE, False, badge_colour(accent)))
    factor = TEXT_SCALE[part.size] * scale
    while True:
        sizes = [max(1, round(look.size * factor)) for _, look, *_ in pieces]
        widths = [
            text_width(text, look.font, size) + (2 * BADGE_PADDING[1] * factor if pill else 0)
            for (text, look, _, _, pill), size in zip(pieces, sizes, strict=True)
        ]
        across = sum(widths) + PRICE_ROW_GAP * factor * (len(pieces) - 1)
        if across <= CARD_WIDTH or min(sizes) <= 1:
            break
        factor *= 0.95
    heights = [
        size * look.line + (2 * BADGE_PADDING[0] * factor if pill else 0)
        for (_, look, _, _, pill), size in zip(pieces, sizes, strict=True)
    ]
    height = round(max(heights))
    x = CARD_MIDDLE - across / 2
    placed = []
    for (text, look, colour, struck, pill), size, width, row in zip(
        pieces, sizes, widths, heights, strict=True
    ):
        placed.append(
            (
                Words(text, look.font, size, colour, x + width / 2, 0.0, row, order, struck, pill),
                height / 2,
            )
        )
        x += width + PRICE_ROW_GAP * factor
    return _Block(PartKind.PRICE, height, tuple(placed))


def _wrapped(text: str, font: str, size: int, most_rows: int) -> tuple[list[str], int]:
    """`text` in rows that fit CARD_WIDTH at `size`, measured with the font, at most
    `most_rows` of them: smaller, down to SMALLEST_TEXT, when the words need more rows or a
    word is too wide for one; then a word too wide is broken across rows, and the words are
    made smaller still, down to LEAST_TEXT, to keep to `most_rows`. Words are never cut: what
    still needs more rows at LEAST_TEXT takes them."""
    smallest = min(size, SMALLEST_TEXT)
    least = min(size, LEAST_TEXT)
    while True:
        rows = _rows(text, font, size, break_words=size <= smallest)
        wide = any(text_width(row, font, size) > CARD_WIDTH for row in rows)
        if not wide and (len(rows) <= most_rows or size <= least):
            return rows, size
        if wide and size <= smallest:
            # Only a word too wide for a row is left, and it is now broken across rows.
            return rows, size
        size -= 2


def _rows(text: str, font: str, size: int, *, break_words: bool) -> list[str]:
    """`text`'s words in rows no wider than CARD_WIDTH, a word too wide for a row left on
    its own, or, with `break_words`, broken across rows."""
    rows: list[str] = []
    for word in text.split():
        pieces = [word]
        if break_words and text_width(word, font, size) > CARD_WIDTH:
            pieces = []
            for letter in word:
                if pieces and text_width(pieces[-1] + letter, font, size) <= CARD_WIDTH:
                    pieces[-1] += letter
                else:
                    pieces.append(letter)
        for piece in pieces:
            joined = f"{rows[-1]} {piece}" if rows else piece
            if rows and piece is pieces[0] and text_width(joined, font, size) <= CARD_WIDTH:
                rows[-1] = joined
            else:
                rows.append(piece)
    return rows


def flat_photo(data: bytes) -> bytes:
    """A product photo as the end card draws it: its first frame as a PNG with no
    transparency, see-through parts white. Raises ValueError for a file that isn't a
    picture."""
    picture = picture_of(data)
    flat = PIL.Image.new("RGB", picture.size, (255, 255, 255))
    flat.paste(picture, mask=picture.getchannel("A"))
    written = io.BytesIO()
    flat.save(written, format="PNG")
    return written.getvalue()


def picture_of(data: bytes) -> PIL.Image.Image:
    """The first frame of a picture file, with its transparency. Raises ValueError for a file
    that isn't a picture."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as opened:
            return opened.convert("RGBA")
    except (OSError, PIL.Image.DecompressionBombError, ValueError) as unreadable:
        raise ValueError(f"the photo can't be read as a picture: {unreadable}") from unreadable


def end_card_picture(card: CardParts, pictures: Mapping[str, bytes]) -> bytes:
    """The end card's picture as a PNG, its words drawn over it later: the card's faintly
    tinted white, with each picture part, read from `pictures` by its file key, fitted into
    its box, keeping its shape: a photo flat on white with rounded corners, a logo exactly as
    it is, see-through parts and all. Raises ValueError for a picture that can't be read."""
    canvas = PIL.Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), rgb_of(card_colour(card.accent)))
    for box in end_card_layout(card).pictures:
        picture = picture_of(pictures[box.file])
        if box.kind != PartKind.LOGO:
            flat = PIL.Image.new("RGBA", picture.size, (255, 255, 255, 255))
            flat.alpha_composite(picture)
            picture = flat
        fit = min(box.width / picture.width, box.height / picture.height)
        size = (max(1, round(picture.width * fit)), max(1, round(picture.height * fit)))
        picture = picture.resize(size, PIL.Image.Resampling.LANCZOS)
        if box.kind != PartKind.LOGO:
            picture.putalpha(_rounded(size, round(PHOTO_CORNER)))
        at = (box.left + (box.width - size[0]) // 2, box.top + (box.height - size[1]) // 2)
        canvas.alpha_composite(picture, at)
    written = io.BytesIO()
    canvas.convert("RGB").save(written, format="PNG")
    return written.getvalue()


def _rounded(size: tuple[int, int], corner: int) -> PIL.Image.Image:
    """A mask the `size` of a picture with its corners rounded, drawn large and shrunk so
    the curves are smooth."""
    large = PIL.Image.new("L", (size[0] * 4, size[1] * 4), 0)
    PIL.ImageDraw.Draw(large).rounded_rectangle(
        (0, 0, large.width - 1, large.height - 1), radius=corner * 4, fill=255
    )
    return large.resize(size, PIL.Image.Resampling.LANCZOS)


def website_of(url: str) -> str:
    """The website a product's link is on, as an ad writes it: its host, without "www."."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


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
# (alignment 8) below one. The end card's words are drawn in Inter, each row centred
# (alignment 5) where end_card_layout puts it, in its own font, size and colour. Sizes are
# for a FRAME_WIDTH by FRAME_HEIGHT frame.
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
Style: EndCard,Inter Medium,50,&H00202020,&H00202020,&H00FFFFFF,&H00FFFFFF,0,0,0,0,\
100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_text_to_draw(
    drawn: Sequence[Caption], parts: Sequence[Cut], into: Path, end_card: EndCard | None = None
) -> None:
    """Write the text on the ad as an ASS file for ffmpeg to draw, at `into`: the captions,
    each part's overlay for as long as its picture plays, or, for a part whose picture
    plays not at all, while its line is said, and the end card's words while it shows,
    each part of them popping in after the one before."""
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
        for words in end_card_layout(end_card.card).words:
            lines += _end_card_words(words, end_card.start, end_card.end)
    into.write_text("".join(lines), encoding="utf-8")


# How a row of words pops in (spec #783): from POP_FROM of its size and see-through, it grows
# to POP_OVER and fully solid by POP_PEAK of its POP_MS, then settles at its size. The first
# part of words starts FIRST_POP_MS after the card does, each next one POP_APART_MS later.
POP_FROM = 70
POP_OVER = 107
POP_PEAK = 0.6
POP_MS = 420
FIRST_POP_MS = 120
POP_APART_MS = 110


def pop_in(order: int) -> str:
    """The ASS tags that pop in the part of words drawn `order`th, counted from 0, timed from
    the start of the card."""
    starts = FIRST_POP_MS + POP_APART_MS * order
    peak = starts + round(POP_MS * POP_PEAK)
    return (
        f"\\fscx{POP_FROM}\\fscy{POP_FROM}\\alpha&HFF&"
        f"\\t({starts},{peak},\\fscx{POP_OVER}\\fscy{POP_OVER}\\alpha&H00&)"
        f"\\t({peak},{starts + POP_MS},\\fscx100\\fscy100)"
    )


def _end_card_words(words: Words, start: float, end: float) -> list[str]:
    """A row of the end card's words as the ASS format lists it, crossed out if it is, over
    its pill, if it has one, drawn behind it at the same place so the two pop in as one."""
    placed = f"\\an5\\pos({words.middle_x:.1f},{words.middle_y:.1f})"
    drawn = []
    if words.pill:
        width = round(words.width + 2 * BADGE_PADDING[1] * words.size / BADGE_LOOK.size)
        height = round(words.height)
        drawn.append(
            _shown(
                "EndCard",
                _pill(width, height),
                start,
                end,
                styled=f"{placed}\\p1\\1c{_ass_colour(words.pill)}{pop_in(words.order)}",
            )
        )
    drawn.append(
        _shown(
            "EndCard",
            words.text,
            start,
            end,
            styled=f"{placed}\\fn{words.font}\\fs{words.size * _em():.1f}"
            f"\\1c{_ass_colour(words.colour)}\\s{int(words.struck)}\\q2{pop_in(words.order)}",
            layer=1,
        )
    )
    return drawn


def _pill(width: int, height: int) -> str:
    """An ASS drawing of a fully rounded pill `width` by `height`."""
    r = height / 2
    k = r * 0.5523  # a quarter circle's Bezier handle
    w, h = width, height
    return (
        f"m {r:g} 0 l {w - r:g} 0 b {w - r + k:g} 0 {w:g} {r - k:g} {w:g} {r:g} "
        f"b {w:g} {r + k:g} {w - r + k:g} {h:g} {w - r:g} {h:g} l {r:g} {h:g} "
        f"b {r - k:g} {h:g} 0 {r + k:g} 0 {r:g} b 0 {r - k:g} {r - k:g} 0 {r:g} 0"
    )


def _ass_colour(colour: str) -> str:
    """A "#rrggbb" colour as ASS writes one: blue, green, red."""
    red, green, blue = rgb_of(colour)
    return f"&H{blue:02X}{green:02X}{red:02X}&"


def _shown(
    style: str, text: str, start: float, end: float, styled: str = "", layer: int = 0
) -> str:
    """One piece of text as the ASS format lists it: in `style`, from `start` to `end`, with
    any `styled` override tags first, on `layer` (higher layers are drawn over lower)."""
    # Braces and backslashes would be read as styling, not shown.
    shown = text.replace("\\", "").replace("{", "(").replace("}", ")")
    tags = f"{{{styled}}}" if styled else ""
    return (
        f"Dialogue: {layer},{_timestamp(start)},{_timestamp(end)},{style},,0,0,0,,{tags}{shown}\n"
    )


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
    card_picture: Path | None = None,
) -> None:
    """Cut each clip file to its part's picture and voice, join the pictures, in order, and
    the voices, in order, apart, as each plays at its own time, and put them together with
    the music under the voice, the captions drawn along the bottom and each part's overlay
    along the top, into the finished ad at `into`, ending on the `end_card`, if given, its
    words drawn over its `card_picture` (end_card_picture), over the music alone. The ad
    lasts as long as its picture."""
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
        assert card_picture is not None, "an end card is drawn over its picture"
        pictures.append("[card]")
        filters.append(_end_card_picture(end_card, len(parts)))
        inputs += ["-loop", "1", "-framerate", str(FRAME_RATE), "-i", str(card_picture)]
    filters.append(f"{''.join(pictures)}concat=n={len(pictures)}:v=1:a=0[joined]")
    filters.append(f"{''.join(voices)}concat=n={len(voices)}:v=0:a=1[said]")
    # ffmpeg reads the text to draw from a file beside the ad, whose path has nothing to
    # escape.
    text = into.parent / "text.ass"
    write_text_to_draw(drawn, [cut for _, cut in parts], text, end_card)
    # The end card's fonts are read from the repo's folder of them, not the machine's.
    filters.append(f"[joined]subtitles=filename={text}:fontsdir={FONTS}[v]")
    music_input = len(parts) + (1 if end_card is not None else 0)
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


def _end_card_picture(card: EndCard, input_number: int) -> str:
    """The filter that plays the end card's picture, ffmpeg's input `input_number`, for as
    long as the card shows, as [card]. Its words are drawn over it with the rest of the
    ad's text."""
    frames = round(card.end * FRAME_RATE) - round(card.start * FRAME_RATE)
    return (
        f"[{input_number}:v]scale={FRAME_WIDTH}:{FRAME_HEIGHT},setsar=1,fps={FRAME_RATE},"
        f"format=yuv420p,trim=end_frame={frames}[card]"
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
