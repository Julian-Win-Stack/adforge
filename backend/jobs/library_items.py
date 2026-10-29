"""Building a job's library (spec #210 point 1): what puts items into it, and its text list.

Items enter at the next library number (jobs.library.add_to_library) and keep it for the life
of the job:
- `add_kept_photos(job)`: every product photo the job keeps, in the order they were kept,
  that isn't in the library yet. Run after the page is read and whenever photos are added,
  so a photo added later joins at the next number. A page photo (off the page at its link, or
  a photo of the page pasted in the page panel) enters as the page's photo, with the picker's
  fields, or, when the picker gave it none, the labelling call's, said in a notice. A photo
  the shop owner sent (use_photos) enters as theirs, labelled by the labelling call.
- `add_owner_photo(job, original, file, shows)`: one photo the shop owner sent, as `file`
  (the clean picture drawn from it, or the photo itself), labelled by the labelling call.
  None when the call fails, which is said in the chat: the caller goes on without an item.
- `label_photo(job, key, source, shows, named=...)`: the labelling call alone, for a photo
  the picker never saw.
- `add_product_only_versions(job)`: the product-only version of each look photo that needs
  one (spec #210 point 6; `needs_product_only` decides from the fields), cut out once by the
  redraw with its check (jobs.cut_out), labelled by the labelling call, entering at the next
  number and pointing at the original. A cut-out that can't be had leaves the original to be
  sent whole, said in red once; one an outage stopped is tried again. `add_kept_photos` runs
  it after the photos it adds; a page being read, once it is kept.
- `originals(job)` and `to_send(job, item)`: a sender chooses among the originals only (never
  a ProductOnly or Recoloured item on its own), then sends, for each, its product-only
  version when it has one, else the item itself.
Each is safe to run again, as after a worker stopped: an item already in the library is
handed back, and the labelling call pays once per photo. Each holds the job's library lock
while it checks, labels and adds, so two steps adding the same photo at once add it once.

The video pictures cut from page videos are not library items: the watcher's look frames
take their place (#221). An owner part's picture enters as an OwnerPart item (#234), not
here."""

import logging
from typing import assert_never

from pydantic import ValidationError

from adforge.retry import OutsideServiceDown
from chat.models import Attachment, Message
from gateway.gateway import UnreadableImage, call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Image,
    UnusableReply,
)

from . import cut_out, photos
from .job_locks import LIBRARY_LOCK, PRODUCT_ONLY_LOCK, one_job_at_a_time
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
from .reference_photos import clean_photo_of

logger = logging.getLogger(__name__)

# What a product photo is kept to show, as the labelling call is told it.
THE_PRODUCT = "the product the advert is for"
# The labelling call is given at most this much of the product's own text off its page.
MOST_PRODUCT_TEXT = 4_000

# Why the labelling call failed, in plain words, by what went wrong.
_WHY_IT_FAILED: dict[type[Exception], str] = {
    UnusableReply: "the AI model's answer couldn't be used",
    OutsideServiceDown: "the AI service didn't answer",
    UnreadableImage: "the photo couldn't be read",
    BlockedBySafetyFilter: "the AI model's safety filter refused the photo",
    AnswerTookTooLong: "the AI model took too long to answer",
}


def add_kept_photos(
    job: Job, *, product_text: str | None = None, product_only: bool = True
) -> list[LibraryEntry]:
    """Put every product photo `job` keeps that isn't in its library yet into it, in the
    order they were kept, and give back the items added. A photo the labelling call can't
    label is left out, with a notice, and tried again the next time. The page's photos the
    picker gave no fields are labelled by the labelling call, and a notice says how many.
    The call is given `product_text`, for a page being read that isn't kept yet. Then the
    product-only versions are made (add_product_only_versions), unless not `product_only`:
    a page being read makes them once it is kept, as they are slow."""
    added: list[LibraryEntry] = []
    labelled_for_the_page = 0
    with one_job_at_a_time(LIBRARY_LOCK, job):
        for photo in job.photos.filter(from_video=None).order_by("position"):
            on_the_page = _on_the_page(photo)
            source: Source = (
                PagePhoto(photo=photo.position) if on_the_page else OwnerPhoto(original=photo.file)
            )
            if _in_the_library(job, source, None if on_the_page else photo.file) is not None:
                continue
            label = _picked_as(photo)
            if label is None:
                label = label_photo(
                    job,
                    photo.file,
                    photo.source_url or photo.file,
                    THE_PRODUCT,
                    named=_named(photo, on_the_page),
                    product_text=product_text,
                )
                if on_the_page and label is not None:
                    labelled_for_the_page += 1
            if label is not None:
                added.append(add_to_library(job, photo.file, label, source))
    if labelled_for_the_page:
        post_notice(job, _labelled_for_the_picker(labelled_for_the_page), Message.Level.INFO)
    if product_only:
        add_product_only_versions(job, product_text=product_text)
    return added


