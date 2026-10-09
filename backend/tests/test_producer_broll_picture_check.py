"""A B-roll scene's starting picture is checked before any clip is paid for, driven through
the chat: side by side with the shop photo, and against the scene's prompts. One that fails
is drawn again from prompts rewritten to fix what the check found, at most twice (Julian,
2026-10-07: a made-up toilet and a bent bottle neck were only seen after the clip was paid
for). The producer's model and the scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge.retry import OutsideServiceDown
from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, SceneStep
from jobs.picture_check import STARTING_PICTURE_CHECK

from .conftest import PICTURE_OK, HeldSteps, WorkerStopped, handoffs, paid_for
from .test_producer_broll import (
    BROLL_CHOICE,
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    picture_of_scene_2,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

BENT_NECK = "The bottle's neck bends down; in the shop photo it rises straight from the top."

# A check that finds the product doesn't match its shop photo.
NOT_THE_PRODUCT: dict[str, Any] = {
    **PICTURE_OK,
    "matches_the_shop_photo": {"passes": False, "problem": BENT_NECK},
}

# The prompts written again to fix it.
FIXED_CHOICE: dict[str, Any] = {
    **BROLL_CHOICE,
    "prompt": "The mug upright on a workbench, its handle rising straight from its side.",
}


def pictures_drawn() -> list[str]:
    """The file of each starting picture paid for, oldest first."""
    calls = ModelCall.objects.filter(purpose="make_starting_picture").order_by("id")
    return [call.output["file"] for call in calls if call.output is not None]


def test_a_picture_that_passes_is_checked_once_beside_the_shop_photo(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    job = Job.objects.get()
    picture = step.produced.get()
    assert paid_for()[-3:] == [
        "choose_broll_picture",
        "make_starting_picture",
        "check_starting_picture",
    ]
    assert ModelCall.objects.get(purpose="check_starting_picture").images == [
        {"label": "The shop photo", "key": job.photos.get(position=1).file},
        {"label": "The starting picture", "key": picture.file},
    ]
    (check,) = handoffs("check_starting_picture")
    assert (check["line"], check["shows"], check["picture_prompt"], check["video_prompt"]) == (
        step.line,
        step.shows,
        BROLL_CHOICE["prompt"],
        BROLL_CHOICE["motion_prompt"],
    )


def test_a_picture_that_fails_is_drawn_again_from_prompts_written_to_fix_it(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_starting_picture", NOT_THE_PRODUCT, PICTURE_OK)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    first, second = handoffs("make_starting_picture")
    assert (first["prompt"], second["prompt"]) == (BROLL_CHOICE["prompt"], FIXED_CHOICE["prompt"])
    # The model writing the prompts again is told what was wrong, and shown the picture.
    _, rewrite = handoffs("choose_broll_picture")
    assert rewrite["redo"] == {
        "picture_prompt": BROLL_CHOICE["prompt"],
        "video_prompt": BROLL_CHOICE["motion_prompt"],
        "problems": [BENT_NECK],
    }
    first_picture = ModelCall.objects.filter(purpose="make_starting_picture").first()
    assert first_picture is not None and first_picture.output is not None
    rewritten = ModelCall.objects.filter(purpose="choose_broll_picture").last()
    assert rewritten is not None and rewritten.images[-1] == {
        "label": "The last picture",
        "key": first_picture.output["file"],
    }
    told_first, told_again = instructed["choose_broll_picture"]
    assert "failed its check" in told_again and "failed its check" not in told_first
    # The picture kept is the one that passed, with the prompts it was drawn from.
    assert step.produced.get().file != first_picture.output["file"]
    assert step.prompt == FIXED_CHOICE["prompt"]


def test_a_picture_that_keeps_failing_is_drawn_three_times_and_the_last_kept(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", *[NOT_THE_PRODUCT] * 3)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)

    drawn = pictures_drawn()
    assert len(drawn) == 3
    assert step.produced.get().file == drawn[-1]
    assert paid_for().count("check_starting_picture") == 3
    assert step.status == "finished"


def test_a_picture_step_run_again_after_the_worker_stopped_pays_for_no_picture_twice(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE)
    # The first picture fails its check; the worker stops as the second is checked.
    fake_model.respond("check_starting_picture", NOT_THE_PRODUCT, WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    fake_model.respond("check_starting_picture", PICTURE_OK)
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert paid_for().count("make_starting_picture") == 2
    assert paid_for().count("choose_broll_picture") == 2
    step = SceneStep.objects.get(pk=step_id)
    assert step.produced.get().file == pictures_drawn()[1]


def test_a_picture_whose_check_cant_be_read_is_kept_and_not_paid_for_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A check that fails without saying why can't be read, but the picture is paid for.
    fake_model.respond(
        "check_starting_picture",
        {**PICTURE_OK, "real_objects": {"passes": False, "problem": ""}},
    )

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert step.status == "finished"
    assert step.produced.get().file == pictures_drawn()[0]
    assert paid_for().count("make_starting_picture") == 1


def test_a_picture_whose_check_service_is_down_is_kept_and_not_paid_for_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", *[OutsideServiceDown("azure answered 503")] * 3)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert step.status == "finished"
    assert step.produced.get().file == pictures_drawn()[0]
    assert paid_for().count("make_starting_picture") == 1


def test_with_quality_checks_switched_off_a_picture_is_kept_unchecked(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], settings: Any
) -> None:
    settings.QUALITY_CHECKS = False

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert step.status == "finished"
    assert paid_for()[-2:] == ["choose_broll_picture", "make_starting_picture"]
    assert "check_starting_picture" not in paid_for()


def test_the_check_fails_a_before_that_doesnt_show_what_will_change() -> None:
    # Test ads (08 Oct), N3: the start picture was asked for clean tile, so before looked the
    # same as after, and the check passed it: it only checked the result wasn't there yet.
    assert (
        "not its result already there, and what the action will change, or the problem, is "
        "plainly visible."
    ) in STARTING_PICTURE_CHECK


def test_the_check_fails_a_picture_that_doesnt_make_sense_for_the_line() -> None:
    # Round 2 #12 s5: the drop started just above the floor, copied from the shop's drop
    # photo (low hand, shoe, knee-height camera), and the six checks passed it. Julian 22:25:
    # "We'll add another check".
    assert (
        '- makes_sense_for_the_line: the picture sets up what "shows" and "line" claim, as '
        "a real person would film it"
    ) in STARTING_PICTURE_CHECK
    assert "a standing person's hand height" in STARTING_PICTURE_CHECK
    assert "not copied from the shop photo" in STARTING_PICTURE_CHECK
    # Tried on Julian's graded pictures: it failed #5's PERFECT crossbody walk for framing
    # "past her knees", so framing and small differences from the prompt are not its job.
    assert (
        "Judge only what decides whether the clip can prove the line, such as where the action "
        'starts: framing, crop and small differences from "picture_prompt" never fail this '
        "check."
    ) in STARTING_PICTURE_CHECK


def test_a_picture_that_doesnt_make_sense_for_the_line_is_drawn_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    too_low = "The phone starts an inch above the floor; the page says it survives 6 ft drops."
    fake_model.respond(
        "check_starting_picture",
        {**PICTURE_OK, "makes_sense_for_the_line": {"passes": False, "problem": too_low}},
        PICTURE_OK,
    )

    picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    assert len(pictures_drawn()) == 2
    _, rewrite = handoffs("choose_broll_picture")
    assert rewrite["redo"]["problems"] == [too_low]
