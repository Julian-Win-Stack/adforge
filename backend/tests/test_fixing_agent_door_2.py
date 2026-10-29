"""Door 2 of the fixing agent, through the chat (fixing agent build, T3; spec section 5): a
shop owner's note on a B-roll scene is planned by the decider as one fix with its goal, the
plan reads as the goal, and after the yes the scene is handed to the fixing agent (scripted
here, tests/fake_fixing_agent.py). A try that passes lands the row and the new ad is
assembled; a give-up keeps the scene as it was and shows the shop owner the tries."""

from collections.abc import Callable

import pytest
from django.db.models import Sum
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import fixing_agent, tasks
from agents.models import SystemNote
from agents.money import dollars
from agents.producer import _latest_clip
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.clip_judge import ASKS
from jobs.models import AgentFix, Fix, FixCheck, FixPlanScene, Scene, SceneStep

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    HeldSteps,
    WorkerStopped,
    facts_ok,
    motion_answer,
)
from .fake_fixing_agent import FakeAgent, ScriptedTry, install
from .test_fix_wiring import decided, planning, plans, row, tap_yes
from .test_fixing_agent import STIRS, film_again
from .test_pick_from_tries import tap_use
from .test_producer_auto_fix import FLOATS, failing
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
    assert (handed["door"], handed["goal"]) == ("owner", GOAL)
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
    latest = _latest_clip(scene)
    assert latest is not None and latest.file == before.file
    assert scene.status == Scene.Status.FINISHED
    assert [made.change for made in work.clip_tries(step)] == ["try 1", "try 2"]
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


def test_after_the_agent_gave_up_the_shop_owner_can_tap_a_try_to_use_it(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(
        monkeypatch,
        FakeAgent(tries=[ScriptedTry(judge="fail"), ScriptedTry(judge="fail")], gave_up="No."),
    )
    _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    [step] = SceneStep.objects.filter(pk__in=held.held)
    held.run_held()
    try_1, _ = work.clip_tries(step)
    assert work.tries_shown(step).on_offer

    sent = tap_use(api, session_id, try_1)

    assert sent.status_code == 201, sent.json()
    latest = _latest_clip(Scene.objects.get(number=2))
    assert latest is not None and latest.file == try_1.file


# --- The real agent, on scripted turns -----------------------------------------------------

NEW_LINE = "Lift it, sip it: it holds 350 ml."


def test_through_the_chat_the_agent_films_the_owners_change_until_a_try_passes(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    ads_before = len(ads())
    # Try 1: the judge finds the change not made; try 2 has it.
    not_made = {
        "old_shows": "The mug stays on the table.",
        "new_shows": "The mug stays on the table.",
        "verdict": "not fixed",
        "still_wrong": "The mug never leaves the table.",
    }
    answers = iter([not_made] * ASKS)
    fake_model.answer_unscripted(
        "check_clip_motion",
        lambda request: motion_answer(CLIP_MOTION_OK, next(answers, CLIP_FIXED))(request),
    )
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=STIRS)]), turn(calls=[film_again()])
    )
    planned = _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    held.run_held()

    fixing = AgentFix.objects.get()
    assert [each.fix_check["verdict"] for each in fixing.tries.order_by("number")] == [  # type: ignore[index]
        "failed",
        "passed",
    ]
    assert fixing.passed is not None and clip(2).file == fixing.passed.file
    planned.refresh_from_db()
    assert planned.landed_at is not None and planned.error == ""
    assert len(ads()) == ads_before + 1
    # The row's cost counts the agent's turns and tries.
    agent = ModelCall.objects.filter(tool_call__agent_fix=fixing).aggregate(cost=Sum("cost_usd"))
    assert planned.cost_usd() >= agent["cost"] > 0
    assert SystemNote.objects.filter(text__contains=f"It cost {dollars(planned.cost_usd())}.")


def test_a_fix_the_cap_ended_keeps_the_scene_as_it_was_and_says_the_cap_ended_it(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Door 2 keeps the clip the scene had before the fix, as before the cap went silent
    # (#217: not settled for door 2, kept as it was).
    monkeypatch.setattr(fixing_agent, "MOST_TRIES", 2)
    # Every try: the judge finds the owner's change not made.
    fake_model.answer_unscripted(
        "check_clip_motion",
        motion_answer(
            CLIP_MOTION_OK,
            {
                "old_shows": "The mug stays on the table.",
                "new_shows": "The mug stays on the table.",
                "verdict": "not fixed",
                "still_wrong": "The mug never leaves the table.",
            },
        ),
    )
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=STIRS)]), turn(calls=[film_again()])
    )
    before = clip(2)

    _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    [step] = SceneStep.objects.filter(pk__in=held.held)
    held.run_held()

    fixing = AgentFix.objects.get()
    assert (fixing.ended_by_cap, fixing.tries.count()) == (True, 2)
    assert clip(2).file == before.file
    told = Message.objects.filter(tries_of=step).get()
    assert told.text == (
        "Scene 2 stays as it was: The fixing agent filmed as many tries as it may at this "
        "scene, and none of them passed. Watch the tries, and tap one to use it instead."
    )


def test_an_agent_that_stops_after_a_try_leaves_the_scene_as_it_was(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    before = clip(2)
    line = Scene.objects.get(number=2).line
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_clip_motion", motion_answer(failing(FLOATS)))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, video_prompt=STIRS)]),
        *[OutsideServiceDown("503 from the model")] * 6,
    )
    planned = _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    held.run_held()

    # Try 1 changed the line and the judge failed it; then the agent stopped. The scene is
    # put back, its clip from before is the one in the ad, and the row ends with the error.
    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.status) == (line, Scene.Status.FINISHED)
    latest = _latest_clip(scene)
    assert latest is not None and latest.file == before.file
    assert AgentFix.objects.get().finished_at is not None
    planned.refresh_from_db()
    assert planned.landed_at is None and planned.error


def test_a_fix_the_worker_stopped_in_leaves_the_scene_as_it_was_once_it_restarts(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    before = clip(2)
    line = Scene.objects.get(number=2).line
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_clip_motion", motion_answer(failing(FLOATS)))
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(line=NEW_LINE, video_prompt=STIRS)]), WorkerStopped()
    )
    planned = _a_fix_said_yes_to(fake_model, held, say, api, session_id)
    with pytest.raises(WorkerStopped):
        held.run_held()

    tasks.carry_on_after_the_worker_starts()

    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.status) == (line, Scene.Status.FINISHED)
    latest = _latest_clip(scene)
    assert latest is not None and latest.file == before.file
    fixing = AgentFix.objects.get()
    assert (fixing.gave_up, fixing.kept) == (work.STOPPED_BY_A_RESTART, latest)
    planned.refresh_from_db()
    assert planned.landed_at is None and planned.error
