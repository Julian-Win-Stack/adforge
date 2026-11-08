"""The scene agent describes action; code supplies the spoken words."""

import re

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


def test_talking_video_rules_keep_the_product_shape_and_let_the_woman_move() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_forms import VideoForm
    from jobs.planning import PLAN_INSTRUCTIONS

    descriptions = {name: field.description or "" for name, field in VideoForm.model_fields.items()}
    product_move = descriptions["product_move"]
    assert "one small move" in product_move
    assert "at the end of the first sentence" in product_move
    assert "then back" in product_move
    assert "photographed side stays to the camera" in product_move
    assert "scene's one action with the product" in product_move
    assert "not needed here" in product_move and "B-roll" in product_move

    beats = descriptions["beats"]
    assert "free hand, head and shoulders move as she talks" in beats
    assert "product_move, without repeating it here" in beats
    assert "the woman, never the presenter" in beats
    assert "fill delivery, feeling, product_move and sounds" in INSTRUCTIONS
    assert "Only the product is locked" in INSTRUCTIONS

    # Negative reminders forbid these poses; no instruction may ask her to do one.
    for text in (PLAN_INSTRUCTIONS, INSTRUCTIONS, *descriptions.values()):
        for sentence in re.split(r"[.!?\n]", text.lower()):
            if re.search(r"hold still|same grip|anchored|\bfreeze\b", sentence):
                assert "never" in sentence, sentence
