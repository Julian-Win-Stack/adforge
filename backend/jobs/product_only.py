"""The product-only version of a library photo (spec #210 point 6, #273 points 5-7): made when
the photo is first sent to the video model, or first used as a reference by another picture
step, never up front when the page is read, and made once.

Before it is cut out, one ask (the helper ask) is shown the photo and the library's pictures on
numbered sheets, and says whether a part of the product is hidden or cut off in the photo,
which part, and which library pictures show it (`helpers`). Code keeps only numbers the library
holds, never the photo itself or one of its versions, at most MOST_HELPERS. The picture model
is given the photo first and those pictures after it, told to keep the product as the photo
shows it and to take only the hidden part's shape from them; the cut-out check is shown the
same pictures and told the same part (jobs.cut_out). The answer is kept on the item, so it is
asked once.

A cut-out that can't be had for good is `Unusable`, and is the same, paying for nothing, each
time it is asked again:
- a part is hidden and no library picture shows it, so nothing is drawn (Julian, Oct 30
  01:15, "Okay, I agree. That's good");
- its check still fails after its redraws, or a safety filter refuses it (the same rule,
  Julian, Oct 30 02:09, #273 point 7).
What then goes is the sender's: cut-and-send leaves the photo out of the clip (`for_clip`),
and another picture step sends it whole (`to_send`). A cut-out an outage or an unusable answer
stopped is said in red and tried again the next time the photo is sent; meanwhile the photo
goes whole."""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import UnreadableImage, call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)

from . import cut_out, library_sheets, notices
from .job_locks import PRODUCT_ONLY_LOCK, one_at_a_time
from .library import LibraryEntry, ProductOnly, Version, add_to_library, library_of
from .library_items import THE_PRODUCT, label_photo, needs_product_only, version_of
from .models import Job, LibraryItem
from .notices import post_notice
from .reference_photos import clean_photo_of

logger = logging.getLogger(__name__)

# The most library pictures a cut-out is given beside its photo (Julian, Oct 30 01:11-01:14:
# "give it a little bit more room"; the picture model takes up to 16).
MOST_HELPERS = 8

# Its examples are made up, never from a test product (CODING_STANDARDS.md).
HELPER_INSTRUCTIONS = """\
You help cut a product out of one photo for a video ad. A picture model will redraw the \
product alone from the photo, and anything of it the photo hides it has to make up, unless \
it is shown another picture of that part. You are shown the photo to cut out first, then \
the ad's picture library on numbered sheets, each picture's number and what it shows written \
under it.

Answer three things.
- covered: whether any part of the product is hidden or cut off in the photo: behind \
something, held in a hand so it can't be seen, inside a stand or a box, or past the photo's \
edge. Not a side that faces away from the camera: only a part that would show from this \
side if nothing were in the way.
- covered_part: that part, in a few words, such as "the lid's top half" or "the handle's \
end"; empty when nothing is hidden.
- helpers: the numbers of the library pictures that show that part of this same product \
clearly, as many as it takes, best first; none when nothing is hidden or no picture shows \
it. Any colour or state of the product will do: only the part's shape is taken from them. \
Never the photo being cut out itself."""

PICTURES_SHOWN = (
    "The photo to cut out is first; the library's pictures follow it, on numbered sheets."
)


class HelperHandoff(Handoff):
    """What the photo to cut out shows and what is to be kept of it, and which picture is
    which."""

    photo_label: str
    keep: str
    pictures: str = PICTURES_SHOWN


# Filled in from the top: the part is named before the pictures showing it are picked. A
# comment, not a docstring: a docstring would be sent to the model in the answer's schema.
class HelperAnswer(BaseModel):
    covered: bool = Field(description="Whether a part of the product is hidden or cut off.")
    covered_part: str = Field(description="That part, in a few words; empty when none is.")
    helpers: list[int] = Field(
        description="The library numbers of the pictures that show that part; empty when none."
    )


@dataclass(frozen=True)
class Unusable:
    """A photo whose product-only version can't be had for good, and why, in plain words
    ("no other photo shows its handle")."""

    why: str


