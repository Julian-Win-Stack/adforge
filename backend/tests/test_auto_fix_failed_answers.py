"""How many of the clip judge's answers about a clip count as failed."""

from typing import Any

import pytest

from jobs.auto_fix import failed_answers


@pytest.mark.parametrize(
    ("ruling", "counted"),
    [
        ({"verdict": "fail", "failed_answers": 2}, 2),
        # A pass can have failed answers, one per question on different asks: it still counts
        # as none, as stand-in call 8 chose.
        ({"verdict": "pass", "failed_answers": 3}, 0),
        # As can a clip the judge couldn't check (stand-in call 7).
        ({"verdict": "not_checked", "failed_answers": 1}, 0),
    ],
)
def test_only_a_failed_clips_failed_answers_count(ruling: dict[str, Any], counted: int) -> None:
    assert failed_answers(ruling) == counted
