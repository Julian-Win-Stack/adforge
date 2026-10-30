"""What a clip step tells the producer once the ad has a clip, and the shared fixtures of the
fix tests: the mug's ad made through the chat. The producer's model and the scene models are
faked at the gateway; the clips are real tiny videos."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.catalog import MODEL_FOR_PURPOSE, PRICE_PER_SECOND_OF_VIDEO
from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem, SceneStep

from .conftest import HeldSteps, results_of
from .test_producer_broll import (
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_2 = "Scene 2 just sits there. The tea should actually pour into the mug."
BOREAL = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_broll_clip"]]
HEYGEN = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_talking_clip"]]

Say = Callable[..., None]


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, if any, then
    replies."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="On it."))
    say(text)


def ads() -> list[int]:
    """Each finished ad's version, oldest first."""
    return list(
        ProducedItem.objects.filter(kind="finished_ad")
        .order_by("version")
        .values_list("version", flat=True)
    )


@pytest.fixture
def shown(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> ProducedItem:
    """A chat whose finished ad (version 1) is shown to the shop owner: scenes 1 and 3 have
    the person talking, scene 2 shows the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


# --- Estimates --------------------------------------------------------------------------------


# Spec #210 point 19: the test of a B-roll picture fix's estimate leaving out the ad's shared
# pictures of its place was deleted: a scene that shows the product has no starting picture.


# --- What a fix cost --------------------------------------------------------------------------


# --- The ad assembled by code -----------------------------------------------------------------


def test_a_clip_with_no_fix_names_no_fix_cost_and_assembles_nothing(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    assemble_through_the_chat(fake_model, steps, say)

    for step in SceneStep.objects.filter(kind="clip"):
        assert "fix" not in step.result
        assert step.result.endswith("Tell the shop owner.")
    # Only the producer's assemble_ad made the ad.
    assert ads() == [1]
    assert len(results_of("assemble_ad")) == 1


# --- Fixing a scene before the whole ad is made ------------------------------------------------
# A scene can be fixed as soon as it has its clip, while the others are still being made
# (Julian 2026-10-12 00:26, #135 review question 5).


# --- Words ------------------------------------------------------------------------------------
