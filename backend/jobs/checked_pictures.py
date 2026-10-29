"""A picture made by the picture model and checked, drawn again while its check fails: the
redraw-with-check a library step builds on (spec #210 points 6 and 8). The step says how a
picture is made and how it is checked; this says how many times, and when to give up.

A refusal by the picture model's safety filter is taken as a failed check: the picture is
asked for again once, and a second refusal ends the tries (spec 2 item 18). Whatever the
step falls back to, and how it says so, is the step's."""

from collections.abc import Callable
from dataclasses import dataclass

from gateway.types import BlockedBySafetyFilter

# A picture whose check fails is drawn again at most this many times (spec #210 point 8).
MOST_REDRAWS = 2
# The picture model's safety filter may refuse at most this many times before the step
# gives up: the first refusal is asked again once.
MOST_REFUSALS = 2


@dataclass(frozen=True)
class Checked:
    """What came of the tries: the picture that passed its check, or None, with what each
    failing check found and each refusal, oldest first."""

    file: str | None
    problems: list[str]


def made_and_checked(make: Callable[[int], str], check: Callable[[str], str | None]) -> Checked:
    """A picture `make` makes (given the try's index, from 0, so a step run again can hand
    back the picture paid for at that try) that `check` passes: `check` gives what is wrong
    with it, or None. Made again while its check fails, at most MOST_REDRAWS times, and
    given up on at the MOST_REFUSALS-th refusal."""
    problems: list[str] = []
    refusals = 0
    for tries in range(MOST_REDRAWS + 1):
        try:
            file = make(tries)
        except BlockedBySafetyFilter as error:
            refusals += 1
            problems.append(f"the picture model's safety filter refused it ({error})")
            if refusals >= MOST_REFUSALS:
                break
            continue
        problem = check(file)
        if problem is None:
            return Checked(file=file, problems=problems)
        problems.append(problem)
    return Checked(file=None, problems=problems)
