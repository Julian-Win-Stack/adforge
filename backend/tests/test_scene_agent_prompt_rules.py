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
            if re.search(
                r"hold still|same grip|anchored|\bfreeze\b|stay in place|stays on|same angle"
                r"|maintains",
                sentence,
            ):
                assert "never" in sentence, sentence


def test_scenes_without_her_face_never_freeze_a_hand_or_the_product() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_forms import VideoForm

    beats = VideoForm.model_fields["beats"].description or ""
    for text in (INSTRUCTIONS, beats):
        assert "In a scene without her face" in text
        assert "except a planned still shot" in text
        assert "never tell a hand or the product to hold still" in text
        assert "describe the real movement" in text
        assert "The lock line keeps the product's shape, not its stillness" in text


def test_the_hook_asks_for_a_calm_natural_face_written_once() -> None:
    """Round 13 hooks wrote face moves in beats, feeling and after_last_word; the hook now
    keeps her face natural and writes it only in feeling (Julian graded 5 of 5, Nov 9)."""
    from agents.scene_forms import HOOK_PART, VideoForm

    descriptions = {name: field.description or "" for name, field in VideoForm.model_fields.items()}

    feeling = descriptions["feeling"]
    hook = feeling[feeling.index(f"The hook ({HOOK_PART}) instead") : feeling.index("A demo:")]
    assert "natural and relaxed" in hook
    assert "at most one small face cue" in hook
    assert "natural blinks" in hook
    for big in ("no wide eyes", "no big grin", "no smirk", "no eyebrow raise", "no nod after"):
        assert big in hook
    # Problem scenes keep the acted feeling.
    assert "A problem scene: amused or a bit fed up (raises her eyebrows, smirks" in feeling

    beats = descriptions["beats"]
    assert (
        f"In the hook ({HOOK_PART}) the beats are only her free hand, shoulders and body" in beats
    )
    assert "her face goes only in feeling" in beats

    after = descriptions["after_last_word"]
    assert f"In the hook ({HOOK_PART}) it is a calm settle with no new face reaction" in after
