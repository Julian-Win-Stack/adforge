"""Scenes that match each other, through the chat with outside models scripted: every scene
agent is told the plan's product photos and real size, a starting picture draws from one of
them, and a changed action that doesn't match the other scenes is refused, three times a
scene agent's run at most (Julian, Nov 8 2026)."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, turn
from jobs import notices
from jobs.models import Job, ProducedItem, Scene

from .conftest import HeldSteps, facts_ok, handoffs, paid_for, recorded
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import GIVE_UP, change, result_of

pytestmark = pytest.mark.django_db(transaction=True)


def started(fake_model: FakeModel, say: Callable[..., None], scene: int = 2) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Make it.", "quotes": ["go on"]}),
    )


def test_a_starting_picture_without_a_product_photo_is_refused_naming_them(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    Job.objects.update(product_photos=[1], real_size="8 fl oz bottle", real_size_estimated=True)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    only_the_side = {**PICTURE_FORM, "picks": [{"item": 2, "take": "Copy the handle."}]}
    beside_the_side = {
        **PICTURE_FORM,
        "picks": [{"item": 2, "take": "Copy the handle."}, {"item": 1, "take": "The glaze."}],
    }
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", only_the_side)]),
        turn(calls=[("draw_starting_picture", beside_the_side)]),
        GIVE_UP,
    )
    started(fake_model, say)
    run(fake_model, steps)

    intro = handoffs("scene_agent")[0]["conversation"][0]["text"]
    assert (
        "The product, the same in every scene of the ad, as the planner fixed it: its photos "
        "are library item 1. Draw the product from these photos: every starting picture picks "
        "at least one of them. Its real size: 8 fl oz bottle (an estimate from the photos). "
        "Draw it this size."
    ) in intro
    assert result_of(1).startswith(
        "Refused: Pick at least one of the product photos, library item 1:"
    )
    # Refused before anything was drawn, and without using a try; other photos go beside it.
    (made,) = ProducedItem.objects.filter(kind="starting_picture")
    assert made.sent is not None
    assert [photo.get("library_number") for photo in made.sent["photos"]][:2] == [2, 1]
    assert paid_for().count("draw_scene_picture") == 1


def mismatch(reason: str) -> dict[str, str]:
    return {"decision": "mismatch", "reason": reason}


NEW_LINE = "This mug holds three hundred and fifty millilitres of your favourite tea."


def test_a_drifting_change_is_refused_three_times_a_run_then_goes_through_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    planned = Scene.objects.get(number=2).shows
    actions = [f"A hand sets the mug on {place}." for place in ("a shelf", "a tray", "a desk")]
    last = "A hand sets the mug on a stool."
    fake_model.respond(
        "check_scene_cohesion",
        *(mismatch(f"It shows two mugs, scene {n} one.") for n in (1, 3, 1, 3)),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        *(turn(calls=[change(line=NEW_LINE, shows=action)]) for action in [*actions, last]),
        GIVE_UP,
    )
    started(fake_model, say)
    run(fake_model, steps)

    for number in (1, 2, 3):
        assert result_of(number).startswith(
            "Refused: the new action doesn't match the ad's other scenes: It shows two mugs"
        )
    assert result_of(4).startswith("The scene row changed")
    # A refusal changes nothing and isn't fact checked; the change let through is.
    checked_actions = [each["lines"][0]["shows"] for each in handoffs("fact_check")]
    assert last in checked_actions
    assert not set(actions) & set(checked_actions)
    asked = handoffs("check_scene_cohesion")
    assert [each["new_action"] for each in asked] == [*actions, last]
    assert [scene["number"] for scene in asked[0]["other_scenes"]] == [1, 3]
    assert asked[0]["product_photos"][0].startswith("1. ")
    assert asked[0]["real_size"] == "350 ml mug, about 10 cm tall"
    assert (
        "problem",
        "Scene 2's new action went through though the cohesion check found it doesn't match "
        "the other scenes, as this run's 3 cohesion refusals are used. New action: "
        f'"{last}". What didn\'t match: It shows two mugs, scene 3 one.',
    ) in recorded(api, session_id)
    # The give-up puts the planned action back.
    assert Scene.objects.get(number=2).shows == planned


def test_a_change_of_line_alone_is_not_checked_against_the_other_scenes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    shows = Scene.objects.get(number=2).shows
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=NEW_LINE, shows=shows, broll_kind="does a job")]),
        GIVE_UP,
    )
    started(fake_model, say)
    run(fake_model, steps)

    assert "The scene row changed" in result_of(1)
    assert not handoffs("check_scene_cohesion")


def test_a_cohesion_check_that_gives_no_answer_lets_the_change_through_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("check_scene_cohesion", *[OutsideServiceDown("Azure answered 503")] * 8)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=NEW_LINE, shows="A hand sets the mug on a shelf.")]),
        GIVE_UP,
    )
    started(fake_model, say)
    run(fake_model, steps)

    assert result_of(1).startswith("The scene row changed")
    assert (
        "problem",
        "The cohesion check didn't answer for scene 2's new action (OutsideServiceDown), so "
        "it goes through unchecked.",
    ) in recorded(api, session_id)


def test_a_starting_picture_is_drawn_without_product_photos_the_owner_excluded_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    Job.objects.update(product_photos=[1])
    Scene.objects.filter(number=2).update(excluded=[1])
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    side = {**PICTURE_FORM, "picks": [{"item": 2, "take": "Copy the handle."}]}
    fake_model.respond("scene_agent", turn(calls=[("draw_starting_picture", side)]), GIVE_UP)
    started(fake_model, say)
    run(fake_model, steps)

    assert ProducedItem.objects.filter(kind="starting_picture").count() == 1
    assert (
        "problem",
        "Scene 2's starting picture was drawn without the plan's product photos (1): the "
        "owner excluded them for this scene or the library no longer holds them.",
    ) in recorded(api, session_id)
    assert notices.NO_PRODUCT_PHOTO_TO_PICK.for_owner is False
