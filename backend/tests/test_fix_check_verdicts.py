"""How a fix check's answers settle its verdict. Plain checks, run without a database."""

from typing import Any

import pytest

from jobs import fix_check

FIXED = {"verdict": "fixed", "still_wrong": None}


def unfixed(why: str) -> dict[str, Any]:
    return {"verdict": "not fixed", "still_wrong": why}


@pytest.mark.parametrize(
    ("answers", "verdict", "reason"),
    [
        ([FIXED, FIXED], "passed", ""),
        ([unfixed("a"), FIXED, FIXED], "passed", ""),
        ([unfixed("a"), unfixed("b")], "failed", "a"),
        ([FIXED, unfixed("a"), unfixed("b")], "failed", "a"),
        ([None, unfixed("a"), unfixed("b")], "failed", "a"),
        ([None, None, None], "unchecked", ""),
        # Two answers that agree settle it; fewer, and it wasn't checked.
        ([None, None, unfixed("a")], "unchecked", ""),
        ([None, FIXED, None], "unchecked", ""),
        ([FIXED, None, unfixed("a")], "unchecked", ""),
    ],
)
def test_the_verdict_is_settled_by_two_answers_of_three(
    answers: list[dict[str, Any] | None], verdict: str, reason: str
) -> None:
    given = iter(answers)
    settled = fix_check.settle(lambda _ask: next(given))

    assert (settled.verdict, settled.reason) == (verdict, reason)
    assert settled.answers == [answer for answer in answers if answer is not None]


@pytest.mark.parametrize(
    ("answers", "verdict", "reason"),
    [
        ([FIXED], "passed", ""),
        ([unfixed("a")], "failed", "a"),
        ([None], "unchecked", ""),
    ],
)
def test_the_clip_judges_one_answer_settles_its_fix_check(
    answers: list[dict[str, Any] | None], verdict: str, reason: str
) -> None:
    """The clip judge asks once (Julian 2026-10-22, "drop the vote"): its one answer about the
    owner's change is the fix check's verdict, and none leaves it unchecked."""
    settled = fix_check.settle_all(answers)

    assert (settled.verdict, settled.reason) == (verdict, reason)
    assert settled.answers == [answer for answer in answers if answer is not None]
