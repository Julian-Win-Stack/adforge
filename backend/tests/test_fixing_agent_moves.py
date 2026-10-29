"""The fixing agent's moves beside film_again and give_up (#236, spec #210 point 23):
change_action films another selling action the agent writes itself, line and shows
included, fact checked and voiced first; end_result_only films the end state with no
action. A move code refuses comes back as the tool's answer with its reason, costing no
clip and no try. Talking scenes are never the agent's. The agent, the fact check, the voice,
the judge, the pictures and the clips are faked at the gateway."""

import json
from typing import Any

import pytest

from agents import fixing_agent
from gateway.fake import FakeModel, turn
from jobs.models import AgentFix, ProducedItem, Scene, SceneStep

from .conftest import WorkerStopped, facts_ok, handoffs
from .test_fixing_agent import (
    STIRS,
    failed_clip,  # noqa: F401 (a fixture)
    film_again,
    fixed,
    scene_row,
    told,
)
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import LINE_2, SHOWS, checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

NEW_LINE = "Pour it, stir it, sip it: it holds 350 ml."
CAUSE = "The model can't film the pour: the tea floats above the mug."


def change_action(**changes: Any) -> tuple[str, dict[str, Any]]:
    """The agent's change_action call: scene 2 with a new action and a new line."""
    name, arguments = film_again(
        **{
            "root_cause": CAUSE,
            "move": "Film the stir instead of the pour: it fills the clip by its own course.",
            "line": NEW_LINE,
            "shows": STIRS,
            "video_prompt": STIRS,
            **changes,
        }
    )
    return "change_action", arguments


def tries() -> list[Any]:
    return list(AgentFix.objects.get().tries.order_by("number"))


# --- change_action -------------------------------------------------------------------------


def test_change_action_fact_checks_voices_and_films_the_new_scene(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[change_action()]))
    checks = len(handoffs("fact_check"))

    outcome = fixed(failed_clip)

    # The fact check ran on the new line, and the line was voiced.
    [check] = handoffs("fact_check")[checks:]
    assert NEW_LINE in json.dumps(check)
    assert fake_model.spoken[-1] == NEW_LINE
    # The scene row is the new scene, filmed: the try's clip speaks the new line.
    assert scene_row()[:2] == (NEW_LINE, STIRS)
    [made] = tries()
    assert made.clip is not None and outcome.passed == made.clip
    assert made.clip.made_from.step.line == NEW_LINE
    # The try records the move and the agent's root cause.
    assert (made.tool_call.tool, made.root_cause) == ("change_action", CAUSE)
    assert outcome.tries[0].tool == "change_action"
    # Like film_again, it answers with the judge's verdict and the clip's frames.
    [answer] = told()
    assert "The judge's verdict: pass." in answer


def test_a_worker_stopped_mid_change_action_carries_on_with_the_new_action(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[change_action()]))
    fake_model.respond("check_clip_motion", WorkerStopped())
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

    outcome = fixed(failed_clip)

    [made] = outcome.tries
    assert outcome.passed is not None and outcome.passed == made.clip
    assert "The judge's verdict: pass." in told()[0]
    assert scene_row()[:2] == (NEW_LINE, STIRS)


def test_a_worker_stopped_after_a_failed_change_action_was_judged_is_told_its_try(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The worker stops once the try is filmed and judged, before the agent is told of it.
    telling = fixing_agent._told

    def stops(*args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(fixing_agent, "_told", telling)
        raise WorkerStopped

    monkeypatch.setattr(fixing_agent, "_told", stops)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[change_action()]),
        turn(calls=[("give_up", {"reason": "It can't be filmed."})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

    outcome = fixed(failed_clip)

    # The restarted call is told of its own try, never refused for the action it filmed.
    [made] = outcome.tries
    assert made.tool == "change_action" and made.clip is not None
    assert told()[0].startswith("Try 1.")


WRONG = {
    "decision": "checked",
    "reason": "One claim isn't on the page.",
    "question": None,
    "lines": [
        {
            "scene": 2,
            "verdict": "wrong",
            "wrong": "line",
            "problem": "The page never says how much it holds.",
            "page_says": "A stoneware mug.",
        }
    ],
}


def test_a_new_line_the_fact_check_fails_is_the_tools_answer_and_costs_no_clip_or_try(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", WRONG, facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[change_action()]),
        turn(calls=[change_action(line="Stir it, sip it: stoneware made to last.")]),
    )
    clips = ProducedItem.objects.filter(kind="clip").count()

    outcome = fixed(failed_clip)

    refused, filmed = told()
    assert refused.startswith(
        "Refused: the fact check failed it: The page never says how much it holds. The page "
        "says: A stoneware mug."
    )
    # No clip and no try for the refusal: the one try is the second call's, one clip made.
    assert [(each.number, each.tool) for each in outcome.tries] == [(1, "change_action")]
    assert ProducedItem.objects.filter(kind="clip").count() == clips + 1
    # The agent's next turn saw the refusal as the tool's answer.
    second = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"][1]
    assert refused in [getattr(each, "result", None) for each in second.handoff.conversation]


def test_change_action_keeping_the_action_last_filmed_is_refused(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[change_action(shows=SHOWS, line=LINE_2)]),
        turn(calls=[change_action()]),
        # The action the try filmed is now the one to change.
        turn(calls=[change_action(line=LINE_2)]),
        turn(calls=[("give_up", {"reason": "It can't be filmed."})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))

    outcome = fixed(failed_clip)

    first, _, third, _ = told()
    assert first.startswith(f"Refused: {fixing_agent.SAME_ACTION}")
    assert third.startswith(f"Refused: {fixing_agent.SAME_ACTION}")
    assert [each.number for each in outcome.tries] == [1]


# --- end_result_only -----------------------------------------------------------------------

FULL = "The mug stands full of hot tea, steam rising from it."


def test_end_result_only_films_the_end_state_and_records_the_move(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(
            calls=[
                (
                    "end_result_only",
                    film_again(root_cause=CAUSE, shows=FULL, video_prompt=FULL)[1],
                )
            ]
        ),
    )

    outcome = fixed(failed_clip)

    [made] = tries()
    assert made.clip is not None and outcome.passed == made.clip
    assert (outcome.tries[0].tool, made.root_cause) == ("end_result_only", CAUSE)
    assert scene_row()[1] == FULL
    # Code adds its fixed line before the agent's own words, as the clip was sent them.
    assert f"{fixing_agent.END_RESULT_ONLY} {FULL}" in made.video_prompt
    assert f"{fixing_agent.END_RESULT_ONLY} {FULL}" in made.step.motion_prompt


# --- Talking scenes are never the agent's ---------------------------------------------------


@pytest.mark.parametrize("tool", ["film_again", "change_action", "end_result_only"])
def test_no_move_makes_the_scene_a_talking_one(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
    tool: str,
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[(tool, film_again(shows=" ")[1])]),
        turn(calls=[film_again()]),
    )

    fixed(failed_clip)

    refused, _ = told()
    assert refused.startswith(f"Refused: {fixing_agent.NO_TALKING}")
    assert [each.number for each in tries()] == [1]


def test_a_talking_scene_is_never_handed_to_the_agent(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    Scene.objects.filter(number=2).update(shows="")

    with pytest.raises(ValueError, match="talking"):
        fixed(failed_clip)

    assert not AgentFix.objects.exists()
    assert not [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
