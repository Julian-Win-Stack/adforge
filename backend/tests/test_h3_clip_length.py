"""The H3 clip length from the agreed speaking speed, without a database."""

import pytest

from jobs.checks import h3_clip_seconds


@pytest.mark.parametrize(
    ("words", "seconds"), [(0, 5), (1, 5), (10, 5), (11, 6), (20, 9), (25, 11), (35, 15)]
)
def test_the_line_leaves_a_second_for_action_after_its_last_word(words: int, seconds: int) -> None:
    assert h3_clip_seconds(words) == seconds


def test_a_line_over_fourteen_seconds_is_refused() -> None:
    with pytest.raises(ValueError, match="14 seconds"):
        h3_clip_seconds(36)


def test_a_word_count_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        h3_clip_seconds(-1)
