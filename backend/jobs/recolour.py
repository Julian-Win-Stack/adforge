"""Painting a library picture (spec #210 point 8, #273 point 4): a code step that makes a
version of a library picture with the product in another colour, the colour of a reference
picture's product.

`recolour(job, number, colour, reference=..., scene=...)`: the picture model is shown item
`number` and the colour reference, item `reference`, and told to make the product in the
first the exact colour of the product in the second, changing nothing else; no colour is
named to it, and it draws at the shape of item `number`. Nothing checks the paint (Julian,
Oct 30: "I think we can just trust it"). It enters the library beside the original, as a
Recoloured item in `colour`, made once per colour: asked again, the item is handed back and
nothing is paid. A paint the picture model's safety filter refuses is asked for again once;
refused twice, the original is handed back, with a red notice saying so, naming the scene it
was asked for: once per picture and colour, as the next scene to ask for it gets the same.

Which reference to use, and when to paint, is cut-and-send's: this step takes both as given.
Run again, as after a worker stopped, it pays for nothing already paid for: the picture made,
and each refusal, is taken again in turn. An outage of the picture model is raised, so the
step can be run again later, rather than failing a try."""

import logging
from dataclasses import dataclass

from chat.models import Message
from gateway.gateway import edit_picture
from gateway.types import BlockedBySafetyFilter

from . import notices
from .checked_pictures import made_and_checked, tried_before
from .job_locks import LIBRARY_LOCK, RECOLOUR_LOCK, one_at_a_time, one_job_at_a_time
from .library import LibraryEntry, PictureLabel, Recolour, Recoloured, add_to_library, entry
from .models import Job
from .notices import post_notice

logger = logging.getLogger(__name__)

# What the picture model is told, shown the picture to recolour then the colour reference.
# No colour is named: the reference shows it (spec #210 point 8).
RECOLOUR_PROMPT = (
    "Make the product in picture 1 the exact colour of the product in picture 2. "
    "Change nothing else."
)


@dataclass(frozen=True)
class Recolouring:
    """What a recolour gave: the picture to send (the recoloured item, or the original when
    it fell back) and its record for the sent list."""

    picture: LibraryEntry
    record: Recolour


def recolour(job: Job, number: int, colour: str, *, reference: int, scene: int) -> Recolouring:
    """Item `number` of `job`'s library with its product made `colour`, the colour of the
    product in item `reference`, for scene `scene`: the Recoloured item already made in
    `colour`, else one made now, else, when the picture model refused it twice, item
    `number` itself, which a red notice says the first time."""
    # Only the same picture in the same colour waits: the ad's other recolours go on.
    with one_at_a_time(RECOLOUR_LOCK, f"{job.pk}:{number}:{colour}"):
        original = _item(job, number)
        if (made := _made_before(job, number, colour)) is not None:
            return _done(made)
        to_send = [original.file, _item(job, reference).file]
        tried = tried_before(job, "recolour_picture", RECOLOUR_PROMPT, to_send)

        def make(tries: int) -> str:
            if tries < len(tried):
                before = tried[tries]
                if isinstance(before, BlockedBySafetyFilter):
                    raise before
                return before
            return edit_picture(
                job=job,
                purpose="recolour_picture",
                prompt=RECOLOUR_PROMPT,
                pictures=to_send,
                keep_shape=True,
            )

        # Unchecked: the first paint drawn is kept.
        checked = made_and_checked(make, lambda _: None)
        if checked.file is None:
            _say_it_fell_back(job, original, colour, scene, checked.problems)
            return Recolouring(
                picture=original,
                record=Recolour(of=number, colour=colour, outcome="fell_back", made=None),
            )
        label = PictureLabel.model_validate(
            {**original.model_dump(include=set(PictureLabel.model_fields)), "colour": colour}
        )
        with one_job_at_a_time(LIBRARY_LOCK, job):
            made = add_to_library(job, checked.file, label, Recoloured(of=number, colour=colour))
        return _done(made)


def _item(job: Job, number: int) -> LibraryEntry:
    return entry(job.library.get(number=number))


def _made_before(job: Job, number: int, colour: str) -> LibraryEntry | None:
    """The item of `job`'s library that is item `number` recoloured `colour`, if any."""
    source = Recoloured(of=number, colour=colour).model_dump()
    row = job.library.filter(source=source).order_by("number").first()
    return None if row is None else entry(row)


def _done(made: LibraryEntry) -> Recolouring:
    assert isinstance(made.source, Recoloured), "a recolour made a Recoloured item"
    return Recolouring(
        picture=made,
        record=Recolour(
            of=made.source.of, colour=made.source.colour, outcome="done", made=made.number
        ),
    )


def _say_it_fell_back(
    job: Job, original: LibraryEntry, colour: str, scene: int, problems: list[str]
) -> None:
    """The red notice for a paint the picture model refused twice, the first time: what failed,
    what goes instead, and what that means for the ad. A later scene asking for the same
    picture in the same colour gets the same, and isn't told again."""
    opening = _fell_back_opening(original, colour)
    if any(warning["text"].startswith(opening) for warning in job.warnings):
        return
    found = " ".join(f"Try {tries}: {problem}" for tries, problem in enumerate(problems, 1))
    post_notice(
        job,
        notices.RECOLOUR_FAILED,
        f"{opening}, for scene {scene}: the picture model refused to paint it, so the picture "
        f"goes to the video model in its own colour, {original.colour}, for this scene and any "
        f"other that sends it in {colour}. A clip made with it may show the product in two "
        f"colours. {found}",
        Message.Level.PROBLEM,
        scene=scene,
    )


def _fell_back_opening(original: LibraryEntry, colour: str) -> str:
    """How the notice of a recolour that fell back starts: the picture and the colour."""
    return (
        f"Picture {original.number} of the ad's picture library (\"{original.label}\") couldn't "
        f"be made {colour}"
    )
