"""The H3 clip length and the spoken pace, from the agreed speaking speed, without a database."""

import pytest

from jobs.pace import (
    FIXED_WORDS_PER_SECOND,
    asks_for_a_slow_pace,
    h3_clip_seconds,
    spoken_pace,
)


def test_lines_are_planned_at_a_natural_ad_pace() -> None:
    assert FIXED_WORDS_PER_SECOND == 2.8


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
    ("voice", "slow"),
    [
        ("A warm woman's voice, mid-30s, at an easy, unhurried pace.", "unhurried"),
        ("Friendly and calm, speaking slowly.", "slowly"),
        ("A relaxed pace, soft and kind.", "relaxed pace"),
        ("A leisurely, gentle voice.", "leisurely"),
        ("She takes her time with every word.", "takes her time"),
        ("Bright, upbeat, natural conversational pace.", None),
        ("Warm and steady, mid-30s, American accent.", None),
    ],
)
def test_voice_words_asking_for_a_slow_pace_are_found(voice: str, slow: str | None) -> None:
    assert asks_for_a_slow_pace(voice) == slow
