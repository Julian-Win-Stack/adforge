"""A silent scene's starting picture made from the shop's own product photo (#772): the photo
fitted to the tall frame by code, its pixels kept, so the buyer sees the exact product.

A photo a little too wide (or any photo taller than the frame) is cropped around the product.
A much wider one, such as a square gallery photo, would lose too much to a crop: it is put
whole across the tall frame, the picture model fills the bands above and below with more of
its background, and code pastes the photo's own pixels back over the middle, so the product
is never redrawn."""

import io
from dataclasses import dataclass
from typing import Literal

import PIL.Image
import PIL.ImageOps

from adforge import file_store
from gateway.gateway import edit_picture
from jobs.models import Job

from .assembly import FRAME_HEIGHT, FRAME_WIDTH

TALL = FRAME_WIDTH / FRAME_HEIGHT
# A crop keeps at least this share of a wide photo's width; a wider photo has its
# background extended instead, as cropping a square photo to the tall frame keeps only 56%
# of it and could cut the product off.
LEAST_WIDTH_KEPT = 0.8
# A photo narrower than this is too small to fill the tall frame without going soft.
SMALLEST_WIDTH = 400
# A crop keeps at least this share of a tall photo's height: more lost could cut the
# product off, and a background can't be extended sideways from a strip.
LEAST_HEIGHT_KEPT = 0.6
# How far the extended picture's shape may be from the tall frame's before the photo's
# pixels can't be laid back where they were.
SHAPE_TOLERANCE = 0.05
# The rows over which the photo's pasted edge fades into the drawn bands, so no seam shows.
MOST_FEATHER_ROWS = 24

EXTEND_PROMPT = (
    "Image 1 is a product photo placed in the middle of a tall frame, with empty grey bands "
    "above and below it. Fill only the empty bands: continue the photo's own background, "
    "surface, light and colours outward, seamlessly, so the whole frame reads as one tall "
    "photo. Add no new objects, no text, no people and no second product. Leave the photo in "
    "the middle exactly as it is."
)


class PhotoNotFitted(Exception):
    """The photo can't be fitted to the tall frame; the message says why."""


class PixelsNotPasted(Exception):
    """The extended picture came back in a shape the photo's pixels can't be laid back on."""


@dataclass(frozen=True)
class Fitted:
    """The starting picture made from the photo, and how: "cropped" by code alone, or
    "extended" by the picture model with the photo's pixels pasted back."""

    file: str
    how: Literal["cropped", "extended"]


def fit(
    job: Job, photo: str, focus_x: int = 50, focus_y: int = 50, extended: str | None = None
) -> Fitted:
    """The photo at `photo` fitted to the tall frame: cropped around `focus_x`, `focus_y`
    (percent of its width and height) when a crop keeps enough of it, else extended (with
    `extended`, the picture model's bands already drawn for it, not paid for again)."""
    picture = _opened(photo)
    width, height = picture.size
    if width < SMALLEST_WIDTH:
        raise PhotoNotFitted(f"it is only {width} pixels wide, too small to fill a tall frame")
    if width / height <= TALL:
        kept = round(width / TALL)
        if kept < height * LEAST_HEIGHT_KEPT:
            raise PhotoNotFitted(
                f"it is so tall that the frame would keep only {kept * 100 // height}% of it "
                "and could cut the product off"
            )
        top = _start(height, kept, focus_y)
        return Fitted(_tall(picture.crop((0, top, width, top + kept))), "cropped")
    kept = round(height * TALL)
    if kept >= width * LEAST_WIDTH_KEPT:
        left = _start(width, kept, focus_x)
        return Fitted(_tall(picture.crop((left, 0, left + kept, height))), "cropped")
    return Fitted(_extended(job, picture, extended), "extended")


def _extended(job: Job, picture: PIL.Image.Image, drawn_before: str | None) -> str:
    """`picture` whole across the tall frame's width, the bands above and below drawn by the
    picture model (or `drawn_before`), and its own pixels pasted back over the middle."""
    shown = _across(picture)
    top = (FRAME_HEIGHT - shown.height) // 2
    key = drawn_before
    if key is None:
        canvas = PIL.Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (128, 128, 128))
        canvas.paste(shown, (0, top))
        key = edit_picture(
            job=job,
            purpose="extend_real_photo",
            prompt=EXTEND_PROMPT,
            pictures=[_saved(canvas, "real-photo-canvas.png")],
            keep_shape=True,
        )
    drawn = _opened(key)
    if abs(drawn.width / drawn.height - TALL) > SHAPE_TOLERANCE:
        raise PixelsNotPasted(f"the extended picture came back {drawn.width}x{drawn.height}")
    drawn = drawn.resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS)
    drawn.paste(shown, (0, top), _feathered(shown.height))
    return _saved(drawn, "real-photo-extended.png")


def _feathered(height: int) -> PIL.Image.Image:
    """The paste mask of a photo `height` rows tall: whole, fading over its top and bottom
    rows."""
    mask = PIL.Image.new("L", (FRAME_WIDTH, height), 255)
    feather = feather_rows(height)
    for row in range(feather):
        value = round(255 * (row + 1) / (feather + 1))
        for y in (row, height - 1 - row):
            mask.paste(value, (0, y, FRAME_WIDTH, y + 1))
    return mask


def feather_rows(height: int) -> int:
    """How many rows at each edge of a pasted photo `height` rows tall are faded."""
    return min(MOST_FEATHER_ROWS, height // 10)


def _across(picture: PIL.Image.Image) -> PIL.Image.Image:
    height = round(picture.height * FRAME_WIDTH / picture.width)
    return picture.resize((FRAME_WIDTH, height), PIL.Image.Resampling.LANCZOS)


def _tall(cut: PIL.Image.Image) -> str:
    return _saved(
        cut.resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS), "real-photo.png"
    )


def _start(size: int, kept: int, centre: int) -> int:
    """Where a crop `kept` long starts along a side `size` long, centred on `centre` percent
    and kept inside the photo."""
    start = round(size * min(max(centre, 0), 100) / 100 - kept / 2)
    return min(max(start, 0), size - kept)


def _opened(key: str) -> PIL.Image.Image:
    """The picture at `key` upright, as a phone's note in it says, with any see-through
    background laid on white."""
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        upright = PIL.ImageOps.exif_transpose(opened).convert("RGBA")
    flat = PIL.Image.new("RGBA", upright.size, "white")
    flat.alpha_composite(upright)
    return flat.convert("RGB")


def _saved(picture: PIL.Image.Image, name: str) -> str:
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return file_store.save(name, out.getvalue())
