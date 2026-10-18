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


def test_the_clean_pictures_are_numbered_first_and_each_said_to_win() -> None:
    assert reference_photos.picture_label(["black toilet", "blue brush"]) == (
        "Image 1 is the shop owner's own black toilet: wherever it disagrees with another "
        "picture, follow Image 1. Image 2 is the shop owner's own blue brush: wherever it "
        "disagrees with another picture, follow Image 2. Image 3 is the product photo."
    )


def test_in_reference_mode_the_clip_starts_like_image_1_and_the_clean_pictures_follow() -> None:
    assert reference_photos.reference_mode_label(["green gel"]) == (
        "Image 1 is how the clip starts: the clip starts exactly like Image 1. Image 2 is the "
        "shop owner's own green gel: wherever it disagrees with another picture, follow Image 2."
    )


def test_the_clean_picture_prompt_never_copies_a_face_or_invents_a_side() -> None:
    prompt = reference_photos.clean_picture_prompt("apron")
    assert prompt.startswith("A clean photo of only the apron in this photo")
    assert "Never copy any person's face." in prompt
    # Locked rule 3: it never invents a side the photo doesn't show.
    assert "never from a side the photo doesn't show" in prompt
    assert "front" not in prompt


def test_the_writer_names_the_product_photo_by_no_image_number_and_picks_it_by_its_photo_number(
) -> None:
    rules = reference_photos.writer_rules(["black toilet", "blue brush"], [])
    assert "(Image 1: their black toilet; Image 2: their blue brush)" in rules
    # One number only (case C12b): told the product photo was Image 3, the writer answered
    # "photo" with 3, not a photo of the product.
    assert "Image 3" not in rules
    assert 'Wherever the rules above say Image 1, write "the product photo"' in rules
    assert 'Answer "photo" with its Photo number' in rules


def test_the_writer_is_told_of_things_only_seen_during_the_clip() -> None:
    assert reference_photos.writer_rules([], ["green gel", "foam", "brush"]) == (
        reference_photos.during_clip_rules(["green gel", "foam", "brush"])
    )
    assert "their green gel, their foam and their brush" in (
        reference_photos.during_clip_rules(["green gel", "foam", "brush"])
    )
    assert reference_photos.writer_rules([], []) == ""
