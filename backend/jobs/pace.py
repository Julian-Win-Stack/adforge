"""How fast the presenter speaks: the speed every line and clip length is planned at, and
the speed a filmed clip really spoke at, measured from its transcript. Pure: no database,
so the planner's wording, the length checks and the clip lengths all read the same numbers.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any

# H3 speaks its own lines at a natural ad pace: every length check plans for this speed
# (spec #401; round 11's clips spoke 1.93 to 2.10 words a second, too slow for an ad).
FIXED_WORDS_PER_SECOND = 2.8
# Every scene line must fill its clip at the fixed speaking speed.
SHORTEST_BROLL_LINE_SECONDS = 4
LONGEST_BROLL_LINE_SECONDS = 14
# A default-length ad's lines take about this long to say: the speech time of the 12 to 16
# word lines planned at the old 2.5 words a second, so a faster speed means more words, not a
# shorter ad.
TYPICAL_LINE_SECONDS = (4.8, 6.4)
# Each clip plays this long after its line, for the action after the last word, and is
# rounded up to whole seconds, at least SHORTEST_CLIP_SECONDS and at most
# LONGEST_CLIP_SECONDS. No more: padding is time H3 stretches her speech over.
SECONDS_AFTER_THE_LINE = 0.6
SHORTEST_CLIP_SECONDS = 5
LONGEST_CLIP_SECONDS = 15
# A short ad's hook lands its point within this many seconds of the ad starting (TikTok's
# guidance: the main point in the first 3 seconds).
HOOK_POINT_SECONDS = 3


def h3_clip_seconds(word_count: int) -> int:
    """Whole clip length, including the action after the line. Refuse a line longer than
    H3 can speak instead of silently cutting its words."""
    if word_count < 0:
        raise ValueError("A word count cannot be negative.")
    seconds = word_count / FIXED_WORDS_PER_SECOND
    if seconds > LONGEST_BROLL_LINE_SECONDS:
        raise ValueError(f"The line takes more than {LONGEST_BROLL_LINE_SECONDS} seconds to say.")
    # Rounded first, so a float sum such as 5.0000000001 isn't rounded up a whole second.
    whole = math.ceil(round(seconds + SECONDS_AFTER_THE_LINE, 6))
    return min(LONGEST_CLIP_SECONDS, max(SHORTEST_CLIP_SECONDS, whole))


def fewest_words_in_a_broll_line(words_per_second: float) -> int:
    """The fewest words a B-roll line can have and take SHORTEST_BROLL_LINE_SECONDS to say."""
    return math.ceil(round(SHORTEST_BROLL_LINE_SECONDS * words_per_second, 6))


def words_said_in(seconds: float, words_per_second: float) -> int:
    """About how many words are said in `seconds`."""
    return round(seconds * words_per_second)


def typical_words_in_a_line(words_per_second: float) -> tuple[int, int]:
    """About how many words a default-length ad's line has at `words_per_second`."""
    least, most = TYPICAL_LINE_SECONDS
    return round(least * words_per_second), round(most * words_per_second)


def most_words_in_a_broll_line(words_per_second: float) -> int:
    """The most words a B-roll line can have and still be said within
    LONGEST_BROLL_LINE_SECONDS."""
    return math.floor(round(LONGEST_BROLL_LINE_SECONDS * words_per_second, 6))


def spoken_pace(words: Sequence[Mapping[str, Any]]) -> float | None:
    """The words a second a clip really spoke: its words over the time from its first word's
    start to its last word's end. None when the transcript has no usable times."""
    if not words:
        return None
    start, end = words[0].get("start"), words[-1].get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return None
    return round(len(words) / (end - start), 2)
