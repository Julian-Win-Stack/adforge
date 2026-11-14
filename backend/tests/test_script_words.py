"""Shared text normalization for planning, evals and leading-word trimming."""

import pytest

from jobs.script_words import tidy


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Only $10!", "only ten dollars"),
        ("55%", "fifty-five percent"),
        ("two-month", "two month"),
        ("HELLO, world!", "Hello world."),
    ],
)
def test_equivalent_writing_matches(line: str, said: str) -> None:
    assert tidy(line) == tidy(said)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("$10", "$20"),
        ("Buy one, get two", "Buy one"),
        ("One hundred", "One thousand"),
        ("1,2", "12"),
    ],
)
def test_tidy_keeps_real_differences(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("£10", "$10"),
        ("€10", "10"),
        ("-10", "10"),
        ("-$10", "$10"),
    ],
)
def test_tidy_preserves_currency_and_negative_quantities(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("Contains <5% sugar", "Contains 5% sugar"),
        ("Use 1/2 cup", "Use one two cup"),
        ("Dilute 1:2", "Dilute one two"),
        ("Lasts 10-20 days", "Lasts ten twenty days"),
        ("Contains .5 grams", "Contains 5 grams"),
    ],
)
def test_tidy_preserves_symbols_that_change_claims(line: str, said: str) -> None:
    assert tidy(line) != tidy(said)


@pytest.mark.parametrize(
    ("line", "said", "prefix"),
    [
        ("Try the mug today.", "Your tour. Try the mug today.", 2),
        ("Try the mug today.", "Try the mug today. Try the mug today.", 4),
        ("It costs $49.99 today.", "A false start. It costs $39.99 today.", 3),
        ("Get your mug today.", "Get your mug-- get your mug today.", 3),
        ("Try the mug today.", "Try the lovely mug today.", 0),
        ("Try the mug today.", "Try the mug today. Thank you.", 0),
        ("Try the mug today.", "A false start. Try the lovely mug today.", 0),
        ("Try the mug today.", "Try the mug today.", 0),
    ],
)
def test_only_words_before_the_line_can_be_cut(line: str, said: str, prefix: int) -> None:
    from jobs.script_words import leading_words

    assert leading_words(line, said.split()) == prefix
