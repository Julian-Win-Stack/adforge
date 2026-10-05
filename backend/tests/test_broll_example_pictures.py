"""Which example pictures a way 3 B-roll scene sends, and the answer that names each one:
the rules alone, without a chat. Driven through the chat in test_producer_broll_way3.py."""

from typing import Any

import pytest
from pydantic import ValidationError

from jobs.scenes import (
    ExamplePicture,
    NeedPhoto,
    broll_examples_choice_for,
    example_pictures,
    photos_for_needs,
)

# Photos 3 and 5 show a stranger's face; 4 and 6 don't.
FACES = {1: False, 3: True, 4: False, 5: True, 6: False}


@pytest.mark.parametrize(
    ("photos", "sent"),
    [
        pytest.param([3, 4], 4, id="the first without a face"),
        pytest.param([4, 6], 4, id="the first of several without a face"),
        pytest.param([5, 3], 5, id="all with a face: the first"),
        pytest.param([9, 3], 3, id="a photo the job doesn't have is passed over"),
    ],
)
def test_a_need_is_shown_by_its_first_photo_without_a_face(photos: list[int], sent: int) -> None:
    (need,) = photos_for_needs([{"what": "the gel", "photos": photos}], FACES, most=3)
    assert need.photo == sent


def test_a_photo_two_needs_share_is_sent_once_for_both() -> None:
    needs = [{"what": "the gel", "photos": [4]}, {"what": "the foam", "photos": [3, 4]}]
    assert photos_for_needs(needs, FACES, most=3) == [
        NeedPhoto(what=["the gel", "the foam"], photo=4, has_face=False)
    ]


def test_a_need_with_no_photo_the_job_has_sends_nothing() -> None:
    assert photos_for_needs([{"what": "the gel", "photos": [9]}], FACES, most=3) == []


def test_needs_past_the_most_that_fit_are_left_out() -> None:
    needs = [{"what": str(n), "photos": [n]} for n in (3, 4, 5, 6)]
    assert [need.photo for need in photos_for_needs(needs, FACES, most=2)] == [3, 4]


GEL = NeedPhoto(what=["the gel"], photo=4, has_face=False)
WOMAN = NeedPhoto(what=["the colour"], photo=3, has_face=True)


@pytest.mark.parametrize(
    ("main", "portrait", "sent"),
    [
        pytest.param(
            1,
            True,
            [ExamplePicture(1), ExamplePicture(4), ExamplePicture(3, True), ExamplePicture(None)],
            id="main, needs, then the portrait",
        ),
        pytest.param(
            4,
            False,
            [ExamplePicture(4), ExamplePicture(3, True)],
            id="a need's photo that is the main photo isn't sent twice",
        ),
    ],
)
def test_the_pictures_are_sent_in_order(
    main: int, portrait: bool, sent: list[ExamplePicture]
) -> None:
    assert example_pictures(main, [GEL, WOMAN], portrait) == sent


def slot(job: str, ignore: str | None = None) -> dict[str, Any]:
    return {"job": job, "ignore": ignore}


ANSWER: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "It shows the bottle's front.",
    "image_1": slot("the bottle; keep its label exactly"),
    "image_2": slot("only the gel's colour"),
    "action": "A hand squeezes the bottle under the rim.",
    "prompt_reason": "It shows the gel going on.",
}


def test_each_picture_sent_has_a_required_slot() -> None:
    choice = broll_examples_choice_for([1], [GEL], portrait=False)
    schema = choice.model_json_schema()
    assert {"image_1", "image_2"} <= set(schema["required"])
    assert "image_3" not in schema["properties"]


def test_the_joined_prompt_names_every_picture_then_the_action() -> None:
    choice = broll_examples_choice_for([1], [GEL, WOMAN], portrait=True)
    answer = choice.model_validate(
        {
            **ANSWER,
            "image_3": slot("only the toilet's shape", "the woman."),
            "image_4": slot("the presenter"),
        }
    )
    assert answer.video_prompt() == (
        "Image 1 is the bottle; keep its label exactly. Image 2 is only the gel's colour. "
        "Image 3 is only the toilet's shape; ignore the woman. Image 4 is the presenter. A "
        "hand squeezes the bottle under the rim."
    )


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        pytest.param({"image_2": None}, "image_2", id="a picture sent with no slot"),
        pytest.param({"photo": 4}, "Photo 4 isn't one", id="a main photo not in the colour"),
        pytest.param({"image_2": slot(" ")}, "can't be empty", id="an empty job"),
        pytest.param({"action": ""}, "can't be empty", id="an empty action"),
    ],
)
def test_a_broken_answer_is_refused(changes: dict[str, Any], why: str) -> None:
    choice = broll_examples_choice_for([1], [GEL], portrait=False)
    with pytest.raises(ValidationError, match=why):
        choice.model_validate({**ANSWER, **changes})


def test_a_slot_given_for_a_picture_not_sent_is_refused() -> None:
    # Photo 4 shows the product in the ad's colour too, and is the gel's photo: picked as
    # the main photo, it isn't sent twice, so only one picture is sent.
    choice = broll_examples_choice_for([1, 4], [GEL], portrait=False)
    with pytest.raises(ValidationError, match="image_2 must be null"):
        choice.model_validate({**ANSWER, "photo": 4})
    answer = choice.model_validate({**ANSWER, "photo": 4, "image_2": None})
    assert answer.video_prompt().startswith("Image 1 is the bottle; keep its label exactly. A ")
