"""Recolouring a library picture (spec #210 point 8): a code step that makes a version of a
library picture with the product in another colour, the colour of a reference picture's
product, and checks it.

`recolour(job, number, colour, reference=...)`: the picture model is shown item `number` and
the colour reference, item `reference`, and told to make the product in the first the exact
colour of the product in the second, changing nothing else; no colour is named to it. The
picture made is checked by one ask, and drawn again while it fails, at most twice
(jobs.checked_pictures). One that passes enters the library beside the original, as a
Recoloured item in `colour`, made once per colour: asked again, the item is handed back and
nothing is paid. When every try fails, or the picture model refuses twice, the original is
handed back, with a red notice saying so.

Which reference to use, and when to recolour, is cut-and-send's: this step takes both as
given. Run again, as after a worker stopped, it pays for nothing already paid for: each
picture made is reused in turn, and each check answered from its record."""

import logging
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import UnreadableImage, call_model, edit_picture
from gateway.models import ModelCall
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    PictureEditHandoff,
    UnusableReply,
)

from .checked_pictures import made_and_checked
from .job_locks import LIBRARY_LOCK, RECOLOUR_LOCK, one_job_at_a_time
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

# What the check is told. Spec 2 item 17 (Julian Oct 28 21:24): the same picture, only the
# product's colour changed, to the reference's. Not approved yet: "same shape, angle and
# background" is the spec's addition to his words (audit section C).
RECOLOUR_CHECK = """\
You check a picture that an AI picture model recoloured. You are shown three pictures: \
picture 1, the picture to recolour; picture 2, the colour reference, a picture of the same \
product in the colour wanted; and the new picture. The picture model was told to make the \
product in picture 1 the exact colour of the product in picture 2 and to change nothing else.

Work in two steps.
1. Look before you judge. Write down every difference you can see between the new picture \
and picture 1: in the product's shape, parts, print and label, the angle it is seen from, \
the framing, the background and anything else in the picture. Then compare the product's \
colour in the new picture with the product's colour in picture 2.
2. Then judge. Decide "pass" only when the new picture is the same picture as picture 1, \
with the same shape, angle and background, and the only change is the product's colour, \
which is now the colour of the product in picture 2. Decide "fail" for anything else, such \
as something besides the product recoloured, the product's shape, parts or print changed, \
another angle, framing or background, or a colour that is not the reference's.

Picture 2 is shown only for the product's colour: ignore its background, angle and \
anything else in it."""

# Said beside the three pictures.
PICTURES_SHOWN = (
    "Picture 1 is the picture to recolour, picture 2 the colour reference, and the new "
    "picture is last."
)


class RecolourCheckHandoff(Handoff):
    """Which picture is which: the check is shown the three, and nothing else."""

    pictures: str = PICTURES_SHOWN


# The check's answer, filled in from the top: it writes down what it sees before it
# decides. A comment, not a docstring: a docstring would be sent to the model in the
# answer's schema.
class RecolourVerdict(BaseModel):
    changes: str = Field(
        description="Every difference between the new picture and picture 1, other than "
        "the product's colour."
    )
    colour_match: str = Field(
        description="How the product's colour in the new picture compares with its colour "
        "in picture 2."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: what is wrong, in one sentence. Null when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "fail" and not (self.problem or "").strip():
            raise ValueError("A picture that fails must say what is wrong.")
        return self


# Why the check couldn't be made, in plain words, by what went wrong.
_WHY_UNCHECKED: dict[type[Exception], str] = {
    UnusableReply: "the check's answer couldn't be used",
    OutsideServiceDown: "the AI service didn't answer the check",
    UnreadableImage: "a picture couldn't be read for the check",
    BlockedBySafetyFilter: "the check's safety filter refused the pictures",
    AnswerTookTooLong: "the check took too long to answer",
}


@dataclass(frozen=True)
class Recolouring:
    """What a recolour gave: the picture to send (the recoloured item, or the original when
    it fell back) and its record for the sent list."""

    picture: LibraryEntry
    record: Recolour


def recolour(job: Job, number: int, colour: str, *, reference: int) -> Recolouring:
    """Item `number` of `job`'s library with its product made `colour`, the colour of the
    product in item `reference`: the Recoloured item already made in `colour`, else one made
    and checked now, else, when every try failed, item `number` itself, which is said in a
    red notice."""
    with one_job_at_a_time(RECOLOUR_LOCK, job):
        original = _item(job, number)
        if (made := _made_before(job, number, colour)) is not None:
            return _done(made)
        to_send = [original.file, _item(job, reference).file]
        paid = _paid_pictures(job, to_send)

        def make(tries: int) -> str:
            if tries < len(paid):
                return paid[tries]
            return edit_picture(
                job=job, purpose="recolour_picture", prompt=RECOLOUR_PROMPT, pictures=to_send
            )

        checked = made_and_checked(make, lambda file: _check(job, [*to_send, file]))
        if checked.file is None:
            _say_it_fell_back(job, original, colour, checked.problems)
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


def _paid_pictures(job: Job, pictures: list[str]) -> list[str]:
    """Each recoloured picture made from `pictures` before, oldest first."""
    handoff = PictureEditHandoff(prompt=RECOLOUR_PROMPT, pictures=pictures).model_dump()
    calls = job.model_calls.filter(
        purpose="recolour_picture", outcome=ModelCall.Outcome.SUCCEEDED, handoff=handoff
    ).order_by("created_at", "id")
    return [str(call.output["file"]) for call in calls if call.output is not None]


def _check(job: Job, pictures: list[str]) -> str | None:
    """What is wrong with the recoloured picture, last of `pictures`, made from the first
    two; None when it passes. A check that can't be made fails it, saying why: a picture
    nobody looked at is never sent as the right colour."""
    original, reference, made = pictures
    try:
        verdict = call_model(
            job=job,
            purpose="check_recolour",
            instructions=RECOLOUR_CHECK,
            handoff=RecolourCheckHandoff(),
            output=RecolourVerdict,
            images=[
                Image("Picture 1, the picture to recolour:", original),
                Image("Picture 2, the colour reference:", reference),
                Image("The new picture:", made),
            ],
            pay_once=True,
        )
    except tuple(_WHY_UNCHECKED) as error:
        logger.warning("The check of the recoloured picture %s couldn't be made: %s", made, error)
        return next(text for kind, text in _WHY_UNCHECKED.items() if isinstance(error, kind))
    return None if verdict.verdict == "pass" else verdict.problem


def _say_it_fell_back(job: Job, original: LibraryEntry, colour: str, problems: list[str]) -> None:
    """The red notice for a recolour every try failed: what failed, what goes instead, and
    what that means for the ad."""
    found = " ".join(f"Try {tries}: {problem}" for tries, problem in enumerate(problems, 1))
    post_notice(
        job,
        f'Making picture {original.number} of the ad\'s picture library ("{original.label}") '
        f"{colour} failed every time it was tried, so the picture goes to the video model in "
        f"its own colour, {original.colour}: a clip made with it may show the product in two "
        f"colours. {found}",
        Message.Level.PROBLEM,
    )