# Why the helper ask or the labelling of a cut-out failed, in plain words: the cut-out is
# tried again the next time the photo is sent.
_WHY_NOT_NOW: dict[type[Exception], str] = {
    UnusableReply: "the AI model's answer couldn't be used",
    OutsideServiceDown: "the AI service didn't answer",
    UnreadableImage: "the photo couldn't be read",
    BlockedBySafetyFilter: "the AI model's safety filter refused the photo",
    AnswerTookTooLong: "the AI model took too long to answer",
}


def for_clip(job: Job, item: LibraryEntry) -> LibraryEntry | Unusable:
    """What goes to a clip for the library original `item`: its product-only version, made
    now when it needs one and has none; `item` itself when it needs none, or, for now, when
    an outage stopped its cut-out (said in red); or Unusable, when the cut-out can't be had
    for good."""
    if not needs_product_only(item):
        return item
    # Only the same photo waits: the ad's other cut-outs go on.
    with one_at_a_time(PRODUCT_ONLY_LOCK, f"{job.pk}:{item.number}"):
        if (made := version_of(job, item)) is not item:
            return made
        row = job.library.get(number=item.number)
        kept: dict[str, Any] = row.cut_out or {}
        try:
            return _cut(job, item, row, kept)
        except tuple(_WHY_NOT_NOW) as error:
            why = next(text for kind, text in _WHY_NOT_NOW.items() if isinstance(error, kind))
            _tried_again(job, item, why, error)
            return item
        except _NotNow as not_now:
            _tried_again(job, item, not_now.why, not_now)
            return item


def to_send(job: Job, item: LibraryEntry) -> LibraryEntry:
    """What another picture step is given for the library original `item` (a made picture's
    or an owner part's reference, a paint's colour reference): as `for_clip`, but the photo
    itself, sent whole, when its cut-out can't be had, said in red once."""
    made = for_clip(job, item)
    if isinstance(made, Unusable):
        _say_once(
            job,
            notices.CUT_OUT_FAILED,
            f"Library item {item.number} ({item.label}) goes whole to a picture made from it, "
            f"with what else it shows: its product-only version can't be had ({made.why}). "
            "The picture made from it may copy those other things.",
        )
        return item
    return made


