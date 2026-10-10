"""The check of a B-roll starting picture: reading its answer, without a chat. Driven
through the chat in test_producer_broll_picture_check.py."""

from typing import Any

import pytest
from pydantic import ValidationError

from jobs.picture_check import StartingPictureCheck

from .conftest import PICTURE_OK


def test_a_check_that_fails_without_saying_why_is_refused() -> None:
    with pytest.raises(ValidationError, match="must say what is wrong"):
        StartingPictureCheck.model_validate(
            {**PICTURE_OK, "real_objects": {"passes": False, "problem": " "}}
        )


def test_the_problems_are_those_of_each_check_that_fails() -> None:
    answer: dict[str, Any] = {
        **PICTURE_OK,
        "real_objects": {"passes": False, "problem": "The rim dips at the front."},
        "no_face": {"passes": False, "problem": "Her chin is at the top."},
    }
    assert StartingPictureCheck.model_validate(answer).problems() == [
        "The rim dips at the front.",
        "Her chin is at the top.",
    ]
    assert StartingPictureCheck.model_validate(PICTURE_OK).problems() == []
