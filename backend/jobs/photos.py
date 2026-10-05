"""Picking this product's own photos off its page. Firecrawl's marked screenshot numbers every
picture a visitor sees (I1, I2, ...) and every piece of text (T0, T1, ...); a model is shown
the screenshot cut into parts, the lists of numbered pictures and text pieces and, as a
reference, the shop's official record of the product with its official photos, and answers
with the numbers of the pictures that show this product. Those photos are then fetched at
their biggest size, each copy of one photo kept once.

Ported from the tested scripts: docs/scraping-test/ref-photo-test/scripts/point_ref.py,
fix-test/scripts/photo_rec.py and screen-test/scripts/compare.py."""

import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image, ImageStat
from pydantic import BaseModel

from gateway.types import Handoff

PICK_INSTRUCTIONS = """\
You look at one shop's product page the way a shopper would, to pick out the product's own \
photos and the text about it for an advert. Nothing about other products may get through.

You get screenshots of the page from top to bottom, cut into parts. Every picture has a blue \
box with a blue label "I<n>" in its top-left corner. Every piece of text has a red label \
"T<n>" just above its first line: a new piece starts at each heading, at each short bold or \
large line, and after about 1,200 characters (marked "(continued)"). Labels only appear on \
what a visitor can see; some numbers are missing from the screenshots. You also get a list of \
the numbered pictures (alt text, and where the picture links to if it links to another page) \
and a list of the numbered text pieces (the piece's first line or heading, and its text, \
including text a visitor has to click open).

First decide which ONE product the page sells: the one with the page title, price and buy \
button at the top. If the page lets the shopper choose a colour, flavour, shade or size, that \
choice is part of the same product.

Then answer:
- gallery_images: the pictures in that product's own photo gallery (the big photo next to the \
title and buy button, its thumbnails, and the rest of that gallery) that show the product \
itself: any angle, close-up, packaging, the product in use or worn by a model. Leave out \
gallery pictures in which the product can't be seen (text cards, charts, skin before/after, \
ingredients, badges).
- more_images: other pictures anywhere else on the page that clearly show this same product.
- product_sections: the T numbers of pieces whose text describes this product: what it is, \
materials or ingredients, sizes, fit, dimensions, how to use, benefits, specs, what's in the \
box, warranty, shipping and returns for this item, its FAQ. T0 is the text before the first \
heading.

Judge each piece by its own text, not only by the line above it. Never include pictures or \
pieces about other products: "you may also like", "shop the look", "pairs well with", \
"complete the set", bundles and kits, the rest of a collection, other flavours, scents, models \
or versions sold on their own pages, accessories sold separately, even from the same brand \
and even if they look alike. A picture that links to another product's page is another \
product. A picture that shows this product next to other products, such as a line-up, a set, \
a routine or a comparison of versions, is another product's picture too, even though this \
product is in it: leave it out. Also leave out reviews, Q&A, navigation, cart, promotions and \
footer text. When unsure whether a picture shows exactly this product, leave it out: a \
missing photo costs little, a wrong one puts another product in the advert.

You also get the shop's official record of the product sold on this page (title, brand, \
description, the names of its colours, shades or sizes) and its official photos, one per \
colour or shade, labelled R1, R2, ... Use them to know exactly what this product looks like \
and to tell it apart from look-alikes: a picture shows this product only if the product in it \
matches one of the official photos (any colour, shade or size in the record). A different \
shape, label, model or design is another product, even from the same brand. The official \
photos are a guide only: answer with I numbers from the page, never R numbers.

Then note faces: face_images are the I numbers, among the ones you picked, of pictures that \
show a person's face you could recognise. A body, a hand, an arm, lips or a face turned away, \
cut off or too small to recognise doesn't count."""

# A photo the picker never saw gets its Face note from a call of its own, worded as the
# picker's note (tested on the 8 test products' 74 photos: backend/broll-test/face_notes.py).
NOTE_FACE_INSTRUCTIONS = """\
You look at one product photo from a shop's page, for an advert. Say whether it shows a \
person's face you could recognise: has_face. A body, a hand, an arm, lips or a face turned \
away, cut off or too small to recognise doesn't count."""

