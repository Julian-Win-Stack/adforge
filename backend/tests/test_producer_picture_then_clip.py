"""A new starting picture for a finished scene un-finishes it: the ad can't be assembled
again until the scene's clip is made from the new picture, so an ad never quietly reuses a
clip of the old one (#133). The new picture is a fix plan's, said yes to and run by code
(fix redesign), which makes its clip by itself once the picture passes its checks. The ad is
first made through the chat; the producer's model and the scene models are faked at the
gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db.models.signals import post_save

from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.money import dollars
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import FixPlan, Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, results_of
from .fixing import fixed
from .test_producer_broll import (
    BROLL_CHOICE,
    TALKING_CHOICE,
    assemble_through_the_chat,
    calling,
    checked,  # noqa: F401 (a fixture)
    picture_of,
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


def a_new_picture_said_yes_to(fake_model: FakeModel, number: int, note: str = HIGHER) -> FixPlan:
    """The shop owner said yes to a plan giving scene `number` a new starting picture for
    `note`: its picture step is queued, and its picture writer will answer."""
    plan = fixed(number, "new_picture", change=note)
    fake_model.respond(
        "choose_broll_picture" if number == 2 else "choose_starting_picture",
        BROLL_CHOICE if number == 2 else TALKING_CHOICE,
    )
    return plan


def new_picture(fake_model: FakeModel, steps: HeldSteps, number: int, note: str = HIGHER) -> None:
    """Make scene `number`'s starting picture again, as the shop owner's note asks, through a
    plan they said yes to, and the clip code makes from it: the new ad is assembled."""
    a_new_picture_said_yes_to(fake_model, number, note)
    steps.run_held()


def ads() -> list[ProducedItem]:
    return list(Job.objects.get().produced.filter(kind="finished_ad").order_by("version"))


def assemble(fake_model: FakeModel, say: Callable[..., None]) -> str:
    """Have the producer assemble the ad, asked in the chat, and give what it was told."""
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="On it."))
    say("Assemble the ad, please.")
    return results_of("assemble_ad")[-1]


def held_kinds(steps: HeldSteps) -> list[str]:
    return [SceneStep.objects.get(pk=held).kind for held in steps.held]


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
    a_new_picture_said_yes_to(fake_model, number)

    steps.run_next()

    assert status(number) == "planned"
    assert {status(other) for other in (1, 2, 3) if other != number} == {"finished"}
    step = picture_step(number)
    # Code queued the clip from the new picture by itself, and the ad waits for it.
    assert held_kinds(steps) == ["clip"]
    assert assemble(fake_model, say) == (
        f"Refused: scene {number}'s clip is still being made. You'll be told when it's ready; "
        "assemble the ad then. Nothing was done."
    )

    steps.run_held()

    remade = clip(number)
    assert remade.version == 2
    assert remade.picture == picture(number) == step.produced.get()
    assert status(number) == "finished"
    # Every scene of the plan landed, so code assembled the new ad: the producer's own
    # assemble_ad finds it already made.
    assert assemble(fake_model, say).startswith("The ad was already assembled")
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
    a_new_picture_said_yes_to(fake_model, 2)
    fake_model.respond("make_starting_picture", *[OutsideServiceDown("503 from OpenAI")] * 3)
    steps.run_held()

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"
    assert clip(2) == old
    assert assemble(fake_model, say).startswith("The ad was already assembled")
    assert ads() == [assembled]


def test_the_same_words_in_a_later_round_with_the_ad_unchanged_are_read_afresh(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    # "Go on" was said once the first ad was made. The new picture couldn't be made, so the
    # decider is handed the same ad and the same words again in this round: its earlier
    # round's turn isn't taken again (its tool calls are kept already), it is asked anew.
    a_new_picture_said_yes_to(fake_model, 2)
    fake_model.respond("make_starting_picture", *[OutsideServiceDown("503 from OpenAI")] * 3)
    steps.run_held()
    decided = ModelCall.objects.filter(purpose="decide_fixes").count()

    calling(fake_model, say, ("assemble_ad", {}))

    assert ModelCall.objects.filter(purpose="decide_fixes").count() == decided + 1
    assert results_of("assemble_ad")[-1].startswith("The ad was already assembled")
    assert ads() == [assembled]


def test_a_picture_that_keeps_failing_its_check_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, assembled: ProducedItem
) -> None:
    old = clip(2)
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    fixed(2, "new_picture", change=HIGHER)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)
    steps.run_held()

    assert picture_step(2).status == "stopped"
    assert status(2) == "finished"
    assert clip(2) == old


def test_two_new_pictures_in_a_row_each_get_their_clip_and_the_scene_ends_on_the_latest(
    fake_model: FakeModel, steps: HeldSteps, assembled: ProducedItem
) -> None:
    a_new_picture_said_yes_to(fake_model, 2, HIGHER)
    # The second is said yes to while the first picture is still being made.
    a_new_picture_said_yes_to(fake_model, 2, "Pour the tea slower.")
    assert held_kinds(steps) == ["starting_picture"]

    steps.run_next()

    # The first picture's clip is made before the second picture is started.
    assert held_kinds(steps) == ["clip"]
    assert SceneStep.objects.get(pk=steps.held[0]).picture == picture(2)
    steps.run_held()

    assert picture(2).version == 3
    assert clip(2).picture == picture(2)
    assert status(2) == "finished"


def test_no_new_picture_is_started_while_the_scenes_clip_is_being_made(
    fake_model: FakeModel, steps: HeldSteps, assembled: ProducedItem
) -> None:
    # So a clip of an older picture can never finish the scene after a newer picture.
    a_new_picture_said_yes_to(fake_model, 2)
    steps.run_next()
    assert held_kinds(steps) == ["clip"]

    a_new_picture_said_yes_to(fake_model, 2, "Pour slower.")

    assert held_kinds(steps) == ["clip"]
    assert picture(2).version == 2
    steps.run_next()
    assert status(2) == "finished"
    assert clip(2).picture == picture(2)
    assert picture(2).version == 2
    # Once the clip is made, the newer picture is started, and its own clip after it.
    assert held_kinds(steps) == ["starting_picture"]
    steps.run_held()
    assert picture(2).version == 3
    assert clip(2).picture == picture(2)
    assert status(2) == "finished"


def test_a_worker_stopping_as_the_picture_is_kept_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, assembled: ProducedItem
) -> None:
    a_new_picture_said_yes_to(fake_model, 2)

    with (
        the_worker_stops(post_save, ProducedItem, when=lambda item: item.version == 2),
        pytest.raises(WorkerStopped),
    ):
        steps.run_next()
    tasks.carry_on_after_the_worker_starts()

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"
    assert steps.held == []


def test_a_worker_stopping_before_the_picture_step_finishes_leaves_the_scene_finished(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], assembled: ProducedItem
) -> None:
    # The scene is planned again only as the step finishes, so a picture kept on a step that
    # then failed changes nothing, as if it couldn't be made.
    old = clip(2)
    a_new_picture_said_yes_to(fake_model, 2)

    with (
        the_worker_stops(post_save, SceneStep, when=lambda step: step.status == "finished"),
        pytest.raises(WorkerStopped),
    ):
        steps.run_next()
    tasks.carry_on_after_the_worker_starts()

    assert picture_step(2).status == "failed"
    assert status(2) == "finished"
    assert clip(2) == old
    assert steps.held == []
    assert assemble(fake_model, say).startswith("The ad was already assembled")


def test_a_scene_already_planned_again_isnt_said_to_be_no_longer_finished(
    fake_model: FakeModel, steps: HeldSteps, assembled: ProducedItem
) -> None:
    # As when its line was changed and changed back before the new picture.
    Scene.objects.filter(number=2).update(status="planned")
    a_new_picture_said_yes_to(fake_model, 2)

    steps.run_next()

    assert status(2) == "planned"
    assert "no longer finished" not in picture_step(2).result


def test_a_scenes_first_picture_leaves_it_planned_and_says_nothing_of_unfinishing(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    picture_of(fake_model, steps, say, 2)

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
    monkeypatch.setattr(SceneStep, "cost_usd", lambda _: Decimal(0))
    picture_of(fake_model, steps, say, 1)

    assert " It cost $0.00." in picture_step(1).result
