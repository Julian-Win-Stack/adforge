"""The product's colour list (#273 point 2, Julian Oct 30 00:43: "i agree with this"): the
colours the shop sells the product in, written once per ad by the photo picker, in the page's
own names when it has them. Every call that names a colour picks one of them, or `NONE` where
the product's own body isn't seen, by its answer's schema: the labelling call, the planner's
ad colour and the watcher. Code compares colours as entries, exactly: two colours are the same
only when they are the same entry.

A job the picker gave no list (its photos fell back to the page's declared ones, or it was
read before the list) has an empty one: the calls then name colours in their own words, each
kept as an entry (`entry`), compared exactly as today."""

from typing import Any, Literal

from .models import Job

# A picture or video where the product's own body isn't seen: what it holds, a result on
# skin, a chart, a face. Never painted, and never a clip's colour.
NONE = "none"


def entry(colour: str) -> str:
    """`colour` as an entry of the list: lower case, its words joined by hyphens ("Sage
    green" is "sage-green")."""
    return "-".join(colour.lower().split())


def entries(colours: list[str]) -> list[str]:
    """`colours` as the list keeps them: each an entry once, in order, never `NONE`."""
    kept: list[str] = []
    for colour in map(entry, colours):
        if colour and colour != NONE and colour not in kept:
            kept.append(colour)
    return kept


def answer(job: Job, *, none: bool, free: Any = str) -> Any:
    """The type a call's colour answer for `job` is held to: one entry of its list, or
    `NONE` too when `none`; `free`, the call's own, when the list is empty."""
    listed = list(job.colours)
    if not listed:
        return free
    return Literal[tuple(listed + ([NONE] if none else []))]  # type: ignore[valid-type]
