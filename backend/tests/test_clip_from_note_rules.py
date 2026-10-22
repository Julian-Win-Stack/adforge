"""What the clip-from-a-note tool takes and what its prompt rewrite is told. Plain checks kept
apart from tests/test_producer_clip_from_note.py so they run without a database."""

import pytest

from agents.producer import MakeClip, normalised_note
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_NOTE_RULES,
    broll_prompt_instructions,
    clip_from_note_instructions,
)

from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    unlabelled,  # noqa: F401 (a fixture)
)


def test_the_tool_takes_a_note() -> None:
    assert list(MakeClip.model_json_schema()["properties"]) == ["scene", "note"]


@pytest.mark.parametrize(
    ("note", "kept"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Pour the tea.", "Pour the tea."),
        ("  Pour   the\ttea.\n", "Pour the tea."),
    ],
)
def test_a_note_is_normalised_as_the_picture_tool_does(note: str | None, kept: str) -> None:
    assert normalised_note(note) == kept


@pytest.mark.parametrize("kind", list(BROLL_KIND_RULES))
def test_the_rewrite_is_told_the_shared_rules_its_kinds_rules_and_the_clip_only_block(
    kind: str,
) -> None:
    told = clip_from_note_instructions(kind)

    assert told.startswith(BROLL_SHARED_RULES)
    assert BROLL_KIND_RULES[kind] in told
    assert told.endswith(CLIP_FROM_NOTE_RULES)
    for other in set(BROLL_KIND_RULES) - {kind}:
        assert BROLL_KIND_RULES[other] not in told


def test_the_clip_only_block_says_the_picture_is_fixed_and_only_the_movement_changes() -> None:
    assert CLIP_FROM_NOTE_RULES == (
        "The scene's starting picture is already made, and it stays as it is: you are shown "
        'it, and "picture_prompt" is the prompt it was drawn from. Pick no photo and write no '
        'picture prompt. Write only a new video prompt: "motion_prompt" is the one the '
        'scene\'s first clip from this picture was made from, before any note, and "note" '
        "says what the shop owner wants changed about how it moves. Change the movement as the "
        "note asks, starting from what the picture shows, and keep what was right. When the "
        'note names a movement to film, such as a step before the one "shows" names, like '
        "putting the product on the tool used with it, film every movement the note names, in "
        "the order it says: here the note wins over the rules above that say to film one "
        "movement only, never to film or draw putting the product on, and to follow a note "
        'only for what "shows" describes. Every other rule of this scene\'s kind still holds, '
        "such as nobody touching the product in a scene that shows the problem. Follow the "
        "note unless it asks for something that "
        "can't start from this picture, or something else \"shows\" doesn't describe, and then "
        "say so in the reason. Give a one-sentence reason for the video prompt, written for "
        "the shop owner."
    )


def test_a_note_naming_a_movement_beats_the_one_movement_rule_only_in_a_note_rewrite() -> None:
    # Julian's grade of the real-clip run (Oct 18): "show the gel going onto the brush first"
    # was dropped because the one-movement rule won. The note wins only in a rewrite from a
    # note: the proven rules a scene's prompts are first written with stay as they are.
    for kind in BROLL_KIND_RULES:
        assert "wins over the rules above" not in broll_prompt_instructions(kind)