NO_RECORD = "Official record of the product: none was found for this page."

# At most this many of the record's official photos are shown.
MOST_REFERENCE_PHOTOS = 6
# Variant values that pick a size rather than a look: they don't get a reference photo each.
SIZE_KEYS = re.compile(
    r"size|pack|count|quantity|qty|capacity|weight|volume|length|width|amount", re.I
)
# The screenshot is cut into parts this tall, from the top, and at most this many are shown.
PART_HEIGHT = 1_200
MOST_PARTS = 14
# A part whose colours vary less than this is blank, and isn't shown.
LEAST_COLOUR_RANGE = 12
# A text piece longer than this is cut to its start and end.
LONGEST_TEXT_PIECE = 2_000
# Two photos whose fingerprints differ in at most this many of their 256 bits are copies.
MOST_BITS_APART = 12


class PickHandoff(Handoff):
    official_record: str  # The record as text, or NO_RECORD.
    page_url: str
    page_title: str
    numbered_pictures: str
    numbered_text_pieces: str


class PickedPhotos(BaseModel):
    product: str
    gallery_images: list[int]
    more_images: list[int]
    # Asked for because the picker was tested with it; the text is copied by its own call.
    product_sections: list[int]
    notes: str
    # Last, as the picker was tested with it: the marks of picked pictures with a face.
    face_images: list[int]


class FaceNoteHandoff(Handoff):
    # Where the photo came from: its link on the page, or its key in the file store when the
    # shop owner attached it. Not a page photo's key, which changes when the page is read again.
    photo: str


class FaceNote(BaseModel):
    has_face: bool


def handoff(record: dict[str, Any] | None, marks: dict[str, Any], title: str) -> PickHandoff:
    """What the picker is handed: the record, the page, and its numbered pictures and text."""
    page_url = str(marks.get("url") or "")
    pictures = []
    for picture in numbered(marks):
        line = f'I{picture["n"]}: alt="{str(picture.get("alt") or "")[:80]}"'
        link = other_page(str(picture.get("href") or ""), page_url)
        pictures.append(line + (f" links to {link}" if link else ""))
    pieces = []
    for piece in marks.get("secs") or []:
        text = str(piece.get("text") or "")
        if len(text) > LONGEST_TEXT_PIECE:
            text = text[:900] + " … " + text[-300:]
        pieces.append(f"T{piece.get('i')} [{piece.get('heading')}]\n{text}")
    return PickHandoff(
        official_record=record_text(record) if record else NO_RECORD,
        page_url=page_url,
        page_title=" ".join(title.split()),
        numbered_pictures="\n".join(pictures),
        numbered_text_pieces="\n\n".join(pieces),
    )


def numbered(marks: dict[str, Any]) -> list[dict[str, Any]]:
    """The pictures the marking script numbered: those a visitor can see."""
    return [
        picture
        for picture in marks.get("imgs") or []
        if isinstance(picture, dict) and isinstance(picture.get("n"), int)
    ]


def picked_links(picked: PickedPhotos, marks: dict[str, Any]) -> list[tuple[str, bool]]:
    """The picked photos' links at their biggest size, gallery first, each with whether the
    picker marked it with a face. Numbers the page didn't show are dropped, and so are marks
    of pictures that weren't picked."""
    by_number = {picture["n"]: picture for picture in numbered(marks)}
    return [
        (best_url(by_number[number]), number in picked.face_images)
        for number in [*picked.gallery_images, *picked.more_images]
        if number in by_number
    ]


