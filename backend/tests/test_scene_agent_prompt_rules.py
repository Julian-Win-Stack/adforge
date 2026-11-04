"""Words a scene agent may not put in its video prompt."""

import pytest

from agents.scene_agent.lifecycle import BANNED


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


@pytest.mark.parametrize(
    ("text", "quoted"),
    [
        ('The banner reads "Save today".', True),
        ('A banner reads "Save\ntoday".', True),
        ("The label reads 'Made for you'.", True),
        ("The banner reads “Save today”.", True),
        ("The label reads ‘Made for you’.", True),
        ("“It’s yours” appears along the top.", True),
        ("'It's yours' appears along the top.", True),
        ("It's open and she'll hold the product's lid.", False),
        ("It’s open and she’ll hold the product’s lid.", False),
        ("The products' labels face the lens.", False),
        ("A banner sits above the product.", False),
        ("not needed here", False),
    ],
)
def test_quoted_video_words_are_found_but_apostrophes_are_allowed(text: str, quoted: bool) -> None:
    from agents.scene_forms import has_quoted_text

    assert has_quoted_text(text) is quoted
