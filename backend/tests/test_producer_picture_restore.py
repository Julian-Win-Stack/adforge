"""Going back to an earlier starting picture (#143, fix redesign decision 8): a plan going
back to a scene's earlier version, said yes to, brings its picture back as the scene's newest
version, and its clip too while it still says the line, with nothing paid again (Julian
2026-10-11, M1 of the #132 review: "same inputs, never pay twice"). A version whose clip was
never made gets one made from its picture. Code goes back once the scene's picture or clip
isn't being made. The ad is first made through the chat; the scene models are faked at the
gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest
from django.db import transaction
from django.db.models.signals import pre_save

from agents import run_plan
from agents.models import SystemNote
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from jobs import fix_plans, work
from jobs.models import FixPlan, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, paid_for, results_of
from .fixing import fixed, owner_says, plan_row, planned, say_yes
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_producer_broll import TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    HIGHER,
    a_new_picture_said_yes_to,
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
    new_picture,
    picture,
    status,
)
from .test_producer_restarts import WorkerStopped, the_worker_stops

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

SLOWER = "Pour the tea slower."
BACK = "The higher one was better."
# Spec #210 point 19: a scene that shows the product has no starting picture to bring back, so
# these run on talking scene 3.
SCENE = 3


def version(kind: str, number: int) -> ProducedItem:
    """Scene 3's `kind` at version `number`."""
    return ProducedItem.objects.get(kind=kind, scene__number=SCENE, version=number)


def versions(kind: str) -> list[int]:
    return list(
        ProducedItem.objects.filter(kind=kind, scene__number=SCENE)
        .order_by("version")
        .values_list("version", flat=True)
    )


def labels() -> list[str]:
    return [each.label for each in work.versions(Scene.objects.get(number=SCENE))]


def back_to(number: int) -> FixPlan:
    """The shop owner said yes to a plan taking scene 3 back to its version `number`."""
    return fixed(SCENE, "go_back", change=BACK, go_back_to=number)


@pytest.fixture
def higher_then_slower(
    fake_model: FakeModel,
    steps: HeldSteps,
    assembled: ProducedItem,  # noqa: F811
) -> None:
    """Scene 3 got a picture for "hold it higher" and its clip, then a picture for "pour the
    tea slower" and its clip, each through a plan the shop owner said yes to: pictures and
    clips 2 and 3."""
    new_picture(fake_model, steps, SCENE, HIGHER)
    new_picture(fake_model, steps, SCENE, SLOWER)
    assert (versions("starting_picture"), versions("clip")) == ([1, 2, 3], [1, 2, 3])
    assert status(SCENE) == "finished"


def test_an_earlier_version_brings_back_its_picture_and_clip_without_paying_again(
    steps: HeldSteps, higher_then_slower: None
) -> None:
    paid, ads_before = paid_for(), len(ads())
    earlier = version("starting_picture", 2)

    back_to(2)

    assert paid_for() == paid
    assert steps.held == []
    back = picture(SCENE)
    assert back.version == 4
    assert (back.file, back.step) == (earlier.file, earlier.step)
    assert versions("clip") == [1, 2, 3, 4]
    assert (clip(SCENE).file, clip(SCENE).step) == (
        version("clip", 2).file,
        version("clip", 2).step,
    )
    assert clip(SCENE).picture == back
    assert status(SCENE) == "finished"
    assert labels()[-1] == "v4 back to v2"
    # The chat shows both again, so the scene's card shows what the ad now uses.
    assert Attachment.objects.filter(file=back.file, scene=SCENE).count() == 2
    assert Attachment.objects.filter(file=clip(SCENE).file, scene=SCENE).count() == 2
    assert list(SystemNote.objects.filter(kind="fixes_started").values_list("text", flat=True))[
        -1
    ] == ("The shop owner said yes to the plan: Scene 3 went back to version 2, for nothing.")
    # Every scene of the plan landed at once, so code assembled the new ad with it.
    assert len(ads()) == ads_before + 1
    scene_3 = next(cut for cut in ads()[-1].cuts if cut["scene"] == SCENE)
    assert scene_3["clip"] == clip(SCENE).pk


