"""The try-another-way ladder (round 13 root cause 2): a scene agent changes the scene before it
gives up, through the chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ModelRequest

from .conftest import CLIP_FIXED, CLIP_MOTION_OK, HeldSteps, facts_ok, paid_for, results_of
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import TO_THE_LENS, change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
START = ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]})
DRAW = ("draw_starting_picture", PICTURE_FORM)
FAILED_PICTURE = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}


def test_the_ladder_is_in_the_scene_agents_instructions() -> None:
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_agent.lifecycle import LADDER_STEPS, NEVER_FILM_A_FAILED_PICTURE

    told = " ".join(INSTRUCTIONS.split())
    for step in LADDER_STEPS:
        assert " ".join(step.split()) in told
    assert "The producer's instruction is a starting point." in told
    assert "change it only once a picture or clip of it has failed" in told
    assert NEVER_FILM_A_FAILED_PICTURE.split(":")[0] in told
    assert "Ask the owner for a photo" not in told


def test_a_broll_scene_changed_after_six_failed_pictures_gets_three_new_tries(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: FAILED_PICTURE)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[DRAW]) for _ in range(7)],
        # The kept line is shown once, then the change is confirmed.
        turn(calls=[change()]),
        turn(calls=[change()]),
        *[turn(calls=[DRAW]) for _ in range(4)],
        turn(calls=[("give_up", {"reason": "Every picture of the new action failed too."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    assert paid_for().count("draw_scene_picture") == 9
    drawn = results_of("draw_starting_picture")
    assert "All 6 starting-picture tries have been used" in drawn[6]
    assert "change the scene with the ladder" in drawn[6]
    assert "The scene row changed" in results_of("change_scene")[-1]
    assert "starting pictures 7 of 9" in drawn[7]
    assert "All 9 starting-picture tries have been used" in drawn[-1]
    # The picture steps came once, at the run's first failed picture.
    assert sum("frame or angle the shot" in each for each in drawn) == 1


def test_a_broll_scene_switched_to_talking_after_six_clips_gets_two_clips_of_its_own(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", *[failing(FLOATS) for _ in range(8)])
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[call]) for _ in range(6) for call in (DRAW, ("film", FILM))],
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[TO_THE_LENS]),
        *[turn(calls=[call]) for _ in range(2) for call in (DRAW, ("film", FILM))],
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    filmed = results_of("film")
    assert paid_for().count("make_broll_clip") == 8
    # Six B-roll clips leave the run open for the talking step; a seventh is refused.
    assert "This run's six clips are used" in filmed[5]
    assert "This run's six clips are used" in filmed[6]
    assert "The scene row changed" in results_of("change_scene")[-1]
    assert "clips 7 of 8" in filmed[7]
    assert "clips 8 of 8" in filmed[8]
    assert "This run's six clips are used" not in filmed[8]
    from jobs.models import AgentFix

    assert AgentFix.objects.get().ended_by_cap


@pytest.mark.parametrize("owner_change", [False, True], ids=["producer-re-plan", "owner-change"])
def test_the_fix_check_fails_a_try_only_for_a_change_the_owner_asked_for(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    owner_change: bool,
) -> None:
    def motion(request: ModelRequest[Any]) -> dict[str, Any]:
        if "owner_change" not in request.output.model_fields:
            return CLIP_MOTION_OK
        return {
            **CLIP_MOTION_OK,
            "owner_change": {
                **CLIP_FIXED,
                "verdict": "not fixed",
                "still_wrong": "The hand still hovers.",
            },
        }

    def note_during_render() -> dict[str, Any]:
        note = {
            "scene": 2,
            "instruction": "Finish the scene with a simpler pour.",
            "quotes": ["finish with what you have"],
            "owner_change": owner_change,
        }
        fake_model.respond(
            "produce",
            turn(calls=[("pass_note", {"scenes": [note]})]),
            turn(says="I passed it on."),
        )
        say("finish with what you have")
        return {"state": "working"}

    fake_model.answer_unscripted("check_clip_motion", motion)
    fake_model.respond("collect_clip", note_during_render)
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The change isn't made."})]),
    )
    calling(fake_model, say, START)
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    last = results_of("film")[-1]
    assert ("Whether the owner's change is made: no." in last) is owner_change
    assert ("It fails." if owner_change else "It passes.") in last


def test_a_scene_switched_to_talking_films_its_closest_picture_past_its_broll_pictures(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from gateway.types import Turn
    from jobs.models import ProducedItem

    fake_model.respond("check_scene_picture", PICTURE_PASSES, *([FAILED_PICTURE] * 5))
    fake_model.respond("fact_check", facts_ok(2))

    def closest() -> Turn:
        picture = ProducedItem.objects.filter(kind="starting_picture", scene__number=2).latest(
            "version"
        )
        return turn(calls=[("film", {**FILM, "closest_picture": picture.pk, "reason": "Closest."})])

    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[TO_THE_LENS]),
        *[turn(calls=[DRAW]) for _ in range(5)],
        closest,
        turn(calls=[("give_up", {"reason": "The clip should have passed."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    # The passed B-roll picture no longer fits the talking scene, so it doesn't block filming.
    assert paid_for().count("make_broll_clip") == 1, results_of("film")


def test_switching_kind_back_and_forth_adds_one_talking_bonus_and_pictures_stay_bounded(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_agent import lifecycle
    from jobs.models import Scene, SceneAgentRun

    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Stop here."})]))
    calling(fake_model, say, START)
    run(fake_model, steps)
    window = SceneAgentRun.objects.get()
    scene = Scene.objects.get(number=2)
    for clips, pictures in ((2, 6), (5, 9), (6, 12)):
        SceneAgentRun.objects.filter(pk=window.pk).update(
            clips=clips, starting_pictures=pictures, end_pictures=pictures
        )
        scene.kind = Scene.Kind.TALKING
        lifecycle.changed_by_the_ladder(window, scene, was_broll=True)
        scene.kind = Scene.Kind.BROLL
        lifecycle.changed_by_the_ladder(window, scene, was_broll=False)
    window.refresh_from_db()
    assert window.talking_switch_at == 2
    assert lifecycle.clip_limit(window) == 6
    assert (window.starting_picture_limit, window.end_picture_limit) == (12, 12)


def test_a_broll_scene_whose_six_renders_fail_stays_open_for_the_talking_step(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from gateway.types import ClipFailed
    from jobs.models import AgentFix

    fake_model.respond("make_clip", *[ClipFailed("Creatify rejected the render")] * 6)
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[call]) for _ in range(6) for call in (DRAW, ("film", FILM))],
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "No talking version fits the plan."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    assert "This run's six clips are used" in results_of("film")[-1]
    assert not AgentFix.objects.get().ended_by_cap
