"""Building a job's library (spec #210 point 1): what puts items into it, and its text list.

Items enter at the next library number (jobs.library.add_to_library) and keep it for the life
of the job:
- `add_kept_photos(job)`: every product photo the job keeps (page photos, and the photos the
  shop owner sent before the plan), in the order they were kept, that isn't in the library
  yet. Run after the page is read and whenever photos are added, so a photo added later joins
  at the next number. A page photo the picker labelled carries the picker's fields; a photo
  the shop owner sent is given them by the labelling call (and a page photo the picker gave
  none, only with LABEL_UNPICKED_PAGE_PHOTOS on).
- `add_owner_photo(job, original, file, shows)`: one photo the shop owner sent, as `file`
  (the clean picture drawn from it, or the photo itself), labelled by the labelling call.
- `label_photo(job, key, source, shows)`: the labelling call alone, for a photo the picker
  never saw.
Each is safe to run again, as after a worker stopped: an item already in the library is
handed back, and the labelling call pays once per photo.

The video pictures cut from page videos are not library items: the watcher's look frames
take their place (#221)."""

import logging
from typing import assert_never

from pydantic import ValidationError

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import UnreadableImage, call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Image,
    UnusableReply,
)

from . import photos
from .library import (
    LibraryEntry,
    LookFrame,
    OwnerPart,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    ProductOnly,
    Recoloured,
    Source,
    add_to_library,
    entry,
    library_of,
)
from .models import Job, ProductPhoto
from .notices import post_notice

logger = logging.getLogger(__name__)

# Whether a page photo the picker gave no fields (the page's declared photos, kept when the
# picker couldn't be used, or a picture it picked without them) is labelled by the labelling
# call and enters the library. Off: it stays out of the library, as the ticket names only the
# owner's photos for the labelling call, and the existing flows' calls stay as they were.
# Every path to such a photo already posts its own red notice (the declared photos used
# instead, or the picture kept unlabelled). Flagged for Julian (#220 notes).
LABEL_UNPICKED_PAGE_PHOTOS = False

# What a product photo is kept to show, as the labelling call is told it.
THE_PRODUCT = "the product the advert is for"
# The labelling call is given at most this much of the product's own text off its page.
MOST_PRODUCT_TEXT = 4_000


def add_kept_photos(job: Job) -> list[LibraryEntry]:
    """Put every product photo `job` keeps that isn't in its library yet into it, in the
    order they were kept, and give back the items added. A photo the labelling call can't
    label is left out, with a notice, and tried again the next time."""
    added = []
    for photo in job.photos.filter(from_video=None).order_by("position"):
        source = _source_of(photo)
        if _in_the_library(job, source) is not None:
            continue
        if photo.source_url and not photo.kind and not LABEL_UNPICKED_PAGE_PHOTOS:
            logger.warning("Page photo %s has no picker's fields: not in the library", photo.pk)
            continue
        label = _picked_as(photo) or label_photo(
            job,
            photo.file,
            photo.source_url or photo.file,
            THE_PRODUCT,
            named=f"the page's photo at {photo.source_url}"
            if photo.source_url
            else "a photo the shop owner sent",
        )
        if label is not None:
            added.append(add_to_library(job, photo.file, label, source))
    return added


def add_owner_photo(job: Job, original: str, file: str, shows: str) -> LibraryEntry | None:
    """Put the shop owner's photo `original` into `job`'s library as `file`, labelled as a
    photo kept to show `shows`, and give back its item: the one already there for the same
    photo and file, if any. None when the labelling call can't label it, which is said."""
    source = OwnerPhoto(original=original)
    if (had := _in_the_library(job, source, file)) is not None:
        return had
    label = label_photo(
        job,
        file,
        original if file == original else f"{original} as {file}",
        shows,
        named="a photo the shop owner sent",
    )
    return None if label is None else add_to_library(job, file, label, source)


def label_photo(job: Job, key: str, source: str, shows: str, *, named: str) -> PictureLabel | None:
    """The picker's fixed fields for the photo at `key` in the file store, which the picker
    never saw, from the labelling call. `source` names the photo by where it came from, so
    a photo stored again under a new key pays for nothing twice. When the call fails, or its
    answer breaks the label's rules (jobs.library.PictureLabel), a red notice naming it as
    `named` says so and gives back None."""
    try:
        return call_model(
            job=job,
            purpose="label_photo",
            instructions=photos.LABEL_INSTRUCTIONS,
            handoff=photos.LabelPhotoHandoff(
                photo=source,
                shows=shows,
                product_text=job.page_text[:MOST_PRODUCT_TEXT],
            ),
            output=photos.PhotoLabel,
            images=[Image("Photo", key)],
            pay_once=True,
            images_may_move=True,
        ).label_of()
    except (
        UnusableReply,
        OutsideServiceDown,
        UnreadableImage,
        BlockedBySafetyFilter,
        AnswerTookTooLong,
        # An answer that breaks the label's own rules, which the library checks.
        ValidationError,
    ) as error:
        post_notice(
            job,
            f"Saying what {named} shows failed ({error}), so it isn't in the ad's picture "
            "library and can't be sent to the video model. It is tried again when photos are "
            "next added.",
            Message.Level.PROBLEM,
        )
        return None


def library_text(job: Job) -> str:
    """`job`'s library as a text list, one line per item: its number, what it shows, its
    colour and where it came from."""
    return "\n".join(library_line(item) for item in library_of(job))


def library_line(item: LibraryEntry) -> str:
    """One library item as a line of the text list."""
    target = f" ({item.target})" if item.target else ""
    return (
        f"{item.number}. {item.label} Kind {item.kind}{target}, side {item.side}, alone "
        f"{item.alone}. Colour {item.colour}. From {came_from(item.source)}."
    )


def came_from(source: Source) -> str:
    """Where a library item came from, in words."""
    match source:
        case PagePhoto(photo=photo):
            return f"the page, product photo {photo}"
        case OwnerPhoto(original=original):
            return f"the shop owner's photo {original}"
        case LookFrame(video=video, second=second):
            return f"page video {video} at {second:g} s"
        case OwnerPart(part=part):
            return f"the owner part {part}"
        case ProductOnly(of=of):
            return f"item {of}, the product alone"
        case Recoloured(of=of, colour=colour):
            return f"item {of}, recoloured {colour}"
        case _:
            assert_never(source)


def _source_of(photo: ProductPhoto) -> Source:
    """Where a kept product photo came from: the page, or the shop owner, who uploaded it."""
    return PagePhoto(photo=photo.position) if photo.source_url else OwnerPhoto(original=photo.file)


def _picked_as(photo: ProductPhoto) -> PictureLabel | None:
    """The picker's fields kept on a page photo; None for one it didn't label."""
    if not photo.kind:
        return None
    return PictureLabel.model_validate(
        {field: getattr(photo, field) for field in PictureLabel.model_fields}
    )


def _in_the_library(job: Job, source: Source, file: str | None = None) -> LibraryEntry | None:
    """The item of `job`'s library that came from `source` (and is `file`, when given)."""
    rows = job.library.filter(source=source.model_dump())
    if file is not None:
        rows = rows.filter(file=file)
    row = rows.order_by("number").first()
    return None if row is None else entry(row)
