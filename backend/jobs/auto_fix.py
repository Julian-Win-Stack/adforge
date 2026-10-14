"""The auto-fix (#155): a B-roll clip the clip judge fails is made again by code, up to
MOST_REMAKES times, and the best of the clips made is kept (change 9, Clip judge
discussion; Julian's yes 2026-10-13 05:03)."""

from typing import Any

from .clip_judge import MOTION_QUESTIONS, still_failing

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
    """Every problem behind the judge's failed checks of a clip, each once: SIDES first, then
    each MOTION question, each check's asks in order. Never one from an answer outvoted on its
    check, or let go (Julian's yes, 2026-10-13 19:42)."""
    return list(dict.fromkeys(still_failing(ruling)))


def judge_saw(ruling: dict[str, Any]) -> list[dict[str, Any]]:
    """Each of the judge's answers about a clip that failed one of its failed checks, each the
    same answer once: what it saw happen in the clip. A MOTION answer's question failed on its
    own, outvoted on its check or let go, is left out of it, so no failed verdict the clip
    wasn't failed for is handed over. What the answer saw happen stays whole."""
    failed = {name for name, verdict in ruling["checks"].items() if verdict == "fail"}
    let_go = {(each["check"], each["ask"]) for each in ruling.get("let_go", [])}
    seen: list[dict[str, Any]] = []
    for failure in ruling["failures"]:
        answer = failure["answer"]
        if failure["check"] == "sides":
            if "sides" not in failed:
                continue
        else:
            said_fail = {q for q in MOTION_QUESTIONS if answer[q]["verdict"] == "fail"}
            counted = {q for q in said_fail if q in failed and (q, failure["ask"]) not in let_go}
            if not counted:
                continue
            not_counted = said_fail - counted
            answer = {key: said for key, said in answer.items() if key not in not_counted}
        if answer not in seen:
            seen.append(answer)
    return seen
