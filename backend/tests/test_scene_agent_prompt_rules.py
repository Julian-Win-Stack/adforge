"""The scene agent describes action; code supplies the spoken words."""

import pytest


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
