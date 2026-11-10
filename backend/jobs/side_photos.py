"""The side picker and true crops (#524).

One vision call per ad (`pick_side_photos`) is shown every page photo in the library, one
image each, and answers which sides of the product the photos show (up to three photos for
each, best first), a box around the product in every photo that shows it, and the photos it
ranked for no side. It is asked the first time a scene picture needs a crop, under a lock so
parallel scenes never pay twice, and its answer is kept on the job (`Job.side_photos`), so
it is asked once per ad. When it fails or is refused, that is kept too: the ad's picks go as
the redrawn cut-outs (jobs.product_only) and it isn't asked again.

A true crop is cut by code from the photo's real pixels, never drawn: the box widened by a
margin (`cut`). It is made once and kept as a version of its photo in the library
(TrueCrop), so the face swap and recolour work on it as on any library item; it is never
shown to the planner or the scene agent as an item to pick.

`for_a_pick` is what a scene agent's pick goes as. The sides are for #525."""

import io
import logging
from typing import Any, Literal

import PIL.Image
import PIL.ImageOps
from pydantic import BaseModel, Field

from adforge import file_store
from chat.models import Message
from gateway.gateway import call_model
from gateway.types import Handoff, Image

from . import notices, product_only
from .cut_and_send import ad_colour
from .job_locks import LIBRARY_LOCK, SIDE_PHOTOS_LOCK, one_job_at_a_time
from .library import LibraryEntry, PagePhoto, PictureLabel, TrueCrop, add_to_library, library_of
from .library_items import MOST_PRODUCT_TEXT, WHY_IT_FAILED, in_the_library, needs_product_only
from .models import Job

logger = logging.getLogger(__name__)

# Its examples are general, never from a tested product (CODING_STANDARDS.md).
INSTRUCTIONS = """\
You choose the page photos that show one product from every side, for an AI picture model \
and two checkers. They will see the photos cropped to the boxes you draw, so they learn the \
product's real shape, parts, printed words and colour from them.

You are shown every photo of the product from its page, one by one, each named "Photo N", \
and told what the page's labeller wrote about each: what it shows, its kind, side, whether \
the product is alone in it, and its colour. Some show the product alone; some show it with \
people, hands or other things. A photo with a person counts as much as one without: it may \
be the only photo of a side.

1. sides: name each side or part of THIS product that the photos show, such as the front, \
the back, the left side, the top, the bottom, the open lid, the controls or the inside. \
Only this exact product: never another size, a variant, a bundle, a refill or a different \
product shown beside it.
2. For each side, rank up to three photos that show it, best first. Best means that side \
faces the camera, the product is big and sharp in the photo, and little of it is covered. \
Prefer the ad's colour; take a photo in another colour only when no photo in the ad's \
colour shows that side. Near-copies of the same view count once: rank only one of them.
3. For each photo you rank, say in one line what its box shows: the side, and anything \
else in it, such as "a hand covers the handle".
4. boxes: draw a box around this product in every photo it shows, ranked or not: the whole \
product, as tightly as you can without cutting any of it off. Whatever covers the product, \
such as a hand holding it, stays inside the box. In a photo of several products, box only \
this one. Give the box as fractions of the photo's width and height, from 0 to 1, with \
(0, 0) the top-left corner.
5. left_out: every photo you ranked for no side, with why in a few words, such as "another \
size", "a copy of photo 3", "the product is too small" or "the product doesn't show"."""


class PickerHandoff(Handoff):
    """The product's own text, the ad's colour, and a line for each photo shown."""

    product: str
    ad_colour: str
    photos: list[str]


