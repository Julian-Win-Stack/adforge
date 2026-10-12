"""A new starting picture for a finished scene un-finishes it, driven through the chat: the ad
can't be assembled again until the scene's clip is made from the new picture, so an ad never
quietly reuses a clip of the old one (#133). The producer's model and the scene models are
faked at the gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db.models.signals import post_save

from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.models import ToolCall
from agents.money import dollars
from gateway.fake import FakeModel
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, results_of
from .test_producer_broll import (
    BROLL_CHOICE,
    TALKING_CHOICE,
    assemble_through_the_chat,
    calling,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_broll_picture_check import FIXED_CHOICE, FUSED_HANDLE, impossible
from .test_producer_restarts import WorkerStopped, the_worker_stops

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


def new_picture(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    number: int,
    note: str = HIGHER,
) -> None:
    """Make scene `number`'s starting picture again, as the shop owner's note asks."""
    calling(fake_model, say, ("make_starting_picture", {"scene": number, "note": note}))
    fake_model.respond(
        "choose_broll_picture" if number == 2 else "choose_starting_picture",
        BROLL_CHOICE if number == 2 else TALKING_CHOICE,
    )
    run(fake_model, steps)


def ads() -> list[ProducedItem]:
    return list(Job.objects.get().produced.filter(kind="finished_ad").order_by("version"))


def assemble(fake_model: FakeModel, say: Callable[..., None]) -> str:
    calling(fake_model, say, ("assemble_ad", {}))
    return results_of("assemble_ad")[-1]


@pytest.mark.parametrize("number", [2, 1], ids=["a B-roll scene", "a talking scene"])
def test_a_new_picture_unfinishes_its_scene_until_its_clip_is_made_from_it(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,
    number: int,
) -> None:
    old_clips = {scene: clip(scene) for scene in (1, 2, 3)}
    assert status(number) == "finished"

    new_picture(fake_model, steps, say, number)

    assert status(number) == "planned"
    assert {status(other) for other in (1, 2, 3) if other != number} == {"finished"}
    step = picture_step(number)
    assert f"Scene {number} is no longer finished: make its clip from this picture." in (
        step.result
    )
    assert assemble(fake_model, say) == (
        f"Refused: scene {number} isn't finished: a scene is finished once its clip is made. "
        "Finish it, then assemble the ad. Nothing was done."
    )

    calling(fake_model, say, ("make_clip", {"scene": number}))
    run(fake_model, steps)

    remade = clip(number)
    assert remade.version == 2
    assert remade.picture == picture(number) == step.produced.get()
    assert status(number) == "finished"
    assemble(fake_model, say)
    first, second = ads()
    assert first == assembled
    expected = [remade if scene == number else old_clips[scene] for scene in (1, 2, 3)]
    assert [cut["clip"] for cut in second.cuts] == [made.pk for made in expected]
    assert [cut["scene"] for cut in second.cuts] == [1, 2, 3]
    # The other scenes' clips were left as they were.
    assert {clip(scene) for scene in (1, 2, 3) if scene != number} == {
        old_clips[scene] for scene in (1, 2, 3) if scene != number
    }


def test_a_picture_that_cant_be_made_leaves_the_scene_finished_with_its_old_clip(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    old = clip(2)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": HIGHER}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    fake_model.respond("make_starting_picture", *[OutsideServiceDown("503 from OpenAI")] * 3)
    run(fake_model, steps)

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"
    assert clip(2) == old
    assert assemble(fake_model, say).startswith("The ad was already assembled")
    assert ads() == [assembled]


def test_a_picture_that_keeps_failing_its_check_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    old = clip(2)
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": HIGHER}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)
    steps.run_held()

    assert picture_step(2).status == "stopped"
    assert status(2) == "finished"
    assert clip(2) == old


def test_two_new_pictures_in_a_row_leave_the_scene_unfinished_and_its_clip_uses_the_latest(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    new_picture(fake_model, steps, say, 2, HIGHER)
    first = picture_step(2)
    new_picture(fake_model, steps, say, 2, "Pour the tea slower.")

    assert status(2) == "planned"
    assert "Scene 2 is no longer finished" in first.result
    # It was already planned again by the first.
    assert "no longer finished" not in picture_step(2).result
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    assert picture(2).version == 3
    assert clip(2).picture == picture(2)
    assert status(2) == "finished"


def test_no_new_picture_is_started_while_the_scenes_clip_is_being_made(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    # So a clip of an older picture can never finish the scene after a newer picture.
    new_picture(fake_model, steps, say, 2)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert len(steps.held) == 1

    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": "Pour slower."}))

    assert results_of("make_starting_picture")[-1] == (
        "Refused: scene 2's clip is still being made. You'll be told when it's ready; make its "
        "starting picture again then. Nothing was done."
    )
    run(fake_model, steps)
    assert status(2) == "finished"
    assert clip(2).picture == picture(2)
    assert picture(2).version == 2


def test_a_worker_stopping_as_the_picture_is_kept_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": HIGHER}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)

    with (
        the_worker_stops(post_save, ProducedItem, when=lambda item: item.version == 2),
        pytest.raises(WorkerStopped),
    ):
        steps.run_next()
    run(fake_model, steps)
    tasks.carry_on_after_the_worker_starts()

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"


def test_a_worker_stopping_before_the_picture_step_finishes_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    # The scene is planned again only as the step finishes and the producer is told, so a
    # picture kept on a step that then failed changes nothing, as if it couldn't be made.
    old = clip(2)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": HIGHER}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)

    with (
        the_worker_stops(post_save, SceneStep, when=lambda step: step.status == "finished"),
        pytest.raises(WorkerStopped),
    ):
        steps.run_next()
    run(fake_model, steps)
    tasks.carry_on_after_the_worker_starts()

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"
    assert clip(2) == old
    assert assemble(fake_model, say).startswith("The ad was already assembled")


def test_a_scene_already_planned_again_isnt_said_to_be_no_longer_finished(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    # As when its line was changed and changed back before the new picture.
    Scene.objects.filter(number=2).update(status="planned")

    new_picture(fake_model, steps, say, 2)

    assert status(2) == "planned"
    assert "no longer finished" not in picture_step(2).result


def test_a_scenes_first_picture_leaves_it_planned_and_says_nothing_of_unfinishing(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    new_picture(fake_model, steps, say, 2, note="")

    assert status(2) == "planned"
    assert "no longer finished" not in picture_step(2).result


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


@pytest.mark.parametrize(
    ("amount", "said"),
    [
        (Decimal("0.40"), "$0.40"),
        (Decimal("0.0025"), "$0.0025"),
        (Decimal("1.50"), "$1.50"),
        (Decimal("1.5"), "$1.50"),
        (Decimal("0"), "$0.00"),
        (Decimal("0.0155"), "$0.0155"),
    ],
)
def test_an_amount_is_said_in_dollars_to_the_cent_or_its_last_digit(
    amount: Decimal, said: str
) -> None:
    assert dollars(amount) == said


def test_a_step_that_cost_nothing_says_so(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ToolCall, "cost_usd", lambda _: Decimal(0))
    new_picture(fake_model, steps, say, 1, note="")

    assert " It cost $0.00." in picture_step(1).result
