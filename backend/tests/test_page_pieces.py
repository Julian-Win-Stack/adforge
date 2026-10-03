"""The small functions that reading a page is built from, each with its tricky inputs."""

import pytest

from jobs.page_text import match_back

# The page a model copied from, as plain text.
STUDIO = "The mug is thrown by hand in our Leeds studio and glazed in sage green."
PAGE = f"""Stoneware Mug
{STUDIO} It's “dishwasher safe”, and it holds 350 ml.
Colours: sage green, cream, charcoal, oat, rust, slate blue, moss, sand, plum, ink
You may also like the Stoneware Bowl, thrown in the same studio."""


@pytest.mark.parametrize(
    ("copied", "kept"),
    [
        pytest.param([STUDIO], [STUDIO], id="a sentence as the page wrote it"),
        pytest.param(
            ['It\'s "dishwasher safe", and it holds 350 ml.'],
            ['It\'s "dishwasher safe", and it holds 350 ml.'],
            id="straight quotes for the page's curly ones",
        ),
        pytest.param(
            ["THE MUG IS THROWN BY HAND IN OUR LEEDS STUDIO AND GLAZED IN SAGE GREEN."],
            ["THE MUG IS THROWN BY HAND IN OUR LEEDS STUDIO AND GLAZED IN SAGE GREEN."],
            id="capital letters",
        ),
        pytest.param(
            ["The mug is  thrown by hand\tin our Leeds studio   and glazed in sage green."],
            ["The mug is  thrown by hand\tin our Leeds studio   and glazed in sage green."],
            id="extra spaces",
        ),
        pytest.param(
            ["The mug is thrown by hand in our Leeds workshops and glazed in sage."],
            [STUDIO],
            id="86% alike: kept as the page's sentence",
        ),
        pytest.param(
            ["The mug is made by hand in our Leeds workshop and glazed in sage green."],
            [],
            id="84% alike: dropped",
        ),
        pytest.param(
            ["cream, charcoal, oat, rust"],
            ["cream, charcoal, oat, rust"],
            id="a sentence inside a long list",
        ),
        pytest.param(
            ["- **Colours:** sage green, [cream](/products/mug-cream), charcoal"],
            ["Colours: sage green, cream, charcoal"],
            id="markdown the page text doesn't have",
        ),
        pytest.param(
            [f"{STUDIO} It comes with a lifetime guarantee against chips."],
            [STUDIO],
            id="a made-up sentence inside a real passage",
        ),
        pytest.param([STUDIO, "Stoneware Mug", STUDIO], [STUDIO, "Stoneware Mug"], id="kept once"),
        pytest.param([], [], id="an empty answer"),
        pytest.param(["", "Sage."], [], id="bits too short to be a sentence"),
    ],
)
def test_a_copied_sentence_is_kept_only_if_the_page_says_it(
    copied: list[str], kept: list[str]
) -> None:
    assert match_back(copied, PAGE) == kept
