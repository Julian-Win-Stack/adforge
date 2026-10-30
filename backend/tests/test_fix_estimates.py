"""How an estimate's amount is said, and the fix words CONTEXT.md names. Plain checks kept
apart from tests/test_producer_fixes.py so they run without a database."""

from decimal import Decimal
from pathlib import Path

import pytest

from agents.money import dollars


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
