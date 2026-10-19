"""Door 2 of the fixing agent, through the chat (fixing agent build, T3; spec section 5): a
shop owner's note on a B-roll scene is planned by the decider as one fix with its goal, the
plan reads as the goal, and after the yes the scene is handed to the fixing agent (scripted
here, tests/fake_fixing_agent.py). A try that passes lands the row and the new ad is
assembled; a give-up keeps the scene as it was and shows the shop owner the tries."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents.models import SystemNote
from agents.producer import _latest_clip
from chat.models import Message
from gateway.fake import FakeModel
from jobs import work
from jobs.models import Fix, FixCheck, FixPlanScene, Scene, SceneStep

from .conftest import HeldSteps
from .fake_fixing_agent import FakeAgent, ScriptedTry, _ruling, install
from .test_fix_wiring import decided, planning, plans, row, tap_yes
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "assembled"),
]

GOAL = "The mug is lifted off the table and set down again."


@pytest.fixture
def held(steps: HeldSteps, fake_model: FakeModel) -> HeldSteps:
    return steps


def _a_fix_said_yes_to(
    fake_model: FakeModel, held: HeldSteps, say: Callable[..., None], api: APIClient, sid: str
) -> FixPlanScene:
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_picture", {"scene": 2}), decided(row(2, "fix", GOAL))),
    )
    say("In scene 2 she should lift the mug and put it down.")
    [plan] = plans()
    tap_yes(api, sid, plan)
    return plan.scenes.get()


def test_a_note_is_a_fix_whose_goal_the_agent_films_until_a_try_passes(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = install(
        monkeypatch,
        FakeAgent(tries=[ScriptedTry(judge="fail"), ScriptedTry(change_made=False), ScriptedTry()]),
    )
    before = clip(2)
    ads_before = len(ads())

    fake_model.respond(
        "decide_fixes",
        planning(("look_at_picture", {"scene": 2}), decided(row(2, "fix", GOAL))),
    )
    say("In scene 2 she should lift the mug and put it down.")

    # The plan reads as the goal, priced as one try of the agent, up to six.
    [plan] = plans()
    [planned] = plan.scenes.all()
    assert (planned.action, planned.change) == (FixPlanScene.Action.FIX, GOAL)
    assert planned.estimate_up_to_usd == 6 * planned.estimate_usd > 0
    shown = Message.objects.get(plan_event="shown", fix_plan=plan).text
    assert f"Scene 2: {GOAL}" in shown
    assert "new clip" not in shown and "new starting picture" not in shown
    assert "Tap Yes to go ahead, or type what to change." in shown
    assert held.held == []

    # The yes: one clip step, for the whole scene, hands the scene and the goal to the agent.
    tap_yes(api, session_id, plan)
    [step] = SceneStep.objects.filter(pk__in=held.held)
    assert (step.kind, step.note, step.fix.redo, step.plan_scene) == (  # type: ignore[union-attr]
        "clip",
        GOAL,
        Fix.Redo.SCENE,
        planned,
    )
    held.run_held()

    [handed] = agent.handed
    assert (handed["door"], handed["goal"], handed["failure"].goal) == ("owner", GOAL, GOAL)
    assert "lift the mug" in handed["failure"].note
    # The third try passed the judge and had the change: it is the scene's clip, in the ad.
    planned.refresh_from_db()
    assert planned.landed_at is not None and planned.error == ""
    assert clip(2).change == "try 3"
    assert len(ads()) == ads_before + 1
    assert clip(2).pk in [cut["clip"] for cut in ads()[-1].cuts]
    # The clip the owner complained about is still a version to go back to (the agent's
    # tries are listed as versions by the agent's own code, T1); the change was checked.
    assert work.versions(Scene.objects.get(number=2))[0].clip == before
    assert FixCheck.objects.get().verdict == "passed"


def test_a_try_the_judge_passed_without_the_owners_change_is_not_passed() -> None:
    passed_without_it = ScriptedTry(
        change_made=False, still_wrong="The mug never leaves the table."
    )
    before = clip(2)
    before.ruling = _ruling(passed_without_it, before)
    assert work.change_not_made(before) == "The mug never leaves the table."
    before.ruling = _ruling(ScriptedTry(), before)
    assert work.change_not_made(before) == ""
    # Judged without the clip it was compared with, the change can't fail a try.
    del before.ruling["compared_with"]
    assert work.change_not_made(before) == ""


def test_the_agent_giving_up_keeps_the_scene_as_it_was_and_shows_the_tries(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "The mug can't be lifted without the hand covering the logo."
    install(
        monkeypatch,
        FakeAgent(tries=[ScriptedTry(judge="fail"), ScriptedTry(judge="fail")], gave_up=reason),
    )
    before = clip(2)
    ads_before = len(ads())

    planned = _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    [step] = SceneStep.objects.filter(pk__in=held.held)
    held.run_held()

    planned.refresh_from_db()
    assert planned.landed_at is None and planned.error == reason
    # The scene's clip in the ad is still the one from before the plan; both tries are kept.
    scene = Scene.objects.get(number=2)
    assert _latest_clip(scene) == before
    assert scene.status == Scene.Status.FINISHED
    assert list(step.produced.values_list("change", flat=True)) == ["try 1", "try 2"]
    # One line in the chat says why, with the tries to pick from.
    told = Message.objects.filter(tries_of=step).get()
    assert told.text == (
        f"Scene 2 stays as it was: {reason} Watch the tries, and tap one to use it instead."
    )
    assert SystemNote.objects.filter(kind=SystemNote.Kind.ERROR, text__contains=reason).exists()
    # Nothing landed, so no new ad.
    assert len(ads()) == ads_before


def test_a_fix_of_a_talking_scene_is_a_cant_said_by_code(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    from agents import run_plan
    from jobs.models import Job

    job = Job.objects.get()
    talking = Scene.objects.get(number=3)
    plan = run_plan.check_and_propose(
        job,
        [{"seq": 1, "text": "She should hold it higher."}],
        [FixPlanScene(scene=talking, action=FixPlanScene.Action.FIX, change="Held higher.")],
    )
    [planned] = plan.scenes.all()
    assert "fixing agent only films scenes that show the product" in planned.cant
    assert planned.estimate_usd == 0
    assert not run_plan.runs_something(plan)