def record_text(record: dict[str, Any]) -> str:
    """The shop's record of the product, as text: title, brand, category, page, the names of
    its variants and its description."""
    description = " ".join(str(record.get("description") or "(none)").split())
    if len(description) > 2_000:
        description = description[:2_000] + " [...]"
    names: list[str] = []
    for variant in _variants(record):
        name = str(variant.get("title") or "")
        if name and name not in names:
            names.append(name)
    listed = "; ".join(names[:40]) + (f"; ... ({len(names)} in all)" if len(names) > 40 else "")
    return (
        f"Title: {record.get('title')}\n"
        f"Brand: {record.get('brand') or '(none)'}\n"
        f"Category: {record.get('category') or '(none)'}\n"
        f"Page: {record.get('url')}\n"
        f"Variants: {listed or '(none)'}\n"
        f"Description: {description}"
    )


def reference_urls(record: dict[str, Any]) -> list[str]:
    """The record's official photos: the first image of each look (its variant's values,
    leaving out sizes), each picture once, then the product's own images, at most 6."""
    looks: set[tuple[tuple[str, str], ...]] = set()
    names: set[str] = set()
    urls: list[str] = []
    for variant in _variants(record):
        values = variant.get("values")
        look = tuple(
            sorted(
                (str(key), str(value))
                for key, value in (values.items() if isinstance(values, dict) else [])
                if not SIZE_KEYS.search(str(key))
            )
        )
        if look in looks:
            continue
        for image in variant.get("images") or []:
            url = _link(image.get("url") if isinstance(image, dict) else None)
            if url and _name(url) not in names:
                looks.add(look)
                names.add(_name(url))
                urls.append(url)
                break
        if len(urls) >= MOST_REFERENCE_PHOTOS:
            return urls
    for image in record.get("images") or []:
        if len(urls) >= MOST_REFERENCE_PHOTOS:
            break
        url = _link(image.get("url") if isinstance(image, dict) else image)
        if url and _name(url) not in names:
            names.add(_name(url))
            urls.append(url)
    return urls


def _variants(record: dict[str, Any]) -> list[dict[str, Any]]:
    variants = record.get("variants")
    return [v for v in variants if isinstance(v, dict)] if isinstance(variants, list) else []


def _link(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    return url if url.startswith(("http://", "https://")) else ""


def _name(url: str) -> str:
    """A picture's file name without its type or size part, so one picture at two sizes
    has one name."""
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    name = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
    name = re.sub(r"(_\d+x\d*|_\d+x|-\d+x\d+|\._[A-Z0-9_,]+_)$", "", name)
    return name.casefold()


def other_page(href: str, page_url: str) -> str:
    """Where a picture links to, when it links to another page: its path, with its host if
    that is another site. "" if it links nowhere, to this page or to a picture."""
    if not href:
        return ""
    link, page = urlparse(href), urlparse(page_url)
    if link.scheme not in ("http", "https") or (
        link.path.rstrip("/") == page.path.rstrip("/") and link.netloc == page.netloc
    ):
        return ""
    if link.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return ""
    return (link.netloc if link.netloc != page.netloc else "") + link.path


def best_url(picture: dict[str, Any]) -> str:
    """The link to a picture at its biggest size: the widest in its srcset, else its src,
    with any size the shop's image server was asked for taken off."""
    url, widest = str(picture.get("src") or ""), 0
    # Split at a comma followed by a space: some image links hold commas.
    for candidate in re.split(r",\s+", str(picture.get("srcset") or "").strip()):
        bits = candidate.strip().split()
        if (
            len(bits) == 2
            and bits[0].startswith(("http", "//"))
            and bits[1].endswith("w")
            and bits[1][:-1].isdigit()
            and int(bits[1][:-1]) > widest
        ):
            widest, url = int(bits[1][:-1]), bits[0]
    if url.startswith("//"):
        url = "https:" + url
    return full_size(url)


def full_size(url: str) -> str:
    """`url` with the size asked of the shop's image server taken off: Shopify's width,
    height and crop, Amazon's `._..._.` part. Other shops' links are left as they are."""
    parts = urlparse(url)
    if "/cdn/shop/" in parts.path or "cdn.shopify.com" in parts.netloc:
        kept = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in ("width", "height", "crop")
        ]
        return urlunparse(parts._replace(query=urlencode(kept)))
    if "media-amazon.com" in parts.netloc:
        return urlunparse(parts._replace(path=re.sub(r"\._[^/]*_\.", ".", parts.path), query=""))
    return url


