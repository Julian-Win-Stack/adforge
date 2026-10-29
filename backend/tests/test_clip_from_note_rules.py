"""What the clip-from-a-note tool takes and what its prompt rewrite is told. Plain checks kept
apart from tests/test_producer_clip_from_note.py so they run without a database."""

import pytest

from agents.producer import MakeClip, normalised_note
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_REFERENCES_RULES,
    broll_prompt_instructions,
    clip_from_references_instructions,
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
    told = clip_from_references_instructions(kind)

    assert told.startswith(BROLL_SHARED_RULES)
    assert BROLL_KIND_RULES[kind] in told
    assert told.endswith(CLIP_FROM_REFERENCES_RULES)
    for other in set(BROLL_KIND_RULES) - {kind}:
        assert BROLL_KIND_RULES[other] not in told


def test_a_note_naming_a_movement_beats_the_one_movement_rule_only_in_a_note_rewrite() -> None:
    # Julian's grade of the real-clip run (Oct 18): "show the gel going onto the brush first"
    # was dropped because the one-movement rule won. The note wins only in a rewrite from a
    # note: the proven rules a scene's prompts are first written with stay as they are.
    for kind in BROLL_KIND_RULES:
        assert "wins over the rules above" not in broll_prompt_instructions(kind)
