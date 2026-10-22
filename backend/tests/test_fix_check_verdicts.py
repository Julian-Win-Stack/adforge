"""How a fix check's answers settle its verdict, and which model checks a picture fix. Plain
checks kept apart from tests/test_fix_check.py so they run without a database."""

from typing import Any

import pytest

from gateway import catalog
from jobs import fix_check

from .test_end_to_end import TALKING, ad_made_through_the_chat  # noqa: F401 (a fixture)
from .test_fix_check import FIXED, unfixed
from .test_fix_loop import (
    shown,  # noqa: F401 (a fixture)
)


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


def test_a_not_fixed_answer_must_say_what_is_still_wrong() -> None:
    with pytest.raises(ValueError, match="must say what is still wrong"):
        fix_check.FixVerdict(old_shows="a", new_shows="b", verdict="not fixed", still_wrong=" ")


def test_a_picture_fix_is_checked_by_the_picture_judges_model() -> None:
    assert catalog.MODEL_FOR_PURPOSE["check_fix"] == "gpt-6.1-sol"
    assert "check_fix" not in catalog.SLOW_ANSWER_SECONDS
