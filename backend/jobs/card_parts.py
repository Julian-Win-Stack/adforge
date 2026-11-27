"""The end card as a record of parts (spec #783): what each part holds, whether it is shown,
its size and its place in the order, and the ad's accent colour. Assembly draws exactly what
the record holds (jobs.assembly.end_card_layout)."""

import colorsys
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator


class PartKind(StrEnum):
    """The kinds of part an end card can hold, each at most once."""

    PHOTO = "photo"
    SECOND_PHOTO = "second_photo"
    HEADLINE = "headline"
    PRICE = "price"
    SALE_END = "sale_end"
    SMALL_LINE = "small_line"
    SHOP_LINE = "shop_line"
    EXTRA_LINE = "extra_line"
    LOGO = "logo"


# The parts drawn from a picture, by its file-store key, rather than from words.
PICTURES = frozenset({PartKind.PHOTO, PartKind.SECOND_PHOTO, PartKind.LOGO})

# Every part in the order a card shows them unless it is told otherwise: the photos side by
# side on top, then the words, then the logo.
DEFAULT_ORDER = tuple(PartKind)


class Size(StrEnum):
    """A part's size, in three steps."""

    SMALL = "small"
    NORMAL = "normal"
    LARGE = "large"


class Part(BaseModel, frozen=True):
    """One part of the end card. `text` holds a line's words or the price; `file` a picture
    part's file-store key; `old_price` and `percent` the price before a sale and the percent
    off; `ends` the day a sale ends. A part with nothing in it, or not `shown`, is not drawn
    and leaves no gap."""

    kind: PartKind
    shown: bool = True
    size: Size = Size.NORMAL
    text: str = ""
    file: str = ""
    old_price: str = ""
    percent: int | None = Field(default=None, ge=1, le=99)
    ends: date | None = None

    @property
    def drawn(self) -> bool:
        """Whether the card draws this part: shown, and with something in it."""
        if not self.shown:
            return False
        if self.kind in PICTURES:
            return bool(self.file)
        if self.kind == PartKind.SALE_END:
            return self.ends is not None
        return bool(self.text.strip())


# Near-black: the accent colour of a card with no colour of its own, today's text colour.
NEAR_BLACK = "#202020"


class CardParts(BaseModel, frozen=True):
    """The end card: its parts in the order they are drawn, top to bottom, and the accent
    colour its headline, price and badge are drawn in, as "#rrggbb"."""

    parts: tuple[Part, ...]
    accent: str = Field(default=NEAR_BLACK, pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def _each_kind_once(self) -> Self:
        kinds = [part.kind for part in self.parts]
        if len(kinds) != len(set(kinds)):
            raise ValueError("an end card holds each kind of part at most once")
        return self

    @classmethod
    def of(cls, contents: dict[PartKind, dict[str, Any]], accent: str = NEAR_BLACK) -> Self:
        """A card with every kind of part in DEFAULT_ORDER, each holding what `contents`
        gives it (empty if nothing), all shown at the normal size."""
        return cls(
            parts=tuple(Part(kind=kind, **contents.get(kind, {})) for kind in DEFAULT_ORDER),
            accent=accent,
        )

    def drawn(self) -> list[Part]:
        """The parts the card draws, in order."""
        return [part for part in self.parts if part.drawn]

    def part(self, kind: PartKind) -> Part | None:
        """The card's part of `kind`, if it has one."""
        return next((part for part in self.parts if part.kind == kind), None)

    def without_file(self, file: str) -> Self:
        """The same card with the picture part(s) holding `file` emptied, for a picture that
        can't be drawn."""
        return self.model_copy(
            update={
                "parts": tuple(
                    part.model_copy(update={"file": ""}) if part.file == file else part
                    for part in self.parts
                )
            }
        )

    def record(self) -> dict[str, Any]:
        """The card as JSON keeps it."""
        return self.model_dump(mode="json")


def sale_end_words(ends: date) -> str:
    """A sale's end as the card writes it: "Sale ends Dec 1"."""
    return f"Sale ends {ends:%b} {ends.day}"


def rgb_of(colour: str) -> tuple[int, int, int]:
    """A "#rrggbb" colour as its red, green and blue, 0 to 255."""
    value = colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_of(rgb: Sequence[float]) -> str:
    """Red, green and blue, 0 to 255, as "#rrggbb"."""
    return "#" + "".join(f"{max(0, min(255, round(value))):02x}" for value in rgb)


def contrast(one: str, other: str) -> float:
    """How far apart two colours read, as the web's contrast ratio, 1 to 21."""

    def luminance(colour: str) -> float:
        def channel(value: int) -> float:
            share = value / 255
            return share / 12.92 if share <= 0.04045 else ((share + 0.055) / 1.055) ** 2.4

        red, green, blue = (channel(value) for value in rgb_of(colour))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    lighter, darker = sorted((luminance(one), luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# The badge's shade is the accent made this much lighter, as the approved mock-up's is, but
# never so light that its white words stop reading (a contrast of 4.5 to 1, the web's rule).
BADGE_LIGHTER = 1.55
READABLE = 4.5


def badge_colour(accent: str) -> str:
    """The "% off" badge's fill and the sale end date's colour: a slightly lighter shade of
    the `accent`, on which white still reads."""
    hue, lightness, saturation = colorsys.rgb_to_hls(*(value / 255 for value in rgb_of(accent)))
    lighter = min(1.0, lightness * BADGE_LIGHTER)
    while lighter > lightness:
        shade = hex_of([value * 255 for value in colorsys.hls_to_rgb(hue, lighter, saturation)])
        if contrast(shade, "#ffffff") >= READABLE:
            return shade
        lighter -= 0.01
    return accent


# The card is white with a very faint tint of the accent: this much of it.
CARD_TINT = 0.06


def card_colour(accent: str) -> str:
    """The card's background: white with a faint tint of the `accent`."""
    return hex_of(
        [255 + (value - 255) * CARD_TINT for value in rgb_of(accent)],
    )
