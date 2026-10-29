"""Cutting one thing out of a photo (spec #210 point 6): the picture model redraws the photo
with only that thing, exactly as it looks, on a plain background, and a check shown the
cut-out beside the photo passes it only when the thing is alone and unchanged. A cut-out that
fails its check is drawn again by the library's redraw-with-check (jobs.checked_pictures).

Two kinds of cut-out are made this way, by the same `redraw_with_check`:
- the clean picture of one of the shop owner's things, drawn from their photo for a note
  (jobs.work.clean_picture), and
- the product-only version of a library photo that shows the product with other things
  (jobs.library_items.add_product_only_versions).

Each caller decides what a cut-out that still fails means: the owner's thing asks for another
photo; a product-only version leaves the original photo to be sent whole, said in red."""

import logging
from collections.abc import Callable
from typing import Literal, NamedTuple, Self

from pydantic import BaseModel, Field, model_validator

from adforge.retry import OutsideServiceDown
from gateway.gateway import UnreadableImage, call_model, edit_picture, past_the_safety_filter
from gateway.models import ModelCall
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)

from .checked_pictures import MOST_REFUSALS, made_and_checked
from .models import Job
from .notices import post_left_out_by_the_filter

logger = logging.getLogger(__name__)

# Its examples are made up, never from a test product (CODING_STANDARDS.md).
CUT_OUT_CHECK = """\
You check a picture cut out of a photo for a video ad. A picture model was asked to keep \
only one thing from the photo, exactly as it looks, on a plain background, and to leave out \
everything else. You are told which thing was to be kept, and shown the cut-out first and \
the photo it was cut from second.

Work in two steps.
1. Look before you judge. Write what the photo shows of the thing to keep: its shape, its \
colours, and any print, label or marks on it, and which side of it faces the camera. Then \
write everything the cut-out shows: the thing as it is drawn, and anything else in it, such \
as another object, a hand, a stand, a surface or a scene behind it.
2. Then judge. Decide "pass" only when both are true: the thing is the only object left, on \
a plain background; and the thing itself is unchanged: the same shape, the same colours and \
the same print or label as in the photo, seen from the same side. Decide "fail" for anything \
else, such as another object or part of one left in, a scene behind the thing, a part of the \
thing missing or added, a changed shape or colour, or print that is changed, missing or made \
up. Small differences in light, sharpness or size are not a fail.

When the photo isn't shown, judge only whether the thing is alone, and whether it could be \
the thing named."""

PICTURES_SHOWN = "The cut-out is first; the photo it was cut from, when shown, is second."


class CutOutCheckHandoff(Handoff):
    """Which thing the cut-out was to keep, what the photo shows as its label says (None when
    it has none), and which picture is which."""

    keep: str
    photo_label: str | None = None
    pictures: str = PICTURES_SHOWN