def same_key(url: str) -> str:
    """What one photo's links have in common at every size: host and path, with Shopify's
    `_<w>x<h>` size part taken off."""
    parts = urlparse(full_size(url))
    return parts.netloc + re.sub(r"_\d+x\d*(?=\.)", "", parts.path)


def dhash(content: bytes) -> int:
    """A photo's fingerprint: 256 bits, each saying whether a spot of the photo, in black and
    white at 17 by 16, is lighter than the spot to its right. The same photo at another size
    gets nearly the same bits. It ignores colour, so the same product photographed in two
    shades gets nearly the same bits too: that is accepted."""
    with Image.open(io.BytesIO(content)) as image:
        # A see-through background counts as white, as in the photos it was tested on.
        flat = Image.new("RGBA", image.size, "white")
        flat.alpha_composite(image.convert("RGBA"))
        pixels = flat.convert("L").resize((17, 16)).tobytes()
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (pixels[y * 17 + x] > pixels[y * 17 + x + 1])
    return bits


class Copies:
    """Photos seen so far, to keep each photo once: two are copies when their links differ
    only in size, or their fingerprints differ in at most MOST_BITS_APART bits. Kept photos
    are numbered from 0 in the order kept."""

    def __init__(self) -> None:
        self._keys: dict[str, int] = {}
        self._hashes: list[tuple[int, int]] = []

    def seen_link(self, url: str) -> bool:
        """Whether a photo at this link, at any size, was already kept."""
        return self.kept_at_link(url) is not None

    def kept_at_link(self, url: str) -> int | None:
        """The number of the kept photo at this link, at any size, if one was kept."""
        return self._keys.get(same_key(url))

    def is_new(self, url: str, content: bytes) -> bool:
        """Whether the photo isn't a copy of one already kept; if so, it counts as kept."""
        return self.copy_of(url, content) is None

    def copy_of(self, url: str, content: bytes) -> int | None:
        """The number of the kept photo this one is a copy of, or None if it is new; if so,
        it counts as kept."""
        kept = self.kept_at_link(url)
        if kept is not None:
            return kept
        try:
            fingerprint = dhash(content)
        except OSError, ValueError, Image.DecompressionBombError:
            fingerprint = None
        if fingerprint is not None:
            for kept_hash, number in self._hashes:
                if (fingerprint ^ kept_hash).bit_count() <= MOST_BITS_APART:
                    return number
        number = len(self._keys)
        self._keys[same_key(url)] = number
        if fingerprint is not None:
            self._hashes.append((fingerprint, number))
        return None


@contextmanager
def any_size() -> Iterator[None]:
    """Let Pillow open a picture of any size: a full-page screenshot is far taller than its
    guard against picture bombs allows. Only for pictures that came from Firecrawl. The
    guard is Pillow's own setting, so it is off for the whole process meanwhile; nothing
    else opens pictures while the screenshot is cut, as the copy call shows none."""
    limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def parts(screenshot: bytes) -> list[tuple[int, bytes]]:
    """The screenshot cut into parts PART_HEIGHT tall from the top, blank ones left out, at
    most MOST_PARTS: each part's top, in pixels down the page, and the part as a JPEG."""
    with any_size(), Image.open(io.BytesIO(screenshot)) as opened:
        page = opened.convert("RGB")
    cut: list[tuple[int, bytes]] = []
    for top in range(0, page.height, PART_HEIGHT):
        part = page.crop((0, top, page.width, min(top + PART_HEIGHT, page.height)))
        small = part.resize((96, max(1, part.height // 20)))
        if max(high - low for low, high in ImageStat.Stat(small).extrema) < LEAST_COLOUR_RANGE:
            continue
        file = io.BytesIO()
        part.save(file, "JPEG", quality=80)
        cut.append((top, file.getvalue()))
        if len(cut) >= MOST_PARTS:
            break
    return cut
