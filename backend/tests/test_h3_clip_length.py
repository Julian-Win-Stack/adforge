"""The H3 clip length and the spoken pace, from the agreed speaking speed, without a database."""

import pytest

from jobs.assembly import closing_silence_end, opening_silence
from jobs.pace import (
    h3_clip_seconds,
    spoken_pace,
)


@pytest.mark.parametrize(
    ("words", "seconds"),
    # words / 2.8 + 0.6, rounded up, at least 5: a 15-word line gets 6 seconds, not 7.
    [(0, 5), (5, 5), (12, 5), (13, 6), (15, 6), (20, 8), (25, 10), (36, 14), (39, 15)],
)
def test_a_clip_lasts_its_line_and_a_little_action_after_it(words: int, seconds: int) -> None:
    assert h3_clip_seconds(words) == seconds


def test_a_line_over_fourteen_seconds_is_refused_not_cut() -> None:
    # 40 words take 14.3 seconds to say at 2.8 words a second.
    with pytest.raises(ValueError, match="14 seconds"):
        h3_clip_seconds(40)


def test_a_word_count_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        h3_clip_seconds(-1)


def test_the_pace_is_the_words_over_the_time_from_the_first_word_to_the_last() -> None:
    words = [
        {"text": "Meet", "start": 0.5, "end": 0.8},
        {"text": "the", "start": 0.8, "end": 0.9},
        {"text": "mug", "start": 1.0, "end": 1.3},
        {"text": "today.", "start": 1.4, "end": 2.5},
    ]
    assert spoken_pace(words) == 2.0


@pytest.mark.parametrize(
    "words",
    [
        [],
        [{"text": "Hi", "start": 1.0, "end": 1.0}],
        [{"text": "Hi", "start": None, "end": 1.0}],
    ],
)
def test_a_transcript_without_usable_times_has_no_pace(words: list[dict[str, object]]) -> None:
    assert spoken_pace(words) is None


@pytest.mark.parametrize(
    ("first_word", "cut"),
    [(None, 0.0), (0.3, 0.0), (1.2, 1.15), (5.0, 0.0), (6.0, 0.0)],
)
def test_only_an_opening_silence_over_three_tenths_of_a_second_is_cut(
    first_word: float | None, cut: float
) -> None:
    # A first word at or past the clip's end is a broken word time, never cut to.
    assert opening_silence(first_word, 5.0) == cut


@pytest.mark.parametrize(
    ("last_word_end", "end"),
    # A clip of 7.332 s, as H3 hands back for 7 asked for.
    [(None, 7.332), (5.7, 6.2), (6.832, 7.332), (7.0, 7.332), (7.5, 7.332), (0.0, 7.332)],
)
def test_a_clip_keeps_at_most_half_a_second_after_its_last_word(
    last_word_end: float | None, end: float
) -> None:
    # No word time, or one at or past the clip's start or end, plays the clip whole.
    assert closing_silence_end(last_word_end, 7.332) == end
