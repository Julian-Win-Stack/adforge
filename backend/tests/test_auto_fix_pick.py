"""The clip an auto-fix keeps (#155): of the clips the judge saw, the one with the fewest
failed answers, the newest of those tied."""

import pytest

from jobs.auto_fix import clip_to_keep


@pytest.mark.parametrize(
    ("failed_answers", "kept"),
    [
        ([2, 0], 1),  # the redo passed
        ([1, 3], 0),  # the redo was worse: the first clip is kept
        ([3, 1, 2], 1),  # redo 1 was the best of three
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
