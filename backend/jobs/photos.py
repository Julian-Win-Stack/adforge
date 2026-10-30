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
from typing import Any, NamedTuple, Self
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image, ImageStat
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gateway.types import Handoff

from .library import Alone, Kind, PictureLabel, Side

# The picker's label rules for one picture's fixed fields (jobs.library.PictureLabel): the
# picker's own, and the labelling call's for a photo the picker never saw.
LABEL_FIELDS = """\
- kind: side (how the product looks from one side), state (the product in another state of \
its own, such as open, unfolded or another pack size), content (what the product holds, out of \
it), in-use (the product being used or worn), result (what using it leaves, the end state, a \
before/after), line-up (this product among other products), chart (a chart, table or diagram).
- side: the side of the product facing the camera: front, back, side or top; none when no one \
side of it shows.
- alone: yes when the product is alone on a plain background, no when anything else is in the \
picture.
- colour: the product's colour in the picture, one word; join two with a hyphen.
- target: for a line-up, where this product stands among the others, so it can be told apart \
from them; empty for every other kind.
- label: one sentence saying what the picture shows, as you would tell someone who can't see \
it: the product, its state, what else is in it. For a before/after, say which part is before \
and which is after."""

PICK_INSTRUCTIONS = f"""\
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
- gallery_images: every picture in that product's own photo gallery (the big photo next to \
the title and buy button, its thumbnails, and the rest of that gallery), whatever it shows: \
any angle, close-up, packaging, the product open or in another state, in use or worn by a \
model, what is inside it, the result of using it, a before/after, a chart, the product next \
to other things. A picture's place in this product's own gallery is what makes it this \
product: keep it even when it shows another colour, shade, size or version of the product \
than the one chosen on the page, and give it the colour it really shows. Leave out only \
cards made of words alone, and a picture that shows only another product: a different item, \
not this one in another colour, shade, size or version.
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
a routine or a comparison of versions, is kept as a line-up when this exact product is in it, \
with where it stands among them (see target below). Also leave out reviews, Q&A, navigation, \
cart, promotions and footer text. When unsure whether a product in a picture outside this \
product's own gallery is exactly this product, leave it out: a missing photo costs little, a \
wrong one puts another product in the advert. These rules sort the pictures outside the \
gallery; they never take a picture out of this product's own gallery.

You also get the shop's official record of the product sold on this page (title, brand, \
description, the names of its colours, shades or sizes) and its official photos, one per \
colour or shade, labelled R1, R2, ... Use them to know exactly what this product looks like \
and to tell it apart from look-alikes: outside this product's own gallery, a picture shows \
this product only if the product in it matches one of the official photos (any colour, shade \
or size in the record). A different shape, label, model or design is another product, even \
from the same brand. The official photos are a guide only: answer with I numbers from the \
page, never R numbers.

Then note faces: face_images are the I numbers, among the ones you picked, of pictures that \
show a person's face you could recognise. A body, a hand, an arm, lips or a face turned away, \
cut off or too small to recognise doesn't count.

Then page_blocked: true when the screenshots show a block, error, login or "verify you're \
human" page instead of the shop's product page.

Last, pictures: one entry for every picture you picked, in gallery_images, more_images or \
record_images, each with these fields:
- picture: its mark, "I<n>", or "R<n>" for an official photo.
- copy_group: a number shared by every copy of one photo: the same shot at another size, crop \
or file, on the page or among the official photos. Every other photo gets a number of its own. \
Pick every copy as usual; the group says they are one photo, and one copy is kept.
{LABEL_FIELDS}

Three more kinds of picture come with the screenshots:

- Hidden pictures. Some numbered pictures could not be drawn in the screenshots because they \
sit inside a scroll box or behind a carousel slide, so you only see their number in the lists. \
Those are given to you as their own small pictures, labelled "I<n> (hidden)". The hidden-picture \
list says which box each one sits in and which drawn pictures share that box: a hidden picture \
that shares the gallery's box with gallery pictures is usually another gallery photo. Judge them \
by the same rules and answer with their I numbers in gallery_images or more_images.

- The official photos R1, R2, ... are no longer a guide only: when an official photo shows \
this product and no picked I picture is the same photo, put its R number in record_images. \
Leave out official photos of other products or bundles by the same rules; keep a line-up with \
this product in it as above.

- Video players V1, V2, ... are the page's videos, each shown as its poster or first frame, \
with where it sits (box, position, and which pictures share its box). Answer gallery_videos \
with the V numbers of every video that sits in THIS product's own photo gallery: placed with \
the product photos, as one of the gallery's slides or thumbnails. Where a video sits decides, \
not what its frame shows: the frame only tells the players apart and is not used to judge what \
the video shows, which a later step does by watching it. So the rules above about other \
products and about leaving a picture out when unsure are for pictures only: never leave out a \
gallery video because its frame looks like another product, shade or version. Never a video \
in a reviews, customer, creator, "shop the look", social or shoppable-video section, and never \
a muted background banner. Say in video_notes why you left out each video you did not pick."""

