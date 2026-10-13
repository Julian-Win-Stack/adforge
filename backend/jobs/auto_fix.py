"""The auto-fix (#155): a B-roll clip the clip judge fails is made again by code, up to
MOST_REMAKES times, and the best of the clips made is kept (change 9, Clip judge
discussion; Julian's yes 2026-10-13 05:03)."""

from typing import Any

# How many times a clip the judge fails is made again.
MOST_REMAKES = 2


def clip_to_keep(failed_answers: list[int]) -> int:
    """Which of the clips made for a scene, in the order they were made, is kept, given how
    many of the judge's answers each failed: the fewest, and the newest of those tied
    (change 9, Clip judge discussion)."""
    fewest = min(failed_answers)
    return max(i for i, failed in enumerate(failed_answers) if failed == fewest)


def failed_answers(ruling: dict[str, Any]) -> int:
    """How many of the judge's answers about a clip failed, as the clip to keep is picked by.
    A clip the judge passed counts as failing none, whatever its answers: it is the auto-fix's
    answer (stand-in call 8). So does one it couldn't check: it goes on as if it passed, and
    the shop owner is told it wasn't checked (stand-in call 7, as Julian chose for #131 at
    05:18). Fewest failed answers decides only among failed clips."""
    if ruling["verdict"] != "fail":
        return 0
    return int(ruling["failed_answers"])


def faults(ruling: dict[str, Any]) -> list[str]:
    """Every problem the judge's failed answers about a clip name, each once, in their
    order."""
    return list(
        dict.fromkeys(problem for failed in ruling["failures"] for problem in failed["problems"])
    )


def judge_saw(ruling: dict[str, Any]) -> list[dict[str, Any]]:
    """Each of the judge's failed answers about a clip whole, each the same answer once: what
    it saw happen in the clip."""
    seen: list[dict[str, Any]] = []
    for failed in ruling["failures"]:
        if failed["answer"] not in seen:
            seen.append(failed["answer"])
    return seen
