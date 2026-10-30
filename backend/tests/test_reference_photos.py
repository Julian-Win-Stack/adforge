"""How a thing taken from the shop owner's photo is named, and the words code puts before the
picture and video prompts for it (jobs.reference_photos)."""

import pytest

from jobs import reference_photos


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("black toilet", "black toilet"),
        ("the black toilet", "black toilet"),
        ("A  blue   brush ", "blue brush"),
        ("an apron", "apron"),
        ("The", ""),
        ("a", ""),
        ("theme mug", "theme mug"),
        ("anchor", "anchor"),
    ],
)
def test_a_things_name_is_kept_without_its_article(said: str, kept: str) -> None:
    assert reference_photos.name_of(said) == kept


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        ("black mug", "The Black  Mug", True),
        ("black mug", "a black mug", True),
        ("black mug", "blue mug", False),
        ("seat", "toilet seat", False),
    ],
)
def test_two_names_are_the_same_thing_whatever_their_case_spacing_or_article(
    one: str, other: str, same: bool
) -> None:
    assert reference_photos.same_thing(one, other) is same


def test_the_clean_picture_prompt_never_copies_a_face_or_invents_a_side() -> None:
    prompt = reference_photos.clean_picture_prompt("apron")
    assert prompt.startswith("A clean photo of only the apron in this photo")
    assert "Never copy any person's face." in prompt
    # Locked rule 3: it never invents a side the photo doesn't show.
    assert "never from a side the photo doesn't show" in prompt
    assert "front" not in prompt


def test_the_writer_is_told_of_things_only_seen_during_the_clip() -> None:
    assert "their green gel, their foam and their brush" in (
        reference_photos.during_clip_rules(["green gel", "foam", "brush"])
    )