# A photo the picker never saw gets its Face note from a call of its own, worded as the
# picker's note (tested on the 8 test products' 74 photos: backend/broll-test/face_notes.py).
NOTE_FACE_INSTRUCTIONS = """\
You look at one product photo from a shop's page, for an advert. Say whether it shows a \
person's face you could recognise: has_face. A body, a hand, an arm, lips or a face turned \
away, cut off or too small to recognise doesn't count."""

# A photo the picker never saw (one the shop owner sent, or one the page declares) is given
# the picker's fixed fields by a call of its own, worded by the picker's label rules.
LABEL_INSTRUCTIONS = f"""\
You look at one photo for an advert of a shop's product, to say what it shows, as the photos \
picked off the product's page are labelled. You get the photo, what it was sent or kept to \
show (shows), and the product's own text off its page. "The product" below is what the photo \
shows. Answer these fields:
{LABEL_FIELDS}"""

NO_RECORD = "Official record of the product: none was found for this page."

# At most this many of the record's official photos are shown, each picture once.
MOST_RECORD_PHOTOS = 24
# The hidden pictures shown: the biggest this many, at least LEAST_HIDDEN_SIDE px a side on the
# page, in page order.
MOST_HIDDEN = 40
LEAST_HIDDEN_SIDE = 100
# The video players shown, in page order, each video once.
MOST_VIDEOS = 20
# A hidden picture's or video player's line names at most this many drawn pictures in its box.
MOST_BOX_MATES = 8
# The screenshot is cut into parts this tall, from the top, and at most this many are shown.
PART_HEIGHT = 1_200
MOST_PARTS = 14
# A part whose colours vary less than this is blank, and isn't shown.
LEAST_COLOUR_RANGE = 12
# A text piece longer than this is cut to its start and end.
LONGEST_TEXT_PIECE = 2_000
# Two photos whose fingerprints differ in at most this many of their 256 bits are copies.
MOST_BITS_APART = 12
# A picture under this many pixels on either side is never a product photo.
SMALLEST_PHOTO = 100
# The size Shopify's image server was asked for in a file name, just before its type:
# `_<w>x<h>`, `_x<h>` or one of its old size names, with any crop and pixel density (`@2x`)
# after it.
SHOPIFY_SIZE = re.compile(
    r"_(?:\d+x\d*|x\d+|pico|icon|thumb|small|compact|medium|large|grande|original|master)"
    r"(?:_crop_[a-z]+)?(?:@\dx)?(?=\.)",
    re.I,
)


class PickHandoff(Handoff):
    official_record: str  # The record as text, or NO_RECORD.
    page_url: str
    page_title: str
    numbered_pictures: str
    numbered_text_pieces: str
    hidden_pictures: str
    official_photos: str
    video_players: str


class PickedPicture(BaseModel):
    """The picker's fields for one picture it kept: its mark, its copy group and the
    library's picture label. The call's schema checks the fixed values; the label's own rules
    (one colour word, a target for a line-up only, a sentence) are the library's, checked
    picture by picture in `label_of`, so one picture that breaks them loses only its own
    fields, never the whole pick."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    picture: str = Field(
        description='The picture\'s mark: "I<n>", or "R<n>" for an official photo.'
    )
    copy_group: int = Field(
        description="The same number for every copy of one photo (another size, crop or file "
        "of the same shot); a different number for every other photo."
    )
    kind: Kind
    side: Side
    alone: Alone
    colour: str = Field(
        description="The product's colour in the picture, one word; two joined by a hyphen."
    )
    target: str = Field(
        description='For a line-up, where this product stands among the others; "" for any '
        "other kind."
    )
    label: str = Field(description="One sentence saying what the picture shows.")

    def label_of(self) -> PictureLabel:
        """The picture's label as the library keeps it, a colour of several words joined
        by hyphens. Raises ValidationError when it breaks the library's rules."""
        return PictureLabel(
            kind=self.kind,
            side=self.side,
            alone=self.alone,
            colour="-".join(self.colour.split()),
            target=self.target,
            label=self.label,
        )


class PickedPhotos(BaseModel):
    product: str
    gallery_images: list[int]
    more_images: list[int]
    # Asked for because the picker was tested with it; the text is copied by its own call.
    product_sections: list[int]
    notes: str
    # After the others, as the picker was tested with it: the marks of picked pictures with a
    # face.
    face_images: list[int]
    # Last, so the answers above come as the picker was tested with them.
    page_blocked: bool
    # After it, as the picker was tested with them on hidden pictures and video players.
    record_images: list[int] = []
    gallery_videos: list[int] = []
    video_notes: str = ""
    # Last, after every answer the picker was tested with: one entry per picture kept.
    pictures: list[PickedPicture] = []


