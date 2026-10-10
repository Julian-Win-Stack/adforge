"""The ad's before-picture, driven through the chat: one picture of the place the B-roll
scenes happen in, before any of them starts, drawn once per ad, and every B-roll starting
picture drawn from it, so the place and what the scenes act on look the same in every scene
(round 3 #8 s3 FAIL: "a different toilet means the different dirt"). The producer's model and
the scene models are faked at the gateway."""

from collections.abc import Callable

import pytest

from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, Scene, SceneStep

from .conftest import (
    BEFORE_PICTURE_PLAN,
    HeldSteps,
    WorkerStopped,
    before_picture_file,
    handoffs,
    paid_for,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    TALKING_CHOICE,
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    picture_of_scene_2,
    run,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
# Every test starts from a chat whose ad is planned with scene 2 showing the mug, has its
# person, and whose lines passed the fact check.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

SHOWS_3 = "A hand wipes a coffee ring off the workbench beside the mug."


def scene_3_shows_the_mug_too() -> None:
    """Make scene 3 a B-roll scene too, doing a job on the same workbench."""
    Scene.objects.filter(number=3).update(
        shows=SHOWS_3,
        broll_kind="does a job",
        person_shown="no face",
        usage="Wiped with a damp cloth.",
        result="The workbench is clean.",
        needs=[],
    )


def pictures_of(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """Make the starting pictures of scenes 1, 2 and 3 at once, as the producer does."""
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in (1, 2, 3)],
    )
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    run(fake_model, steps)


def test_every_broll_starting_picture_is_drawn_from_one_before_picture(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    scene_3_shows_the_mug_too()

    pictures_of(fake_model, steps, say)

    # One before-picture for the ad, drawn as it was planned.
    assert [p for p in paid_for() if "before_picture" in p] == [
        "plan_before_picture",
        "make_before_picture",
    ]
    (drawn,) = handoffs("make_before_picture")
    assert drawn["prompt"] == BEFORE_PICTURE_PLAN["prompt"]
    # Both B-roll scenes' pictures are drawn from it, sent last; the talking scene's isn't.
    talking, *broll = handoffs("make_starting_picture")
    assert [asked["pictures"][-1] for asked in broll] == [before_picture_file()] * 2
    assert before_picture_file() not in talking["pictures"]


def test_the_before_picture_is_planned_from_every_broll_scene(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    scene_3_shows_the_mug_too()

    pictures_of(fake_model, steps, say)

    (planned,) = handoffs("plan_before_picture")
    # The B-roll scenes only: scene 1 is said to camera.
    assert [(scene["scene"], scene["shows"]) for scene in planned["broll_scenes"]] == [
        (2, Scene.objects.get(number=2).shows),
        (3, SHOWS_3),
    ]
    (told,) = instructed["plan_before_picture"]
    assert "No product, no person and no hand" in told


def test_the_model_writing_a_scenes_prompts_is_shown_the_before_picture_last(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = picture_of_scene_2(fake_model, steps, say)

    shown = ModelCall.objects.get(purpose="choose_broll_picture").images
    assert shown[-1] == {"label": "The before-picture", "key": before_picture_file()}
    (planned,) = handoffs("choose_broll_picture")
    assert planned["before_picture"] == BEFORE_PICTURE_PLAN["prompt"]
    assert planned["pictures"][-1] == {
        "image": 2,
        "job": "the place, its light and each thing in it, with every mark, exactly as they are",
    }
    (told,) = instructed["choose_broll_picture"]
    assert 'The last picture in "pictures" is the ad\'s before-picture' in told
    assert step.pictures_sent[-1] == {
        "image": 2,
        "before_picture": True,
        "job": "the place, its light and each thing in it, with every mark, exactly as they are",
    }


def test_a_before_picture_drawn_before_the_worker_stopped_is_not_paid_for_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    # The worker stops once the before-picture is drawn, as the scene's own is drawn.
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    fake_model.respond("make_starting_picture", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    Job.objects.get().produced.filter(kind="before_picture").delete()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert [p for p in paid_for() if "before_picture" in p] == [
        "plan_before_picture",
        "make_before_picture",
    ]
    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"][-1] == before_picture_file()