def add_owner_photo(job: Job, original: str, file: str, shows: str) -> LibraryEntry | None:
    """Put the shop owner's photo `original` into `job`'s library as `file`, labelled as a
    photo kept to show `shows`, and give back its item: the one already there for the same
    photo and file, if any. None when the labelling call can't label it, which is said."""
    source = OwnerPhoto(original=original)
    with one_job_at_a_time(LIBRARY_LOCK, job):
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


def label_photo(
    job: Job, key: str, source: str, shows: str, *, named: str, product_text: str | None = None
) -> PictureLabel | None:
    """The picker's fixed fields for the photo at `key` in the file store, which the picker
    never saw, from the labelling call: its answer's schema holds the label's rules, so an
    answer that breaks one is a failed call, never kept. `source` names the photo by where it
    came from, so a photo stored again under a new key pays for nothing twice. When the call
    fails, a red notice naming the photo as `named` says why, and None is given back. It is
    given the product's own text off its page: `product_text`, else the job's."""
    try:
        return call_model(
            job=job,
            purpose="label_photo",
            instructions=photos.LABEL_INSTRUCTIONS,
            handoff=photos.LabelPhotoHandoff(
                photo=source,
                shows=shows,
                product_text=(job.page_text if product_text is None else product_text)[
                    :MOST_PRODUCT_TEXT
                ],
            ),
            output=photos.PhotoLabel,
            images=[Image("Photo", key)],
            pay_once=True,
            images_may_move=True,
        ).label_of()
    except tuple(_WHY_IT_FAILED) as error:
        why = next(text for kind, text in _WHY_IT_FAILED.items() if isinstance(error, kind))
        post_notice(
            job,
            f"Saying what {named} shows failed ({why}), so it isn't in the ad's picture "
            "library and can't be sent to the video model. It is tried again when photos are "
            "next added.",
            Message.Level.PROBLEM,
        )
        return None


# The kinds of look photo whose product-only version is made when the product isn't alone in
# it (spec #210 point 6). A line-up always gets one, keeping only its target. Every other
# kind (content, in-use, result, chart) is sent whole.
LOOK_KINDS = ("side", "state")


def needs_product_only(item: LibraryEntry) -> bool:
    """Whether `item` gets a product-only version, decided from its fields: a look photo
    (kind side or state) that isn't the product alone, or any line-up. Only photos do: a page
    photo or a photo the shop owner sent as it was sent. A clean picture of an owner's photo
    is a cut-out already, and a look frame (a still of a page video), an owner part's picture
    and an item's versions are none of them a photo."""
    photo = isinstance(item.source, PagePhoto) or (
        isinstance(item.source, OwnerPhoto) and item.file == item.source.original
    )
    if not photo:
        return False
    return item.kind == "line-up" or (item.kind in LOOK_KINDS and item.alone == "no")


def add_product_only_versions(job: Job, *, product_text: str | None = None) -> list[LibraryEntry]:
    """Make the product-only version of each item of `job`'s library that needs one and has
    none yet, and give back those added. Each is a cut-out (jobs.cut_out) of the item's photo,
    checked to hold the product alone and unchanged, labelled by the labelling call, and
    enters the library at the next number as ProductOnly(of=<the item>). One that can't be
    had (its cut-outs keep failing their check, or a safety filter refuses it: the picture
    model's past its retry, or the check's) is not made again: the original goes whenever
    the item is sent, and a red notice says so once. One a model's outage or unusable answer
    stopped, or whose label can't be had, is said in red and tried again the next time,
    paying for no cut-out or check twice. The labelling call is given `product_text`, as
    label_photo is.

    One run at a time per job: two at once would cut the same photo out twice."""
    added: list[LibraryEntry] = []
    with one_job_at_a_time(PRODUCT_ONLY_LOCK, job):
        job.refresh_from_db(fields=["warnings"])
        items = library_of(job)
        made = {item.source.of for item in items if isinstance(item.source, ProductOnly)}
        for item in items:
            if not needs_product_only(item) or item.number in made:
                continue
            text = _sent_whole(item)
            if any(warning["text"].startswith(text) for warning in job.warnings):
                continue
            version = _product_only(job, item, product_text)
            if version is not None:
                added.append(version)
    return added


