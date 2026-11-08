"""TRY (throwaway, never merged): a cropped close-up of each picked product photo, sent next to
the full photo, never in place of it. A small vision call boxes what the pick's line asks
for; code crops it with a margin. No close-up when the product already fills the photo."""

import io
import logging
import re

import PIL.Image
from pydantic import BaseModel, Field

from adforge import file_store
from gateway.gateway import call_model
from gateway.types import Handoff, Image
from jobs.models import Job

from .photos import SelectedPhoto

logger = logging.getLogger(__name__)

CROP_INSTRUCTIONS = """\
You frame a close-up of one product photo for an AI picture model. The picture model will \
get the full photo and your close-up, and draws a new picture of the product from them. \
The close-up exists so small details, such as printed words, logos, artwork and small \
parts, reach the picture model large and sharp.

You are given the product's name and one line saying what to take from this photo. Draw a \
box around everything in the photo that line asks for, and only that: the whole product or \
part it names, with all of its printed words and logos inside the box. Never cut through \
the product or part the line names. If the line names several things, box them all.

Give the box as fractions of the photo's width and height, from 0 to 1, with (0, 0) the \
top-left corner. If nothing in the photo matches the line, say so."""


class CropHandoff(Handoff):
    product_name: str
    take: str


class CropBox(BaseModel):
    found: bool = Field(description="Whether the photo shows what the line asks for.")
    what: str = Field(description="One line: what the box holds.")
    left: float
    top: float
    right: float
    bottom: float


# A close-up is skipped when the box already covers this much of the photo: the product
# fills it, so the close-up would add nothing.
FILLS_PHOTO = 0.6
# Margin added around the box on every side, as a share of the box's own width and height.
MARGIN = 0.12


# Each side of a close-up is at least this share of the photo's side, so a thin box (a ring,
# one word) keeps enough around it to be read.
SMALLEST_SIDE = 0.15
# And at least this share of the photo's side is added as margin on each side.
SMALLEST_MARGIN = 0.03


def _widen(start: float, end: float) -> tuple[float, float]:
    """One side of the box with its margin, at least SMALLEST_SIDE long, within 0 to 1."""
    margin = max((end - start) * MARGIN, SMALLEST_MARGIN)
    start, end = start - margin, end + margin
    if end - start < SMALLEST_SIDE:
        middle = (start + end) / 2
        start, end = middle - SMALLEST_SIDE / 2, middle + SMALLEST_SIDE / 2
    if start < 0:
        start, end = 0.0, end - start
    if end > 1:
        start, end = start - (end - 1), 1.0
    return max(0.0, start), min(1.0, end)


def close_up(
    job: Job | None, file: str, product_name: str, take: str
) -> tuple[str, CropBox] | None:
    """The key of a close-up of `file` around what `take` asks for, with the box, or None
    when there's nothing to crop or the product already fills the photo."""
    box = call_model(
        job=job,
        purpose="crop_close_up",
        instructions=CROP_INSTRUCTIONS,
        handoff=CropHandoff(product_name=product_name, take=take),
        output=CropBox,
        pay_once=True,
        images=[Image(label="The photo:", key=file)],
    )
    left, right = sorted((min(max(box.left, 0.0), 1.0), min(max(box.right, 0.0), 1.0)))
    top, bottom = sorted((min(max(box.top, 0.0), 1.0), min(max(box.bottom, 0.0), 1.0)))
    if not box.found or right - left < 0.02 or bottom - top < 0.02:
        return None
    if (right - left) * (bottom - top) >= FILLS_PHOTO:
        return None
    left, right = _widen(left, right)
    top, bottom = _widen(top, bottom)
    with PIL.Image.open(io.BytesIO(file_store.read(file))) as photo:
        upright = PIL.ImageOps.exif_transpose(photo)
        w, h = upright.size
        crop = upright.crop((round(left * w), round(top * h), round(right * w), round(bottom * h)))
        longest = max(crop.size)
        if longest < 1024:
            scale = 1024 / longest
            crop = crop.resize(
                (round(crop.width * scale), round(crop.height * scale)), PIL.Image.LANCZOS
            )
        if crop.mode not in ("RGB", "RGBA"):
            crop = crop.convert("RGBA" if "A" in crop.getbands() else "RGB")
        out = io.BytesIO()
        crop.save(out, format="PNG")
    return file_store.save("close_up.png", out.getvalue()), box


def with_close_ups(
    job: Job | None, photos: list[SelectedPhoto], picked: int, product_name: str, first: int
) -> list[SelectedPhoto]:
    """`photos` with a close-up right after each of the first `picked` (the picked library
    photos), every "Image N" renumbered from `first` in the new order."""
    out: list[SelectedPhoto] = []
    for index, photo in enumerate(photos):
        out.append(photo)
        if index >= picked:
            continue
        take = re.sub(r"^Image \d+:\s*", "", photo.line)
        try:
            made = close_up(job, photo.file, product_name, take)
        except Exception:
            logger.exception("Close-up failed for %s", photo.file)
            made = None
        if made is not None:
            out.append(
                SelectedPhoto(
                    file=made[0],
                    close_up=True,
                    line=f"Image 0: a close-up of image {{prev}}, for its details: {take}",
                )
            )
    renumbered: list[SelectedPhoto] = []
    for number, photo in enumerate(out, first):
        line = re.sub(r"^Image \d+\b", f"Image {number}", photo.line)
        renumbered.append(
            photo.model_copy(update={"line": line.replace("{prev}", str(number - 1))})
        )
    return renumbered
