"""What the clip judge saw wrong in a B-roll clip, as the fixing agent is handed it when the
judge fails a clip (door 1), and how many tries it may film at most."""

from typing import Any

from .clip_judge import MOTION_QUESTIONS, left_out, still_failing

# The most tries the fixing agent may film of one scene: a guard on money and time only, as
# the agent decides when to give up (Julian, Oct 19: "We cannot make that decision with the
# code").
MOST_TRIES = 6


def failed_answers(ruling: dict[str, Any]) -> int:
    """How many of the judge's answers about a clip failed. A clip the judge passed counts as
    failing none, whatever its answers, and so does one it couldn't check."""
    if ruling["verdict"] != "fail":
        return 0
    return int(ruling["failed_answers"])


def faults(ruling: dict[str, Any]) -> list[str]:
    """Every problem behind the judge's failed checks of a clip, each once: SIDES first, then
    each MOTION question, each check's asks in order. Never one from an answer outvoted on its
    check (Julian's yes, 2026-10-13 19:42)."""
    return list(dict.fromkeys(still_failing(ruling)))


def judge_saw(ruling: dict[str, Any]) -> list[dict[str, Any]]:
    """Each of the judge's answers about a clip that failed one of its failed checks, each the
    same answer once: what it saw happen in the clip. A MOTION answer's question failed on its
    own or outvoted on its check (clip_judge.left_out) is left out of it, so no failed verdict
    the clip wasn't failed for is handed over. What the answer saw happen stays whole."""
    failed = {name for name, verdict in ruling["checks"].items() if verdict == "fail"}
    left = left_out(ruling)
    seen: list[dict[str, Any]] = []
    for failure in ruling["failures"]:
        answer = failure["answer"]
        if failure["check"] == "sides":
            if "sides" not in failed:
                continue
        else:
            said_fail = {q for q in MOTION_QUESTIONS if answer[q]["verdict"] == "fail"}
            counted = {q for q in said_fail if q in failed and (q, failure["ask"]) not in left}
            if not counted:
                continue
            not_counted = said_fail - counted
            answer = {key: said for key, said in answer.items() if key not in not_counted}
        if answer not in seen:
            seen.append(answer)
    return seen
