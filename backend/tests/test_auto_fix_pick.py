"""The clip an auto-fix keeps (#155): of the clips the judge saw, the one with the fewest
failed answers, the newest of those tied."""

from typing import Any

import pytest

from jobs.auto_fix import clip_to_keep, failed_answers


@pytest.mark.parametrize(
    ("failed_answers", "kept"),
    [
        ([2, 0], 1),  # the remake passed
        ([1, 3], 0),  # the remake was worse: the first clip is kept
        ([3, 1, 2], 1),  # remake 1 was the best of three
        ([2, 2, 1], 2),
        ([1, 1], 1),  # tied: the newest
        ([2, 1, 1], 2),
        ([1, 2, 1], 2),
        ([1, 1, 3], 1),  # tied best, then a worse one: the newer of the tied
        ([4], 0),  # one clip only
    ],
)
def test_the_clip_with_the_fewest_failed_answers_is_kept_the_newest_on_a_tie(
    failed_answers: list[int], kept: int
) -> None:
    assert clip_to_keep(failed_answers) == kept


@pytest.mark.parametrize(
    ("ruling", "counted"),
    [
        ({"verdict": "fail", "failed_answers": 2}, 2),
        # A pass can have failed answers, one per question on different asks: it still counts
        # as none, so it is kept over any failed clip (stand-in call 8).
        ({"verdict": "pass", "failed_answers": 3}, 0),
        # As can a clip the judge couldn't check (stand-in call 7).
        ({"verdict": "not_checked", "failed_answers": 1}, 0),
    ],
)
def test_only_a_failed_clips_failed_answers_count(ruling: dict[str, Any], counted: int) -> None:
    assert failed_answers(ruling) == counted