# Filled in from the top, so the check looks at both pictures before it judges
# (vision-judges.md, rule 1). A comment, not a docstring: a docstring would be sent to the
# check in the answer's schema.
class CutOutVerdict(BaseModel):
    photo_shows: str = Field(
        description="The thing to keep as the photo shows it: shape, colours, print, side."
    )
    cut_out_shows: str = Field(
        description="Everything the cut-out shows: the thing as drawn, and anything else."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: what is wrong, in one sentence. Null when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "fail" and not (self.problem or "").strip():
            raise ValueError("A cut-out that fails must say what is wrong.")
        return self


class Unchecked(NamedTuple):
    """The check gave no answer on a cut-out: `why`, in plain words. `refused` when the
    check's safety filter refused to look at the cut-out itself."""

    why: str
    refused: bool = False


# What a check says of one cut-out: None when it passes, what is wrong when it fails, or
# Unchecked when no answer could be had.
Said = str | Unchecked | None
Check = Callable[[str], Said]


def check_cut_out(
    job: Job,
    picture: str,
    photo: str,
    *,
    keep: str,
    named: str,
    photo_named: str,
    needs_photo: bool,
    photo_label: str | None = None,
) -> Said:
    """What the cut-out check says of the cut-out at `picture`, cut out of the photo at
    `photo` (labelled `photo_label`, if it is) to keep only `keep`: asked once (spec #210
    point 6, no vote), and answered from its record when asked the same again. When the
    check's safety filter refuses the photo, the cut-out is judged without it, and the chat
    told in red naming the cut-out as `named` and the photo as `photo_named`; unless it
    `needs_photo`, when it counts as unchecked: without the photo, nothing can say the thing
    is unchanged."""
    shown = Image(label="The photo it was cut from:", key=photo)
    try:
        verdict, left = past_the_safety_filter(
            lambda images: call_model(
                job=job,
                purpose="check_cut_out",
                instructions=CUT_OUT_CHECK,
                handoff=CutOutCheckHandoff(keep=keep, photo_label=photo_label),
                output=CutOutVerdict,
                images=images,
                pay_once=True,
            ),
            [Image(label="The cut-out:", key=picture), shown],
            may_leave_out=lambda image: image == shown and not needs_photo,
        )
    except BlockedBySafetyFilter as error:
        logger.warning("The cut-out check of %s was refused: %s", keep, error)
        return Unchecked("the AI model's safety filter refused to look at it", refused=True)
    except (UnusableReply, AnswerTookTooLong, UnreadableImage) as error:
        logger.warning("The cut-out check of %s gave no answer: %s", keep, error)
        return Unchecked("the AI model checking it gave no usable answer")
    except OutsideServiceDown as error:
        logger.warning("The cut-out check of %s gave no answer: %s", keep, error)
        return Unchecked("the AI service checking it didn't answer")
    if left:
        post_left_out_by_the_filter(job, [photo_named], f"{named} was checked")
    if verdict.verdict == "pass":
        return None
    assert verdict.problem is not None, "a failing answer says why"
    return verdict.problem


class CutOutFailed(Exception):
    """No cut-out passed its check: each check's problem, in the order drawn; `refused`,
    the picture model's refusal, when it refused to draw past its retry; `unchecked`, why
    the last cut-out's check gave no answer, when it didn't."""

    def __init__(
        self,
        problems: list[str],
        *,
        refused: str | None = None,
        unchecked: Unchecked | None = None,
    ) -> None:
        self.problems = problems
        self.refused = refused
        self.unchecked = unchecked
        super().__init__(self.why())

    def why(self) -> str:
        """Why no cut-out could be used, in plain words."""
        if self.refused is not None:
            return f"the picture model refused to draw it {MOST_REFUSALS} times ({self.refused})"
        if self.unchecked is not None:
            return f"its check couldn't be made ({self.unchecked.why})"
        found = " ".join(self.problems)
        return f"all {len(self.problems)} tries failed ({found})"


class _NoAnswer(Exception):
    """A cut-out's check gave no answer, and an unchecked cut-out isn't kept: the tries end."""

    def __init__(self, said: Unchecked) -> None:
        super().__init__(said.why)
        self.said = said


def redraw_with_check(
    job: Job,
    *,
    purpose: str,
    prompt: str,
    photo: str,
    check: Check,
    unchecked_passes: bool,
) -> str:
    """The first cut-out of the photo at `photo` drawn as `prompt` says (for `purpose`) that
    `check` passes, by the library's redraw-with-check (jobs.checked_pictures): drawn again
    when it fails, at most its MOST_REDRAWS times, and a picture the picture model refuses asked
    for again once. A cut-out whose check gives no answer is kept when `unchecked_passes`,
    else no cut-out is had. Gives its key in the file store; raises CutOutFailed when none
    is had.

    Run again, as after a worker stopped, it pays for nothing already paid for: each
    cut-out drawn is reused in turn, and each check answered from its record."""
    paid = [
        str(call.output["file"])
        for call in job.model_calls.filter(
            purpose=purpose,
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff={"prompt": prompt, "pictures": [photo]},
        ).order_by("id")
        if call.output is not None
    ]
    drawn: list[str] = []
    refusals: list[str] = []

    def make(_: int) -> str:
        # Counted by the cut-outs drawn, not the tries: a refused try draws nothing.
        try:
            file = (
                paid[len(drawn)]
                if len(drawn) < len(paid)
                else edit_picture(job=job, purpose=purpose, prompt=prompt, pictures=[photo])
            )
        except BlockedBySafetyFilter as error:
            logger.warning("The picture model refused a cut-out for %s: %s", purpose, error)
            refusals.append(str(error))
            raise
        drawn.append(file)
        return file

    def checking(file: str) -> str | None:
        said = check(file)
        if isinstance(said, Unchecked):
            if unchecked_passes:
                return None
            raise _NoAnswer(said)
        return said

    try:
        checked = made_and_checked(make, checking)
    except _NoAnswer as no_answer:
        raise CutOutFailed([], unchecked=no_answer.said) from no_answer
    if checked.file is not None:
        return checked.file
    if len(refusals) >= MOST_REFUSALS:
        raise CutOutFailed(checked.problems, refused=refusals[-1])
    raise CutOutFailed(checked.problems)
