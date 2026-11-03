"""A new starting picture for a finished talking scene un-finishes it: the ad can't be assembled
again until the scene's clip is made from the new picture, so an ad never quietly reuses a
clip of the old one (#133). The new picture is the producer's, made with a note, then its
clip. The ad is first made through the chat; the producer's model and the scene models are
faked at the gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, results_of
from .test_producer_broll import (
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked"),
]


HIGHER = "Hold it higher."


@pytest.fixture
def assembled(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> ProducedItem:
    """The first ad, made through the chat: scenes 1 and 3 talk, scene 2 shows the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


def status(number: int) -> str:
    return Scene.objects.get(number=number).status


def clip(number: int) -> ProducedItem:
    """Scene `number`'s latest clip."""
    return ProducedItem.objects.filter(kind="clip", scene__number=number).latest("version")


def picture(number: int) -> ProducedItem:
    """Scene `number`'s latest starting picture."""
    return ProducedItem.objects.filter(kind="starting_picture", scene__number=number).latest(
        "version"
    )


def picture_step(number: int) -> SceneStep:
    return picture(number).step


def ads() -> list[ProducedItem]:
    return list(Job.objects.get().produced.filter(kind="finished_ad").order_by("version"))


def assemble(fake_model: FakeModel, say: Callable[..., None]) -> str:
    """Have the producer assemble the ad, asked in the chat, and give what it was told."""
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="On it."))
    say("Assemble the ad, please.")
    return results_of("assemble_ad")[-1]


def new_picture(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    number: int,
    note: str = HIGHER,
) -> None:
    """Draw and film a new version through the same scene agent."""
    from .scene_script import make_scene_clip

    make_scene_clip(fake_model, steps, say, scene=number, instruction=note)


def held_kinds(steps: HeldSteps) -> list[str]:
    return [SceneStep.objects.get(pk=held).kind for held in steps.held]


# --- What each step cost ------------------------------------------------------------------------