def _product_only(job: Job, item: LibraryEntry, product_text: str | None) -> LibraryEntry | None:
    """The product-only version of `item`, added to the library; None when it can't be had,
    which is said."""
    keep = _what_to_keep(item)
    try:
        file = cut_out.redraw_with_check(
            job,
            purpose="make_product_only",
            prompt=f"The photo shows: {item.label} {clean_photo_of(keep)}",
            photo=item.file,
            check=lambda picture: cut_out.check_cut_out(
                job,
                picture,
                item.file,
                keep=keep,
                photo_label=item.label,
                named=f"the product cut out of library item {item.number}",
                photo_named=f"library item {item.number}",
                needs_photo=True,
            ),
            unchecked_passes=False,
        )
    except tuple(_WHY_IT_FAILED) as error:
        why = next(text for kind, text in _WHY_IT_FAILED.items() if isinstance(error, kind))
        _tried_again(job, item, why, error)
        return None
    except cut_out.CutOutFailed as failed:
        if failed.unchecked is not None and not failed.unchecked.refused:
            _tried_again(job, item, failed.unchecked.why, failed)
            return None
        logger.warning("Library item %s's product-only version failed: %s", item.number, failed)
        post_notice(
            job,
            f"{_sent_whole(item)} {failed.why()}, so the photo is sent whole wherever it "
            "is sent, with what else it shows. The video may copy those other things.",
            Message.Level.PROBLEM,
        )
        return None
    label = label_photo(
        job,
        file,
        file,
        THE_PRODUCT,
        named=f"the product cut out of library item {item.number}",
        product_text=product_text,
    )
    return None if label is None else add_to_library(job, file, label, ProductOnly(of=item.number))


def _tried_again(job: Job, item: LibraryEntry, why: str, error: Exception) -> None:
    """Say in red, with its warning line, that `item`'s product-only version couldn't be made
    this time, for `why`, and is tried again when photos are next added."""
    logger.warning("Library item %s's product-only version wasn't made: %s", item.number, error)
    post_notice(
        job,
        f"Cutting the product out of library item {item.number} ({item.label}) didn't work "
        f"this time ({why}), so for now the photo would be sent whole, with what else it "
        "shows. It is tried again when photos are next added.",
        Message.Level.PROBLEM,
    )


def _what_to_keep(item: LibraryEntry) -> str:
    """What the product-only version of `item` keeps: the product, or, from a line-up, only
    the one product its target names."""
    if item.kind == "line-up":
        return f"the product that is {item.target}"
    return "the product"


def _sent_whole(item: LibraryEntry) -> str:
    """How the notice that `item` is sent whole starts, the same each time it is said, so it
    is said once."""
    return f"Cutting the product out of library item {item.number} ({item.label}) failed:"


def originals(job: Job) -> list[LibraryEntry]:
    """Every item of `job`'s library that isn't a version of another (ProductOnly or
    Recoloured), by number: what a sender chooses among, before `to_send` maps each to the
    item that goes."""
    return [
        item for item in library_of(job) if not isinstance(item.source, ProductOnly | Recoloured)
    ]


def to_send(job: Job, item: LibraryEntry) -> LibraryEntry:
    """The library item to send for `item`: its product-only version when it has one, else
    `item` itself, sent whole (a photo whose cut-out failed, or one that needs none).

    The contract for a sender (cut-and-send): choose among `originals(job)` only, never a
    ProductOnly or Recoloured item on its own (they carry their own fields, so walking the
    whole library would send a cut-out twice, or make one Image 1 ahead of a page photo),
    then map each choice through `to_send`, then recolour what it gives."""
    version = _in_the_library(job, ProductOnly(of=item.number))
    return item if version is None else version


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


def _on_the_page(photo: ProductPhoto) -> bool:
    """Whether a kept product photo is one of the page's: off the page at its link, or a
    photo of the page the shop owner pasted in the page panel. Else the shop owner sent it."""
    return (
        bool(photo.source_url)
        or Attachment.objects.filter(file=photo.file, message__pasted_page=True).exists()
    )


def _named(photo: ProductPhoto, on_the_page: bool) -> str:
    """A kept product photo, named for a notice."""
    if photo.source_url:
        return f"the page's photo at {photo.source_url}"
    return "a photo of the pasted page" if on_the_page else "a photo the shop owner sent"


def _labelled_for_the_picker(count: int) -> str:
    """The notice saying how many of the page's photos the labelling call labelled."""
    many = count != 1
    return (
        f"The photo picker didn't say what {count} of the page's photo{'s' if many else ''} "
        f"show{'' if many else 's'}, so a call of its own looked at "
        f"{'each' if many else 'it'} and labelled {'them' if many else 'it'} for the ad's "
        "picture library, as the picker labels the photos it picks."
    )


def _picked_as(photo: ProductPhoto) -> PictureLabel | None:
    """The picker's fields kept on a page photo; None for one it didn't label, or whose
    fields no longer fit the label's value sets (the photo is then labelled again)."""
    if not photo.kind:
        return None
    try:
        return PictureLabel.model_validate(
            {field: getattr(photo, field) for field in PictureLabel.model_fields}
        )
    except ValidationError as error:
        logger.warning("Product photo %s's picker fields no longer fit: %s", photo.pk, error)
        return None


def _in_the_library(job: Job, source: Source, file: str | None = None) -> LibraryEntry | None:
    """The item of `job`'s library that came from `source` (and is `file`, when given)."""
    rows = job.library.filter(source=source.model_dump())
    if file is not None:
        rows = rows.filter(file=file)
    row = rows.order_by("number").first()
    return None if row is None else entry(row)
