"""The check of a fix (route B step 6, #131): after a scene is made again for a fix the shop
owner said yes to, a judge is asked the complaint back about the new version, shown beside
the old one, before anything more is made or shown as fixed. A new starting picture is
judged here. A new clip is judged by the clip judge itself (#109), asked one more question
beside the old clip (jobs.clip_judge, Julian 2026-10-14), and its answers are settled here.
A new line is never judged: its words are the proof (Julian 2026-10-11 21:51).

Agreed by Julian (2026-10-13 05:18, "yes"): the judge's question below, showing the old
version beside the new, and stopping before a failed picture's clip is paid for."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from gateway.types import Handoff

from .picture_check import ASKS, FAILS_TO_FAIL

# A picture fix is asked 3 times, failing on 2, as the picture judge is
# (reference/vision-judges.md, rule 6). A clip fix is asked as often as the clip judge asks.
FIX_CHECK = """\
You check whether a change a shop owner asked for was made to a picture in their video ad. \
You are shown the old picture first and the new picture second, and told the change. Judge \
only that change: nothing else in the picture matters, and other differences between the two \
pictures are not a fail.

Work in two steps.
1. Look before you judge. Write what the old picture shows about the thing the change is \
about, then what the new picture shows about it. Describe what is actually drawn, not what \
the change asks for.
2. Then decide: is the change clearly there in the new picture? Answer "fixed" only if it is. \
Answer "not fixed" if what the shop owner complained about is still there, or the change is \
only partly made, and say in one sentence what is still wrong."""

PICTURES_SHOWN = "The old picture is first; the new picture is second."

# A fix's new starting picture this check fails is drawn again by itself, told why, at most
# this many times before the shop owner is asked (Julian 2026-10-17 17:04, "two retry would
# make sense"). A fix's clip is never drawn again for it: the shop owner is asked at once
# (Julian 2026-10-14).
MOST_REDRAWS = 2

# What a talking scene's picture writer is told, after the change, when it draws a fix's
# picture again; a B-roll scene's is told by picture_check.REDO_RULES, shown the picture.
REDRAW_NOTE = "The last picture drawn for this change failed its check, which found: {reason}"


# The judge's answer, filled in from the top: it looks at both pictures before it decides
# (vision-judges.md, rule 1). A comment, not a docstring: a docstring would be sent to the
# judge in the answer's schema.
class FixVerdict(BaseModel):
    old_shows: str = Field(description="What the old picture shows about the change.")
    new_shows: str = Field(description="What the new picture shows about the change.")
    verdict: Literal["fixed", "not fixed"]
    still_wrong: str | None = Field(
        description="When it isn't fixed: what is still wrong, in one sentence. Null when it is."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "not fixed" and not (self.still_wrong or "").strip():
            raise ValueError("A change that isn't made must say what is still wrong.")
        return self


# For a fix that takes things from the shop owner's photos (Julian 2026-10-15 19:34, the one
# change to the frozen checks): the judge is also shown each photo, named, and looks at it
# first. Every other fix is checked as before.
PHOTO_FIX_QUESTION = """\
The shop owner also sent photos of their own things for this change, named in \
"owner_photos" in the order shown, after the new picture. Each named thing in the new \
picture should look like it does in its photo, whatever the old picture or the product \
photos show. Before anything else, write what the shop owner's photos show about each named \
thing. A thing that doesn't look like its photo in the new picture is not fixed."""


# Only for a fix with the shop owner's photo of a thing that only shows during the clip: the
# clip's judge looks for it (design thread 23:54).
DURING_THE_ACTION = """\
The things named in "during_the_action" only show once the clip's action starts, never in \
a still picture. Don't expect them in the new picture, and don't count the change as not \
made because they aren't there: they are checked in the clip."""


# The judge's answer when it is shown the shop owner's photos: what they show comes first,
# then FixVerdict's fields, in its order.
class PhotoFixVerdict(BaseModel):
    owner_photo_shows: str = Field(
        description="What the shop owner's photos show about each named thing."
    )
    old_shows: str = Field(description="What the old picture shows about the change.")
    new_shows: str = Field(description="What the new picture shows about the change.")
    verdict: Literal["fixed", "not fixed"]
    still_wrong: str | None = Field(
        description="When it isn't fixed: what is still wrong, in one sentence. Null when it is."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "not fixed" and not (self.still_wrong or "").strip():
            raise ValueError("A change that isn't made must say what is still wrong.")
        return self


class FixCheckHandoff(Handoff):
    """The fix the shop owner said yes to, word for word, and which of the judge's asks
    about one new picture this is, from 1: the number keeps each ask's answer apart, so one
    asked again after the worker stopped isn't paid for twice."""

    fix: str
    ask: int
    pictures: str = PICTURES_SHOWN


class DuringTheActionHandoff(FixCheckHandoff):
    """And the things of the shop owner's photos that only show during the clip."""

    during_the_action: list[str]


class PhotoFixCheckHandoff(DuringTheActionHandoff):
    """And the things the shop owner's photos are taken for, in the order shown."""

    owner_photos: list[str]
    pictures: str = (
        "The old picture is first; the new picture is second; then the shop owner's photos, "
        "in the order of owner_photos."
    )


Verdict = Literal["passed", "failed", "unchecked"]


@dataclass(frozen=True)
class Settled:
    """What the judge's answers settled: passed or failed, or unchecked when too few of its
    asks were answered to agree; why it failed; and each answer had, as kept."""

    verdict: Verdict
    reason: str
    answers: list[dict[str, Any]]


def settle_all(answers: Sequence[dict[str, Any] | None]) -> Settled:
    """`settle` over answers all asked at once, in ask order, keeping every answer had: each
    was paid for. Most of them must agree: the clip judge asks once (clip_judge.ASKS), so its
    one answer settles it; a ruling made while it asked 3 times settles on 2, as it did."""
    settled = settle(
        lambda number: answers[number - 1], asks=len(answers), needed=len(answers) // 2 + 1
    )
    return Settled(
        settled.verdict, settled.reason, [answer for answer in answers if answer is not None]
    )


def settle(
    ask: Callable[[int], dict[str, Any] | None],
    *,
    asks: int = ASKS,
    needed: int = FAILS_TO_FAIL,
) -> Settled:
    """Ask the judge with `ask`, from ask 1, until `needed` of its answers agree, at most
    `asks` times. An ask gives the judge's answer, or None when none could be had: the safety
    filter blocked it, or it couldn't be read. Too few answers that agree leave it
    unchecked: a fix is neither passed nor failed on a guess. A fail's reason is its first
    failing answer's."""
    answers: list[dict[str, Any]] = []
    for number in range(1, asks + 1):
        answer = ask(number)
        if answer is not None:
            answers.append(answer)
        fails = [answer for answer in answers if answer["verdict"] == "not fixed"]
        if len(fails) >= needed:
            return Settled("failed", fails[0]["still_wrong"], answers)
        if len(answers) - len(fails) >= needed:
            return Settled("passed", "", answers)
    return Settled("unchecked", "", answers)