class _NotNow(Exception):
    """The cut-out's check gave no answer: it is tried again the next time."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


def _cut(
    job: Job, item: LibraryEntry, row: LibraryItem, kept: dict[str, Any]
) -> LibraryEntry | Unusable:
    """`item`'s product-only version, cut out now with the helpers the helper ask names (asked
    now unless `kept` holds its answer), added to the library; or Unusable, kept on `row`."""
    if "covered" not in kept:
        kept = {**_helpers_for(job, item), **kept}
        row.cut_out = kept
        row.save(update_fields=["cut_out"])
    covered_part = str(kept["covered_part"]).strip() or "a part of the product"
    library = {each.number: each for each in library_of(job)}
    helpers = [library[number] for number in kept["helpers"]]
    if kept["covered"] and not helpers:
        return _unusable(row, f"{covered_part} is hidden in the photo and no other photo shows it")
    hidden = covered_part if helpers else None
    keep = _what_to_keep(item)
    try:
        file = cut_out.redraw_with_check(
            job,
            purpose="make_product_only",
            prompt=_prompt(item, keep, hidden),
            photo=item.file,
            helpers=[helper.file for helper in helpers],
            check=lambda picture: cut_out.check_cut_out(
                job,
                picture,
                item.file,
                keep=keep,
                photo_label=item.label,
                named=f"the product cut out of library item {item.number}",
                photo_named=f"library item {item.number}",
                needs_photo=True,
                hidden_part=hidden,
                helpers=[helper.file for helper in helpers],
            ),
            unchecked_passes=False,
        )
    except cut_out.CutOutFailed as failed:
        if failed.unchecked is not None and not failed.unchecked.refused:
            raise _NotNow(failed.unchecked.why) from failed
        logger.warning("Library item %s's product-only version failed: %s", item.number, failed)
        return _unusable(row, f"cutting the product out of it failed: {failed.why()}")
    label = label_photo(
        job,
        file,
        file,
        THE_PRODUCT,
        named=f"the product cut out of library item {item.number}",
        then="It is tried again the next time the photo is sent.",
    )
    if label is None:
        raise _NotNow("the cut-out couldn't be labelled")
    return add_to_library(job, file, label, ProductOnly(of=item.number))


def _helpers_for(job: Job, item: LibraryEntry) -> dict[str, Any]:
    """The helper ask's answer for `item`, its helpers cut to library pictures that aren't
    `item` or one of its versions, each once, at most MOST_HELPERS."""
    sheets = library_sheets.sheets_of(job)
    photo = Image(label="The photo to cut out:", key=item.file)
    answer, _ = library_sheets.past_the_filter(
        job,
        sheets,
        lambda images: call_model(
            job=job,
            purpose="pick_cut_out_helpers",
            instructions=HELPER_INSTRUCTIONS,
            handoff=HelperHandoff(photo_label=item.label, keep=_what_to_keep(item)),
            output=HelperAnswer,
            images=[photo, *images],
            pay_once=True,
        ),
    )
    ours = {
        each.number
        for each in library_of(job)
        if isinstance(each.source, Version) and each.source.of == item.number
    } | {item.number}
    shown = set(sheets.entries)
    helpers: list[int] = []
    for number in answer.helpers if answer.covered else []:
        if number in shown and number not in ours and number not in helpers:
            helpers.append(number)
    return {
        "covered": answer.covered,
        "covered_part": answer.covered_part,
        "helpers": helpers[:MOST_HELPERS],
    }


def _prompt(item: LibraryEntry, keep: str, hidden: str | None) -> str:
    """The picture model's prompt for `item`'s cut-out, keeping `keep`, told when its `hidden`
    part is shown in the pictures after the photo."""
    prompt = f"The photo shows: {item.label} {clean_photo_of(keep)}"
    if hidden is None:
        return prompt
    return (
        f"{prompt} The first picture is the photo. In it, part of the product is hidden or cut "
        f"off: {hidden}. The other pictures show that part. Keep the product exactly as the "
        "first picture shows it: its side, its state and its colour. Take only the shape of "
        "that part from the other pictures, and nothing else from them."
    )


def _what_to_keep(item: LibraryEntry) -> str:
    """What the product-only version of `item` keeps: the product, or, from a line-up, only
    the one product its target names."""
    if item.kind == "line-up":
        return f"the product that is {item.target}"
    return "the product"


def _unusable(row: LibraryItem, why: str) -> Unusable:
    """That `row`'s cut-out can't be had, for `why`. Asked again, it pays for nothing: the
    helper ask's answer is kept on the row, and the cut-outs drawn and checked are their
    records' (jobs.cut_out.redraw_with_check)."""
    logger.warning("Library item %s's product-only version can't be had: %s", row.number, why)
    return Unusable(why)


def _tried_again(job: Job, item: LibraryEntry, why: str, error: Exception) -> None:
    """Say in red, with its warning line, that `item`'s product-only version couldn't be made
    this time, for `why`, and is tried again the next time it is sent."""
    logger.warning("Library item %s's product-only version wasn't made: %s", item.number, error)
    _say_once(
        job,
        notices.CUT_OUT_TRIED_AGAIN,
        f"Cutting the product out of library item {item.number} ({item.label}) didn't work "
        f"this time ({why}), so for now the photo is sent whole, with what else it shows. It "
        "is tried again the next time the photo is sent.",
    )


def _say_once(job: Job, kind: notices.Kind, text: str) -> None:
    """Record `text`, a notice of `kind`, in red, unless the job was told exactly that."""
    job.refresh_from_db(fields=["warnings"])
    if any(warning["text"] == text for warning in job.warnings):
        return
    post_notice(job, kind, text, Message.Level.PROBLEM)