def test_a_brought_back_clip_counts_as_landed_when_another_scenes_fix_lands(
    fake_model: FakeModel, steps: HeldSteps, higher_then_slower: None
) -> None:
    before = len(ads())

    say_yes(
        planned(
            plan_row(SCENE, "go_back", change=BACK, go_back_to=2),
            plan_row(1, "new_picture", change="Smile more."),
        )
    )
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    # Scene 1 is still being made, so the ad waits for it.
    assert len(ads()) == before
    steps.run_held()

    # Scene 3 went back by bringing its clip back, so the ad doesn't wait for it.
    assert len(ads()) == before + 1
    scene_3 = next(cut for cut in ads()[-1].cuts if cut["scene"] == SCENE)
    assert scene_3["clip"] == clip(SCENE).pk
    assert clip(SCENE).file == version("clip", 2).file


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_brought_back_picture_whose_clip_was_never_made_gets_a_new_clip_from_it(
    fake_model: FakeModel,
    steps: HeldSteps,
    assembled: ProducedItem,  # noqa: F811
) -> None:
    # The higher picture fails its fix check, so its clip isn't made; a newer plan takes the
    # scene on to the slower picture and its clip.
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    a_new_picture_said_yes_to(fake_model, SCENE, HIGHER)
    steps.run_held()
    assert versions("clip") == [1]
    new_picture(fake_model, steps, SCENE, SLOWER)
    assert labels() == ["v1 first version", f"v2 {HIGHER}", f"v3 {SLOWER}"]
    paid = paid_for()

    plan = back_to(2)

    # Version 2 has no clip, so going back makes one from its picture, as the plan said.
    row = plan.scenes.get()
    assert row.remakes_clip and row.estimate_usd > 0
    assert picture(SCENE).file == version("starting_picture", 2).file
    assert status(SCENE) == "planned"
    steps.run_held()

    assert paid_for()[len(paid) :] == ["make_talking_clip", "collect_talking_clip"]
    made = clip(SCENE)
    assert made.version == 3
    assert made.picture == picture(SCENE)
    assert made.step is not None and made.step.plan_scene == row
    assert status(SCENE) == "finished"
    assert labels()[-1] == "v4 back to v2"
    # Every scene of the plan landed, so code assembled the ad with it.
    scene_3 = next(cut for cut in ads()[-1].cuts if cut["scene"] == SCENE)
    assert scene_3["clip"] == made.pk


def test_bringing_back_an_earlier_picture_still_needs_the_shop_owners_yes(
    steps: HeldSteps, higher_then_slower: None
) -> None:
    planned(plan_row(SCENE, "go_back", change=BACK, go_back_to=2))

    assert versions("starting_picture") == [1, 2, 3]
    assert steps.held == []
    assert status(SCENE) == "finished"


def test_the_producer_brings_back_a_picture_on_a_scene_with_a_clip_for_nothing(
    fake_model: FakeModel, say: Callable[..., None], higher_then_slower: None
) -> None:
    # Nothing locks once a scene has a clip (spec #247 point 5): the picture already made for
    # the line with no note is handed back, free, and the scene waits for a clip made from it.
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": SCENE, "note": None})]),
        turn(says="On it."),
    )
    say("Make scene 3's picture again")

    assert results_of("make_starting_picture")[-1].startswith(
        "Scene 3's starting picture was already made for this line with no note (version 1), "
        "so it is the scene's starting picture again, as version 4, and nothing was made or "
        "paid for again."
    )
    assert versions("starting_picture") == [1, 2, 3, 4]
    assert status(SCENE) == "planned"


def test_no_picture_is_brought_back_while_the_scenes_clip_is_being_made(
    fake_model: FakeModel,
    steps: HeldSteps,
    assembled: ProducedItem,  # noqa: F811
) -> None:
    # So a clip of a newer picture can never finish the scene after the earlier one is back.
    new_picture(fake_model, steps, SCENE, HIGHER)
    a_new_picture_said_yes_to(fake_model, SCENE, SLOWER)
    steps.run_next()
    assert [SceneStep.objects.get(pk=held).kind for held in steps.held] == ["clip"]

    back_to(2)

    assert versions("starting_picture") == [1, 2, 3]
    steps.run_held()
    # Once the slower picture's clip was made, code went back on top of it.
    assert labels()[-1] == "v4 back to v2"
    assert clip(SCENE).file == version("clip", 2).file
    assert status(SCENE) == "finished"


def test_a_worker_stopping_as_a_picture_is_brought_back_leaves_one_new_version(
    steps: HeldSteps, higher_then_slower: None
) -> None:
    paid = paid_for()
    plan = planned(plan_row(SCENE, "go_back", change=BACK, go_back_to=2))
    # The picture is back, but the worker stops before the yes is done with, so the yes and
    # its run are undone together, as the chat routes them (agents/routing.py).
    run_kept = the_worker_stops(
        pre_save, SystemNote, when=lambda note: note.kind == "fixes_started"
    )
    with run_kept, pytest.raises(WorkerStopped), transaction.atomic():
        fix_plans.settle(plan, FixPlan.State.CONFIRMED, by=owner_says("yes"))
        run_plan.run_after_yes(plan)
    assert versions("starting_picture") == [1, 2, 3]

    say_yes(FixPlan.objects.get(pk=plan.pk))

    assert versions("starting_picture") == [1, 2, 3, 4]
    assert versions("clip") == [1, 2, 3, 4]
    assert paid_for() == paid
    assert status(SCENE) == "finished"


def test_no_picture_is_brought_back_while_a_newer_one_is_being_made(
    fake_model: FakeModel, steps: HeldSteps, higher_then_slower: None
) -> None:
    # The newer picture would land on top of it, so the ad wouldn't use what the owner was told.
    a_new_picture_said_yes_to(fake_model, SCENE, "Make it brighter.")
    assert len(steps.held) == 1

    back_to(2)

    assert versions("starting_picture") == [1, 2, 3]
    steps.run_held()
    # Code went back once the brighter picture and its clip were made.
    assert labels()[-2:] == ["v4 Make it brighter.", "v5 back to v2"]
    assert picture(SCENE).file == version("starting_picture", 2).file
    assert status(SCENE) == "finished"
