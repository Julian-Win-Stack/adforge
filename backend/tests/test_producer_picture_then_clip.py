"""A new starting picture for a finished talking scene un-finishes it: the ad can't be assembled
again until the scene's clip is made from the new picture, so an ad never quietly reuses a
clip of the old one (#133). The new picture is the producer's, made with a note, then its
clip. The ad is first made through the chat; the producer's model and the scene models are
faked at the gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from decimal import Decimal

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, results_of
from .test_producer_broll import (
    TALKING_CHOICE,
    assemble_through_the_chat,
    calling,
    checked,  # noqa: F401 (a fixture)
    picture_of,
    run,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]


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
    return SceneStep.objects.filter(kind="starting_picture", scene__number=number).latest("id")


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
    """Make talking scene `number`'s starting picture again with the producer's `note`, then
    its clip, through the chat."""
    calling(fake_model, say, ("make_starting_picture", {"scene": number, "note": note}))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, ("make_clip", {"scene": number}))
    run(fake_model, steps)


def held_kinds(steps: HeldSteps) -> list[str]:
    return [SceneStep.objects.get(pk=held).kind for held in steps.held]


def test_a_scenes_first_picture_leaves_it_planned_and_says_nothing_of_unfinishing(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    picture_of(fake_model, steps, say, 3)

    assert status(3) == "planned"
    assert "no longer finished" not in picture_step(3).result


# --- What each step cost ------------------------------------------------------------------------


def test_every_finished_step_tells_the_producer_what_it_cost(assembled: ProducedItem) -> None:
    # Scene 1 is a talking scene of 8 words: what the fake's model calls cost for it.
    told = {
        step.kind: step.result
        for step in SceneStep.objects.filter(scene__number=1, status="finished")
    }
    assert {kind: said.split(" It cost ")[1].split(" ")[0] for kind, said in told.items()} == {
        "starting_picture": "$0.0155.",
        "line_audio": "$0.00095.",
        "transcript": "$0.000244.",
        "clip": "$0.20.",
    }


def test_a_step_that_cost_nothing_says_so(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SceneStep, "cost_usd", lambda _: Decimal(0))
    picture_of(fake_model, steps, say, 1)

    assert " It cost $0.00." in picture_step(1).result
