"""The check of a fix (route B step 6, #131): after a scene is made again for a fix the shop
owner said yes to, a judge is asked the complaint back about the new version, shown beside
the old one, before anything more is made or shown as fixed. A new starting picture is
judged here, and a new clip, after the clip judge (#109) and its remakes (#155) have kept it.
A new line is never judged: its words are the proof (Julian 2026-10-11 21:51).

Agreed by Julian (2026-10-13 05:18, "yes"): the judge's question below, showing the old
version beside the new, and stopping before a failed picture's clip is paid for."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from gateway.types import Handoff

from .picture_check import ASKS, FAILS_TO_FAIL

# Asked 3 times, failing on 2, as the picture judge is (reference/vision-judges.md, rule 6).
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


class FixCheckHandoff(Handoff):
    """The fix the shop owner said yes to, word for word, and which of the judge's asks
    about one new picture this is, from 1: the number keeps each ask's answer apart, so one
    asked again after the worker stopped isn't paid for twice."""

    fix: str
    ask: int
    pictures: str = PICTURES_SHOWN


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
    was paid for."""
    settled = settle(lambda number: answers[number - 1])
    return Settled(
        settled.verdict, settled.reason, [answer for answer in answers if answer is not None]
    )


def settle(ask: Callable[[int], dict[str, Any] | None]) -> Settled:
    """Ask the judge with `ask`, from ask 1, until FAILS_TO_FAIL of its answers agree, at most
    ASKS times. An ask gives the judge's answer, or None when none could be had: the safety
    filter blocked it, or it couldn't be read. Too few answers that agree leave it
    unchecked: a fix is neither passed nor failed on a guess. A fail's reason is its first
    failing answer's."""
    answers: list[dict[str, Any]] = []
    for number in range(1, ASKS + 1):
        answer = ask(number)
        if answer is not None:
            answers.append(answer)
        fails = [answer for answer in answers if answer["verdict"] == "not fixed"]
        if len(fails) >= FAILS_TO_FAIL:
            return Settled("failed", fails[0]["still_wrong"], answers)
        if len(answers) - len(fails) >= FAILS_TO_FAIL:
            return Settled("passed", "", answers)
    return Settled("unchecked", "", answers)


# The picture question in clip words (decision thread for Julian, 2026-10-13 05:47, call 4).
# Shown frames of the old clip and the new one, 5 a second, each labelled with its time.
FIX_CHECK_CLIP = """\
You check whether a change a shop owner asked for was made to a clip in their video ad. You \
are shown frames of the old clip first, then frames of the new clip, each labelled with its \
time, and told the change. Judge only that change: nothing else in the clip matters, and \
other differences between the two clips are not a fail.

Work in two steps.
1. Look before you judge. Write what the old clip does about the thing the change is about, \
then what the new clip does about it. Describe what actually happens in the frames, not what \
the change asks for.
2. Then decide: is the change clearly there in the new clip? Answer "fixed" only if it is. \
Answer "not fixed" if what the shop owner complained about is still there, or the change is \
only partly made, and say in one sentence what is still wrong."""

CLIPS_SHOWN = "The old clip's frames are first; the new clip's frames follow."

# Each clip is shown in at most this many pictures, so both fit in one ask under the
# 50-picture limit (reference/rate-limits.md).
MOST_PICTURES_PER_CLIP = 24


class FixClipVerdict(FixVerdict):
    old_shows: str = Field(description="What the old clip does about the change.")
    new_shows: str = Field(description="What the new clip does about the change.")


class FixClipCheckHandoff(Handoff):
    """As FixCheckHandoff, for a fix's new clip."""

    fix: str
    ask: int
    clips: str = CLIPS_SHOWN


def thinned[Item](pictures: list[Item], most: int = MOST_PICTURES_PER_CLIP) -> list[Item]:
    """At most `most` of `pictures`, spread evenly from the first to the last: an owner's
    change often shows at the end, so the end is never cut off."""
    if len(pictures) <= most:
        return pictures
    if most == 1:
        return pictures[-1:]
    step = (len(pictures) - 1) / (most - 1)
    return [pictures[round(n * step)] for n in range(most)]
