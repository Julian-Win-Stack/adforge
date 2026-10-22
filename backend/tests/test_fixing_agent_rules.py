"""What the fixing agent's video prompt may not say. Plain checks of the words, kept apart
from tests/test_fixing_agent.py, which tests the refusal through a fix."""

import pytest

from agents.fixing_agent import BANNED


@pytest.mark.parametrize(
    ("prompt", "bans"),
    [
        ("The tea pours. Nothing spills.", {"nothing"}),
        ("The tea pours and doesn't spill, with no drips.", {"n't", "no"}),
        ("Avoid spills; never tip the mug.", {"avoid", "never"}),
        ("The tea pours into the mug and steams.", set()),
    ],
)
def test_the_bans_in_a_video_prompt_are_found(prompt: str, bans: set[str]) -> None:
    assert {match.group(0).lower() for match in BANNED.finditer(prompt)} == bans
