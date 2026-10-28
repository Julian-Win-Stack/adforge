"""What a fix is estimated at and how an amount is said. Plain checks kept apart from
tests/test_producer_fixes.py so they run without a database."""

from decimal import Decimal
from pathlib import Path

import pytest

from agents.money import dollars
from jobs import work

from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_fixes import BOREAL, HEYGEN


@pytest.mark.parametrize(
    ("audio_seconds", "billed"), [(4.0, 5), (5.0000001, 5), (5.2, 6), (9.0, 9), (14.9, 15)]
)
def test_a_broll_clip_is_estimated_at_its_whole_seconds_at_the_boreal_price(
    audio_seconds: float, billed: int
) -> None:
    assert work.clip_estimate_usd(audio_seconds, broll=True) == billed * BOREAL


@pytest.mark.parametrize(("audio_seconds", "billed"), [(0.5, "1"), (3.5, "3.5"), (9.0, "9.0")])
def test_a_talking_clip_is_estimated_at_its_audio_at_the_heygen_price(
    audio_seconds: float, billed: str
) -> None:
    assert work.clip_estimate_usd(audio_seconds, broll=False) == Decimal(billed) * HEYGEN


@pytest.mark.parametrize(
    ("amount", "said"),
    [
        ("0.396", "$0.396"),
        ("0.4", "$0.40"),
        ("1.5", "$1.50"),
        ("0", "$0.00"),
        ("2.05", "$2.05"),
        ("0.0025", "$0.0025"),
        ("0.0155", "$0.0155"),
    ],
)
def test_estimates_are_in_dollars_to_the_cent_or_their_last_digit(amount: str, said: str) -> None:
    assert dollars(Decimal(amount)) == said


def test_context_names_note_and_fix_under_fixing_a_scene() -> None:
    context = (Path(__file__).resolve().parents[2] / "CONTEXT.md").read_text()
    language = context.split("## Language", 1)[1]
    fixing = language.split("### Fixing a scene", 1)[1].split("\n### ", 1)[0]

    assert "**Note**:" in fixing
    assert "**Fix**:" in fixing