class FaceNoteHandoff(Handoff):
    # Where the photo came from: its link on the page, or its key in the file store when the
    # shop owner attached it. Not a page photo's key, which changes when the page is read again.
    photo: str


class FaceNote(BaseModel):
    has_face: bool


class LabelPhotoHandoff(Handoff):
    # Where the photo came from: its link on the page, or its key in the file store when the
    # shop owner sent it. Not a page photo's key, which changes when the page is read again.
    photo: str
    # What the photo was sent or kept to show, such as the product the ad sells.
    shows: str
    # The product's own text off its page, so the product in the photo can be told apart.
    product_text: str


class PhotoLabel(BaseModel):
    """The labelling call's answer: a photo's fixed fields, as the picker gives them, held to
    the library's label rules by the call itself, as one photo's answer is all there is."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Kind
    side: Side
    alone: Alone
    colour: str = Field(
        description="The product's colour in the picture, one word; two joined by a hyphen."
    )
    target: str = Field(
        description='For a line-up, where this product stands among the others; "" for any '
        "other kind."
    )
    label: str = Field(description="One sentence saying what the picture shows.")

    # The label's rules (jobs.library.PictureLabel) as validators, which OpenAI's strict
    # schema leaves out: an answer that breaks one is a failed call, asked again, never kept.
    @field_validator("colour", "label")
    @classmethod
    def _said(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("it is empty")
        return value

    @model_validator(mode="after")
    def _a_target_for_a_line_up_only(self) -> Self:
        if (self.kind == "line-up") != bool(self.target.strip()):
            raise ValueError("a line-up names its target, and nothing else has one")
        return self

    def label_of(self) -> PictureLabel:
        """The photo's label as the library keeps it, a colour of several words joined by
        hyphens."""
        return PictureLabel(
            kind=self.kind,
            side=self.side,
            alone=self.alone,
            colour="-".join(self.colour.split()),
            target=self.target,
            label=self.label,
        )


def handoff(
    record: dict[str, Any] | None, marks: dict[str, Any], title: str, shown: ShownLines
) -> PickHandoff:
    """What the picker is handed: the record, the page, its numbered pictures and text, and
    the lines for the pictures shown beside the screenshot."""
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
        hidden_pictures=shown.hidden_pictures or "(none: every numbered picture was drawn)",
        official_photos=shown.official_photos or "(none)",
        video_players=shown.video_players or "(none)",
    )


class ShownLines(BaseModel):
    """The lines naming the pictures shown beside the screenshot, one per picture shown."""

    hidden_pictures: str = ""
    official_photos: str = ""
    video_players: str = ""


def numbered(marks: dict[str, Any]) -> list[dict[str, Any]]:
    """The pictures the marking script numbered, those a visitor can see, that are big
    enough to be a product photo."""
    return [
        picture
        for picture in marks.get("imgs") or []
        if isinstance(picture, dict)
        and isinstance(picture.get("n"), int)
        and not too_small(picture)
    ]


def too_small(picture: dict[str, Any]) -> bool:
    """Whether a numbered picture is under SMALLEST_PHOTO px on a side, as the browser
    loaded it. A picture of unknown size, as one not loaded yet (0 by 0), isn't, and nor is
    one with a bigger size to fetch: in its srcset, or by taking a size off its link."""
    width, height = picture.get("nw"), picture.get("nh")
    if not isinstance(width, int) or not isinstance(height, int) or not width or not height:
        return False
    if best_url(picture) != _link(picture.get("src")):
        return False
    return min(width, height) < SMALLEST_PHOTO


class PickedLink(NamedTuple):
    """A picked photo's link at its biggest size, the link as the page or record gave it,
    whether the picker marked it with a face: None for an official photo, which it doesn't
    note; and its mark, I<n> or R<n>."""

    url: str
    as_given: str
    has_face: bool | None
    mark: str


def picked_links(
    picked: PickedPhotos, marks: dict[str, Any], record_urls: list[str]
) -> list[PickedLink]:
    """The picked photos' links, the gallery's, then the rest of the page's, then the
    record's official photos. Numbers the page or record didn't show are dropped, and so are
    marks of pictures that weren't picked."""
    by_number = {picture["n"]: picture for picture in numbered(marks)}
    return [
        PickedLink(
            best_url(by_number[number]),
            widest_url(by_number[number]),
            number in picked.face_images,
            f"I{number}",
        )
        for number in [*picked.gallery_images, *picked.more_images]
        if number in by_number
    ] + [
        PickedLink(full_size(record_urls[number - 1]), record_urls[number - 1], None, f"R{number}")
        for number in picked.record_images
        if 1 <= number <= len(record_urls)
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


def record_photo_urls(record: dict[str, Any]) -> list[str]:
    """Every official photo in the record, each picture once: its variants' photos, then the
    product's own, at most MOST_RECORD_PHOTOS. The picker knows them as R1, R2, ..."""
    names: set[str] = set()
    urls: list[str] = []
    images = [image for variant in _variants(record) for image in variant.get("images") or []]
    for image in [*images, *(record.get("images") or [])]:
        url = _link(image.get("url") if isinstance(image, dict) else image)
        if url and _name(url) not in names:
            names.add(_name(url))
            urls.append(url)
    return urls[:MOST_RECORD_PHOTOS]


def hidden(marks: dict[str, Any]) -> list[dict[str, Any]]:
    """The numbered pictures the screenshot couldn't draw that the picker is shown: the
    MOST_HIDDEN biggest at least LEAST_HIDDEN_SIDE px a side, in page order."""
    undrawn = [
        picture
        for picture in numbered(marks)
        if picture.get("shown") is False
        and _side(picture, "w") >= LEAST_HIDDEN_SIDE
        and _side(picture, "h") >= LEAST_HIDDEN_SIDE
    ]
    biggest = sorted(undrawn, key=lambda p: -_side(p, "w") * _side(p, "h"))[:MOST_HIDDEN]
    return sorted(biggest, key=lambda picture: picture["n"])


def hidden_line(picture: dict[str, Any], marks: dict[str, Any]) -> str:
    """What the picker is told of a hidden picture: how much of it shows, its size, where it
    is, what hides it and which drawn pictures share its box."""
    box = picture.get("box") or 0
    mates = box_mates(marks, box)
    return (
        f"I{picture['n']} (hidden): {round(float(picture.get('vis') or 0) * 100)}% visible, "
        f"{_side(picture, 'w')}x{_side(picture, 'h')} px at {_side(picture, 'y')} px down the "
        "page, "
        + (
            f"cut off by box {box}, "
            if box and picture.get("disp", True)
            else "not displayed at all (a collapsed menu, popup or inactive tab), "
        )
        + (
            f"box shared with drawn pictures {', '.join(mates)}"
            if mates
            else "no drawn picture shares its box"
        )
    )


def box_mates(marks: dict[str, Any], box: int) -> list[str]:
    """The drawn pictures in `box`, by their labels, at most MOST_BOX_MATES."""
    if not box:
        return []
    return [
        f"I{picture['n']}"
        for picture in numbered(marks)
        if picture.get("box") == box and picture.get("shown", True)
    ][:MOST_BOX_MATES]


def _side(picture: dict[str, Any], key: str) -> int:
    value = picture.get(key)
    return round(value) if isinstance(value, int | float) else 0


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
    name = urlparse(full_size(url)).path.rstrip("/").split("/")[-1]
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
    return full_size(widest_url(picture))


def widest_url(picture: dict[str, Any]) -> str:
    """The link to the widest size of a picture its srcset offers, else its src, as the
    page gives it."""
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
    return url


def full_size(url: str) -> str:
    """`url` with the size asked of the shop's image server taken off: Shopify's width,
    height and crop, and its `_<w>x<h>` size in the file name; Amazon's `._..._.` part.
    Other shops' links are left as they are."""
    parts = urlparse(url)
    if "/cdn/shop/" in parts.path or "cdn.shopify.com" in parts.netloc:
        kept = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in ("width", "height", "crop")
        ]
        path = SHOPIFY_SIZE.sub("", parts.path)
        return urlunparse(parts._replace(path=path, query=urlencode(kept)))
    if "media-amazon.com" in parts.netloc:
        return urlunparse(parts._replace(path=re.sub(r"\._[^/]*_\.", ".", parts.path), query=""))
    return url


def same_key(url: str) -> str:
    """What one photo's links have in common at every size: host and path, with Shopify's
    `_<w>x<h>` size part taken off."""
    parts = urlparse(full_size(url))
    return parts.netloc + re.sub(r"_\d+x\d*(?=\.)", "", parts.path)


def size_of(content: bytes) -> tuple[int, int] | None:
    """A photo's width and height in pixels, or None when it can't be read as an image."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            return image.size
    except OSError:
        return None


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


def fingerprint_of(content: bytes) -> int | None:
    """The photo's fingerprint (dhash), or None when it can't be read as a photo."""
    try:
        return dhash(content)
    except OSError, ValueError, Image.DecompressionBombError:
        return None


def alike(one: int, other: int) -> bool:
    """Whether two fingerprints are of copies of one photo: at most MOST_BITS_APART apart."""
    return (one ^ other).bit_count() <= MOST_BITS_APART


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
        fingerprint = fingerprint_of(content)
        if fingerprint is not None:
            for kept_hash, number in self._hashes:
                if alike(fingerprint, kept_hash):
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