class Box(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class Ranked(BaseModel):
    photo: int = Field(description="The photo's number.")
    shows: str = Field(description="One line on what its box shows.")


class SidePick(BaseModel):
    side: str
    ranked: list[Ranked] = Field(description="Up to three photos, best first.")


class Boxed(BaseModel):
    photo: int = Field(description="The photo's number.")
    box: Box


class LeftOut(BaseModel):
    photo: int
    why: str


class Answer(BaseModel):
    sides: list[SidePick]
    boxes: list[Boxed] = Field(description="A box around the product in every photo it shows.")
    left_out: list[LeftOut]


# A box covering this much of the photo already fills it: the photo goes whole.
FILLS_PHOTO = 0.6
# The margin on each side of a box: this share of the box, and at least this share of the photo.
MARGIN = 0.08
SMALLEST_MARGIN = 0.02
# A crop whose longest side is under this many pixels of the photo is too small to send.
SMALLEST_PIXELS = 320
# A box narrower or shorter than this share of the photo is empty.
SMALLEST_BOX = 0.01

WHOLE: Literal["whole"] = "whole"
TOO_SMALL: Literal["too small"] = "too small"
NO_BOX: Literal["no box"] = "no box"
type Cut = tuple[int, int, int, int] | Literal["whole", "too small", "no box"]


def cut(box: Box, width: int, height: int) -> Cut:
    """Where to crop a photo `width` by `height` px for `box`: (left, top, right, bottom) in
    px, the box widened by its margin and kept inside the photo; WHOLE when the box covers
    FILLS_PHOTO of it or more; TOO_SMALL when the crop's longest side is under
    SMALLEST_PIXELS; NO_BOX when the box is empty or off the photo."""
    left, right = sorted(min(max(edge, 0.0), 1.0) for edge in (box.left, box.right))
    top, bottom = sorted(min(max(edge, 0.0), 1.0) for edge in (box.top, box.bottom))
    if right - left < SMALLEST_BOX or bottom - top < SMALLEST_BOX:
        return NO_BOX
    if (right - left) * (bottom - top) >= FILLS_PHOTO:
        return WHOLE
    left, right = _widened(left, right)
    top, bottom = _widened(top, bottom)
    piece = (round(left * width), round(top * height), round(right * width), round(bottom * height))
    if max(piece[2] - piece[0], piece[3] - piece[1]) < SMALLEST_PIXELS:
        return TOO_SMALL
    return piece


def _widened(start: float, end: float) -> tuple[float, float]:
    margin = max((end - start) * MARGIN, SMALLEST_MARGIN)
    return max(0.0, start - margin), min(1.0, end + margin)


def for_a_pick(job: Job, item: LibraryEntry, *, scene: int) -> LibraryEntry:
    """What a scene agent's pick of the library original `item` goes as in a scene picture:
    `item` itself when it needs no cut-out (jobs.library_items.needs_product_only); else its
    true crop; `item` whole when the picker's box gives no usable crop (said for the
    developer); or, when the side picker failed for this ad, its redrawn cut-out."""
    if not needs_product_only(item):
        return item
    if not isinstance(item.source, PagePhoto):
        # The side picker sees only the page's photos: a shop owner's photo keeps its redraw.
        return product_only.to_send(job, item)
    answer = answer_of(job)
    if answer is None:
        return product_only.to_send(job, item)
    box = next((each.box for each in answer.boxes if each.photo == item.number), None)
    if box is None:
        _say_once(job, f"{_named(item, scene)} goes whole: the side picker gave no box for it.")
        return item
    with one_job_at_a_time(LIBRARY_LOCK, job):
        made = in_the_library(job, TrueCrop(of=item.number))
        if made is not None:
            return made
        with PIL.Image.open(io.BytesIO(file_store.read(item.file))) as opened:
            photo = PIL.ImageOps.exif_transpose(opened).convert("RGB")
        where = cut(box, *photo.size)
        if where == WHOLE:
            return item
        if where == NO_BOX:
            _say_once(
                job,
                f"{_named(item, scene)} goes whole: the side picker's box for it is empty or "
                "off the photo.",
            )
            return item
        if where == TOO_SMALL:
            _say_once(
                job,
                f"{_named(item, scene)} goes whole: its crop at the side picker's box is too "
                f"small (under {SMALLEST_PIXELS} px on its longest side).",
            )
            return item
        data = io.BytesIO()
        photo.crop(where).save(data, "PNG")
        file = file_store.save(f"jobs/{job.pk}/true-crops/{item.number}.png", data.getvalue())
        label = PictureLabel.model_validate(item.model_dump(include=set(PictureLabel.model_fields)))
        return add_to_library(job, file, label, TrueCrop(of=item.number))


def answer_of(job: Job) -> Answer | None:
    """The side picker's answer for `job`, asked now unless it is kept; None when it failed
    or was refused, now or before (said for the developer once)."""
    with one_job_at_a_time(SIDE_PHOTOS_LOCK, job):
        job.refresh_from_db(fields=["side_photos"])
        kept: dict[str, Any] = job.side_photos or {}
        if "answer" in kept:
            return Answer.model_validate(kept["answer"])
        if "failed" in kept:
            return None
        try:
            answer = _ask(job)
        except tuple(WHY_IT_FAILED) as error:
            why = next(text for kind, text in WHY_IT_FAILED.items() if isinstance(error, kind))
            logger.warning("The side picker failed for job %s: %s", job.pk, error)
            job.side_photos = {"failed": why}
            job.save(update_fields=["side_photos"])
            notices.post_notice(
                job,
                notices.SIDE_PICKER_FAILED,
                f"The side picker (the call that boxes the product in the page photos) failed "
                f"({why}), so this ad's picks that aren't the product alone go as redrawn "
                "cut-outs. It isn't asked again for this ad.",
                Message.Level.PROBLEM,
            )
            return None
        job.side_photos = {"answer": answer.model_dump()}
        job.save(update_fields=["side_photos"])
        return answer


def _ask(job: Job) -> Answer:
    photos = [item for item in library_of(job) if isinstance(item.source, PagePhoto)]
    return call_model(
        job=job,
        purpose="pick_side_photos",
        instructions=INSTRUCTIONS,
        handoff=PickerHandoff(
            product=job.page_text[:MOST_PRODUCT_TEXT],
            ad_colour=ad_colour(job),
            photos=[
                f"Photo {item.number}: {item.label} (kind {item.kind}, side {item.side}, alone "
                f"{item.alone}, colour {item.colour})"
                for item in photos
            ],
        ),
        output=Answer,
        images=[Image(label=f"Photo {item.number}:", key=item.file) for item in photos],
        pay_once=True,
    )


def _named(item: LibraryEntry, scene: int) -> str:
    return f"Scene {scene}'s pick of library item {item.number} ({item.label})"


def _say_once(job: Job, text: str) -> None:
    """Record `text` for the developer, unless the job was told exactly that."""
    notices.post_once(job, notices.NO_TRUE_CROP, text)
