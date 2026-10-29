"""A picture the producer makes (spec #247 point 5, Julian Q11): drawn by the picture model
from the producer's own prompt, with library items as references, checked once the way a
cut-out is (jobs.cut_out.check_made_picture), and stored as a new library item with the
label the producer gives, so a drawn or combined picture is a reference like any other that
the producer may name for a scene; it is sent only where it is named (never as cut-and-send's
tier 4), and one made from an owner part's picture only to the scenes holding that part.
Combining several sides into one picture is one use of it. Its other fixed fields (kind,
side, alone, colour) come from the labelling call, told what the producer says it shows.

A picture that fails its check, or whose check or label can't be had, is not stored: the
reason goes back to the producer, which may ask for another. The shop owner is never asked.
A refusal by the picture model's safety filter, or for money where nothing else could draw
it, is raised for the producer's tool to answer with (agents.loop)."""

from gateway.gateway import draw_picture, edit_picture
from gateway.models import ModelCall

from .cut_out import Unchecked, check_made_picture
from .job_locks import LIBRARY_LOCK, one_job_at_a_time
from .library import LibraryEntry, Made, add_to_library, entry
from .library_items import label_photo, to_send
from .models import Job

PURPOSE = "make_picture"


class PictureNotMade(Exception):
    """The picture made can't go in the library: why, in plain words."""


def make_picture(
    job: Job,
    *,
    prompt: str,
    references: list[LibraryEntry],
    label: str,
    paid_under: int | None = None,
) -> LibraryEntry:
    """The library item made from `prompt` with `references`, labelled `label`, once its
    check passes. Raises PictureNotMade saying why when it doesn't, or its check or label
    can't be had.

    Made again under the same producer call (`paid_under`, its id), as after a worker
    stopped, it pays for nothing twice: the picture already drawn is reused, its check and
    label answered from their records, and the item already stored handed back."""
    # Each reference as a sender sends it: its product-only version when it has one.
    sent = [to_send(job, reference) for reference in references]
    pictures = [reference.file for reference in sent]
    file = _paid_for(job, prompt, pictures, paid_under) or _drawn(job, prompt, pictures)
    said = check_made_picture(
        job,
        file,
        [(reference.file, reference.label) for reference in sent],
        asked=prompt,
        label=label,
    )
    if isinstance(said, Unchecked):
        raise PictureNotMade(f"its check couldn't be made ({said.why})")
    if said is not None:
        raise PictureNotMade(f"it failed its check: {said}")
    fields = label_photo(
        job,
        file,
        file,
        label,
        named="the picture the producer made",
        then="It can be made again.",
    )
    if fields is None:
        raise PictureNotMade("saying what it shows for the library failed")
    source = Made(references=[reference.number for reference in references])
    with one_job_at_a_time(LIBRARY_LOCK, job):
        stored = job.library.filter(source=source.model_dump(), file=file).first()
        if stored is not None:
            return entry(stored)
        return add_to_library(job, file, fields.model_copy(update={"label": label}), source)


def _paid_for(job: Job, prompt: str, pictures: list[str], paid_under: int | None) -> str | None:
    """The picture already drawn for `prompt` with `pictures` under the producer call
    `paid_under`, if one was."""
    if paid_under is None:
        return None
    call = (
        job.model_calls.filter(
            purpose=PURPOSE,
            outcome=ModelCall.Outcome.SUCCEEDED,
            tool_call_id=paid_under,
            handoff={"prompt": prompt, "pictures": pictures} if pictures else {"prompt": prompt},
        )
        .order_by("id")
        .last()
    )
    return None if call is None or call.output is None else str(call.output["file"])


def _drawn(job: Job, prompt: str, pictures: list[str]) -> str:
    """A new picture drawn from `prompt`: made from `pictures` when it has any, else drawn
    from the words alone."""
    if pictures:
        return edit_picture(job=job, purpose=PURPOSE, prompt=prompt, pictures=pictures)
    return draw_picture(job=job, purpose=PURPOSE, prompt=prompt)
