"""The fixing agent (agents/fixing_agent.py): it owns one failed B-roll scene, films it again
as it writes it, and reads the judge's answer, until a try passes or it gives up. Every try
is a version the shop owner can see and pick. The agent's turns, the judge, the pictures and
the clips are faked at the gateway (spec: handoffs/fixing-agent/spec.md)."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge.retry import OutsideServiceDown
from agents import fixing_agent, producer, tasks
from agents.fixing_agent import Failure, Outcome, Try, fix_scene
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import Said
from jobs import work
from jobs.models import BROLL_FIELDS, AgentFix, Fix, ProducedItem, Scene, SceneStep

from .conftest import (
    CLIP_MOTION_OK,
    PICTURE_OK,
    HeldSteps,
    WorkerStopped,
    facts_ok,
    handoffs,
    motion_answer,
)
from .fixing import fixed as fixed_by_a_plan
from .test_pick_from_tries import made_ad  # noqa: F401 (a fixture)
from .test_producer_auto_fix import FLOATS, REMADE, clip_of_scene_2, failing, judged, rewrite
from .test_producer_broll import (
    LINE_2,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import ads

pytestmark = pytest.mark.django_db(transaction=True)

STIRS = "A hand stirs the tea in the mug with a spoon for the whole clip."
POURS = "Tea pours from the teapot into the mug for the whole clip, filling it."


@pytest.fixture
def failed_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Scene 2's clip, made and judged: the step the agent is handed."""
    return clip_of_scene_2(fake_model, steps, say)


def colour_photo() -> int:
    photo = ProducedItem.objects.get(kind="clip").job.photos.filter(shows_product_colour=True)[0]
    return photo.position


def film_again(**changes: Any) -> tuple[str, dict[str, Any]]:
    """The agent's film_again call: scene 2 as planned, filmed again with the same picture,
    with `changes`."""
    return (
        "film_again",
        {
            "why": "The action stops too soon, so the model fills the clip.",
            "line": LINE_2,
            "shows": SHOWS,
            "result": "",
            "broll_kind": "showcase",
            "person_shown": "no face",
            "new_picture": False,
            "photo": None,
            "picture_prompt": None,
            "video_prompt": STIRS,
            "would_ask_owner": None,
            **changes,
        },
    )


def fixed(step: SceneStep, **kwargs: Any) -> Outcome:
    clip = step.produced.order_by("id").first()
    assert clip is not None
    return fix_scene(step, door="judge", failure=Failure(ruling=clip.ruling), goal=None, **kwargs)


# --- A try the judge passes ------------------------------------------------------------------


@pytest.fixture
def passed_on_try_2(fake_model: FakeModel, failed_clip: SceneStep) -> Outcome:
    """The agent films twice: the judge fails try 1 on the teapot floating, passes try 2."""
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[film_again()]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    return fixed(failed_clip)


def test_a_try_the_judge_passes_is_the_scenes_clip(passed_on_try_2: Outcome) -> None:
    assert [verdict(each) for each in passed_on_try_2.tries] == ["fail", "pass"]
    assert passed_on_try_2.passed == passed_on_try_2.tries[-1].clip
    assert passed_on_try_2.gave_up is None
    assert scene_clip() == passed_on_try_2.passed


def clip_of(made: Try) -> ProducedItem:
    assert made.clip is not None
    return made.clip


def verdict(made: Try) -> str:
    ruling = clip_of(made).ruling
    assert ruling is not None
    return str(ruling["verdict"])


def told() -> list[str]:
    """What each tool the agent called told it, in order."""
    return list(
        ToolCall.objects.filter(agent="fixing_agent")
        .exclude(tool="take_turn")
        .order_by("id")
        .values_list("result", flat=True)
    )


def scene_row() -> tuple[str, str, str, str, str]:
    scene = Scene.objects.get(number=2)
    return (scene.line, scene.shows, scene.result, scene.broll_kind, scene.person_shown)


def scene_clip() -> ProducedItem:
    """Scene 2's clip now: its newest."""
    clip = ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("version").last()
    assert clip is not None
    return clip


def test_every_try_is_a_version_named_by_its_try(passed_on_try_2: Outcome) -> None:
    assert [version.label for version in work.versions(Scene.objects.get(number=2))] == [
        "v1 first version (first clip)",
        "v2 try 1",
        "v3 try 2",
    ]


def test_each_try_is_filmed_from_the_agents_own_video_prompt(passed_on_try_2: Outcome) -> None:
    assert [
        each.video_prompt.endswith(prompt)
        for each, prompt in zip(passed_on_try_2.tries, [POURS, STIRS], strict=True)
    ] == [True, True]
    assert [clip_of(each).remake for each in passed_on_try_2.tries] == [1, 2]
    assert passed_on_try_2.cost_usd > sum(each.cost_usd for each in passed_on_try_2.tries) > 0


def test_every_model_call_of_the_fixing_is_charged_to_one_of_its_tool_calls(
    passed_on_try_2: Outcome,
) -> None:
    fixing = AgentFix.objects.get()
    calls = ModelCall.objects.filter(created_at__gte=fixing.created_at)
    assert calls.filter(purpose="fix_scene").count() == 2
    assert not calls.filter(tool_call__isnull=True).exists()
    assert set(calls.values_list("tool_call__agent", flat=True)) == {"fixing_agent"}


def test_the_agent_is_told_the_judges_words_and_the_tries_left(passed_on_try_2: Outcome) -> None:
    first, second = told()
    assert "The judge's verdict: fail." in first and FLOATS in first
    assert first.endswith("Tries left: 5 of 6.")
    assert "It passes." in second and second.endswith("Tries left: 4 of 6.")


def test_a_fixed_scene_is_never_looked_at_again(
    fake_model: FakeModel, passed_on_try_2: Outcome
) -> None:
    turns = len(fake_model.turns_taken)
    again = fixed(SceneStep.objects.get(agent_fix__isnull=False))
    assert again == passed_on_try_2
    assert len(fake_model.turns_taken) == turns


# --- The scene's row: written back on a pass, put back on a give-up ------------------------

NEW_LINE = "Pour it, stir it, sip it: it holds 350 ml."


@pytest.fixture
def new_line_passes(fake_model: FakeModel, failed_clip: SceneStep) -> Outcome:
    """The agent films a new line and a new action, and the judge passes it."""
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
    )
    return fixed(failed_clip)


def test_a_passing_tries_row_is_the_scenes_and_its_try_steps(
    new_line_passes: Outcome, failed_clip: SceneStep
) -> None:
    assert scene_row() == (NEW_LINE, STIRS, "", "showcase", "no face")
    passed = new_line_passes.passed
    assert passed is not None and passed.step is not None
    assert (passed.step.line, passed.step.shows) == (NEW_LINE, STIRS)
    # The step the agent fixed keeps the row its first clip was made for, as that clip says.
    failed_clip.refresh_from_db()
    assert (failed_clip.line, failed_clip.shows) == (LINE_2, SHOWS)


def test_going_back_to_the_first_clip_after_a_new_line_passed_makes_a_new_clip(
    new_line_passes: Outcome,
) -> None:
    scene = Scene.objects.get(number=2)
    assert not work.go_back_is_free(scene, 1)
    assert work.go_back(scene, 1).clip_to_make


def test_a_new_line_is_said_again_and_its_clip_speaks_it(
    fake_model: FakeModel, new_line_passes: Outcome
) -> None:
    assert fake_model.spoken[-1] == NEW_LINE
    assert new_line_passes.passed is not None
    audio = new_line_passes.passed.made_from
    assert audio is not None and audio.step is not None and audio.step.line == NEW_LINE
    assert ProducedItem.objects.filter(kind="transcript", made_from=audio).exists()


@pytest.fixture
def given_up(fake_model: FakeModel, failed_clip: SceneStep) -> Outcome:
    """The agent films a new line the judge fails, then gives up, picking that try."""
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
        turn(calls=[("say_to_camera", {"reason": "The stir never reads.", "best_try": 1})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    return fixed(failed_clip)


def test_a_give_up_says_why_and_marks_the_agents_pick(given_up: Outcome) -> None:
    assert given_up.passed is None
    assert given_up.gave_up == "The stir never reads."
    assert [(each.number, each.picked) for each in given_up.tries] == [(1, True)]


def test_a_give_up_puts_the_scene_back_as_it_was(given_up: Outcome) -> None:
    assert scene_row() == (LINE_2, SHOWS, "", "showcase", "no face")
    assert Scene.objects.get(number=2).fact_checked


def test_a_given_up_scenes_tries_are_still_versions(given_up: Outcome) -> None:
    assert [version.label for version in work.versions(Scene.objects.get(number=2))] == [
        "v1 first version (first clip)",
        "v2 try 1",
    ]


# --- What code refuses -----------------------------------------------------------------------


def refused_then_given_up(fake_model: FakeModel, step: SceneStep, *calls: Any) -> list[str]:
    """Have the agent make `calls`, one a turn, then film once and give up; gives what it
    was told of each."""
    fake_model.respond(
        "fix_scene",
        *[turn(calls=[call]) for call in calls],
        turn(calls=[film_again()]),
        turn(calls=[("say_to_camera", {"reason": "It can't be filmed."})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    fixed(step)
    return told()[: len(calls)]


@pytest.mark.parametrize(
    ("prompt", "bans"),
    [
        ("The tea pours. Nothing spills.", "nothing"),
        ("The tea pours and doesn't spill, with no drips.", "n't, no"),
        ("Avoid spills; never tip the mug.", "avoid, never"),
    ],
)
def test_a_video_prompt_with_bans_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep, prompt: str, bans: str
) -> None:
    [answer] = refused_then_given_up(fake_model, failed_clip, film_again(video_prompt=prompt))
    assert answer.startswith(f"Refused: the video prompt has bans ({bans}).")


def test_a_line_the_fact_check_fails_is_refused_with_its_reason(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fact_check",
        {
            "decision": "checked",
            "reason": "One claim isn't on the page.",
            "question": None,
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "wrong": "line",
                    "problem": "The page never says it keeps tea hot.",
                    "page_says": "It holds 350 ml.",
                }
            ],
        },
    )
    [answer] = refused_then_given_up(
        fake_model, failed_clip, film_again(line="It keeps tea hot for hours.")
    )
    assert answer.startswith(
        "Refused: the fact check failed it: The page never says it keeps tea hot. The page "
        "says: It holds 350 ml."
    )
    assert scene_row()[0] == LINE_2


def test_showing_the_face_without_a_new_picture_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    [answer] = refused_then_given_up(fake_model, failed_clip, film_again(person_shown="has face"))
    assert answer.startswith("Refused: showing or hiding the face changes the picture")


def test_a_result_on_a_scene_that_doesnt_do_a_job_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    [answer] = refused_then_given_up(fake_model, failed_clip, film_again(result="The mug is full."))
    assert answer.startswith('Refused: only a "does a job" scene has a result.')


def test_saying_it_to_camera_before_filming_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    [answer] = refused_then_given_up(
        fake_model, failed_clip, ("say_to_camera", {"reason": "Too hard."})
    )
    assert answer.startswith("Refused: try to film it at least once")


def test_a_line_that_cant_be_voiced_is_refused_and_the_old_line_kept(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("speak_line", *[OutsideServiceDown("503 from the voice service")] * 3)
    [answer] = refused_then_given_up(fake_model, failed_clip, film_again(line=NEW_LINE))
    assert answer.startswith(
        "Refused: the new line can't be voiced right now, so keep the scene's line as it is."
    )
    assert scene_row()[0] == LINE_2


def test_a_refused_try_is_no_try(fake_model: FakeModel, failed_clip: SceneStep) -> None:
    refused_then_given_up(fake_model, failed_clip, film_again(video_prompt="Never spill."))
    assert [each.number for each in AgentFix.objects.get().tries.all()] == [1]


# --- The cap ---------------------------------------------------------------------------------


def test_the_seventh_try_is_refused_and_the_agent_told_to_give_up(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        *[turn(calls=[film_again()]) for _ in range(7)],
        turn(calls=[("say_to_camera", {"reason": "Six tries failed."})]),
    )
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 18)
    outcome = fixed(failed_clip)
    assert len(outcome.tries) == 6
    assert told()[6].startswith(
        "Refused: you have filmed all 6 tries. Call say_to_camera with your reason"
    )
    assert told()[6].endswith("Tries left: 0 of 6.")
    assert outcome.gave_up == "Six tries failed."


# --- A new start picture -----------------------------------------------------------------------

PICTURE_PROMPT = "Image 1's mug on the bench of Image 2, a hand holding a spoon above it."
CROOKED = "The handle is on the wrong side."


def a_new_picture(**changes: Any) -> tuple[str, dict[str, Any]]:
    return film_again(
        new_picture=True, photo=colour_photo(), picture_prompt=PICTURE_PROMPT, **changes
    )


def picture_fails() -> dict[str, Any]:
    return {**PICTURE_OK, "verdict": "fail", "problem": CROOKED}


def test_a_new_start_picture_is_drawn_checked_and_filmed(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[a_new_picture()]))
    outcome = fixed(failed_clip)
    [made] = outcome.tries
    assert made.picture is not None and made.picture.change == "try 1"
    assert made.clip is not None and made.clip.picture == made.picture
    assert made.picture.step is not None and made.picture.step.prompt == PICTURE_PROMPT
    assert [version.label for version in work.versions(Scene.objects.get(number=2))] == [
        "v1 first version (first clip)",
        "v2 try 1",
    ]


def test_a_picture_that_fails_its_check_is_drawn_again_without_counting_as_a_try(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("check_starting_picture", picture_fails(), picture_fails())
    fake_model.respond("fix_scene", turn(calls=[a_new_picture()]), turn(calls=[a_new_picture()]))
    outcome = fixed(failed_clip)
    assert told()[0].startswith(
        f"Refused: the picture check failed the new start picture: {CROOKED} {CROOKED} Draw it "
        "again (redraw 1 of 2"
    )
    assert [each.number for each in outcome.tries] == [1]
    assert outcome.passed is not None


def test_a_picture_that_keeps_failing_ends_its_try_with_no_clip(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("check_starting_picture", *[picture_fails()] * 6)
    fake_model.respond(
        "fix_scene",
        *[turn(calls=[a_new_picture()]) for _ in range(3)],
        turn(calls=[film_again()]),
    )
    outcome = fixed(failed_clip)
    assert told()[2].startswith(
        f"The picture check failed the new start picture: {CROOKED} {CROOKED} That was the last "
        "redraw: try 1 ends with no clip."
    )
    assert [(each.number, each.clip is None) for each in outcome.tries] == [(1, True), (2, False)]


# --- Door 2: the shop owner's note -------------------------------------------------------------

NOTE = "The hand just hovers. She should stir the tea."
GOAL = "She stirs the tea."
STILL_HOVERS = "The hand still hovers beside the mug."


def owners_fix(fake_model: FakeModel, step: SceneStep, *calls: Any) -> Outcome:
    """The agent fixes scene 2 for the owner's note, filming `calls`, one a turn."""
    first = step.produced.order_by("id").first()
    step.fix = Fix.objects.create(
        job=step.scene.job,
        scene=step.scene,
        fix=GOAL,
        redo=Fix.Redo.CLIP,
        estimate_usd=0,
        asked=step.tool_call,
    )
    step.save(update_fields=["fix"])
    fake_model.respond("fix_scene", *[turn(calls=[call]) for call in calls])
    return fix_scene(step, door="owner", failure=Failure(note=NOTE, clip=first), goal=GOAL)


def test_an_owners_note_is_given_to_the_agent_with_its_goal(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    owners_fix(fake_model, failed_clip, film_again())
    [first, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    said = first.handoff.conversation[0]
    assert isinstance(said, Said)
    intro = said.text
    assert f'sent this note about scene 2: "{NOTE}"' in intro
    assert f"What must be different, in the owner's words: {GOAL}" in intro


def test_a_try_for_an_owners_note_is_judged_beside_the_clip_they_noted(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    noted = failed_clip.produced.order_by("id").first()
    outcome = owners_fix(fake_model, failed_clip, film_again(), film_again())
    assert outcome.tries[0].ruling["compared_with"] == noted.pk  # type: ignore[index, union-attr]
    assert outcome.tries[0].fix_check == {"verdict": "passed", "reason": ""}
    assert outcome.passed == outcome.tries[0].clip


def test_a_try_whose_owners_change_isnt_made_fails_though_the_judge_passes_it(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    not_made = {
        "old_shows": "The hand hovers.",
        "new_shows": "The hand hovers.",
        "verdict": "not fixed",
        "still_wrong": STILL_HOVERS,
    }
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, owner_change=not_made)
    )
    outcome = owners_fix(
        fake_model,
        failed_clip,
        film_again(),
        ("say_to_camera", {"reason": "The stir never shows."}),
    )
    [made] = outcome.tries
    assert made.ruling["verdict"] == "pass"  # type: ignore[index]
    assert made.fix_check is not None and made.fix_check["verdict"] == "failed"
    assert outcome.passed is None and outcome.gave_up == "The stir never shows."
    assert f"Whether the owner's change is made: no. {STILL_HOVERS}" in told()[0]


# --- An old plan's new clip ----------------------------------------------------------------

SLOWER = "Pour the tea slower."


def failing_first(clips: int) -> Callable[[Any], dict[str, Any]]:
    """MOTION failing every answer about the first `clips` clips judged, passing after,
    each with the shop owner's change made."""
    failed = iter([failing(FLOATS)] * 3 * clips)

    def answering(request: Any) -> dict[str, Any]:
        return motion_answer(next(failed, CLIP_MOTION_OK))(request)

    return answering


def test_an_old_plans_new_clip_the_agent_fixes_with_a_new_line_lands(
    made_ad: ProducedItem,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    plan = fixed_by_a_plan(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.answer_unscripted("check_clip_motion", failing_first(1))
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)])
    )

    steps.run_held()

    passed = AgentFix.objects.get().passed
    assert passed is not None and scene_row()[0] == NEW_LINE
    row = plan.scenes.get()
    assert (row.landed_at is not None, row.error) == (True, "")
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [passed.pk]


# --- When the agent stops ------------------------------------------------------------------


def test_an_agent_that_stops_after_a_try_puts_the_scene_back_and_keeps_the_first_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        *judged(failing(FLOATS), failing(FLOATS)),
        *judged(failing(FLOATS), failing(FLOATS)),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
        *[OutsideServiceDown("503 from the model")] * 6,
    )

    step = clip_of_scene_2(fake_model, steps, say)

    # Try 1 changed the line and the judge failed it; then the agent stopped. The scene is
    # put back as it was, and the ad is assembled from the first clip, never from try 1.
    assert scene_row() == (LINE_2, SHOWS, "", "showcase", "no face")
    assert AgentFix.objects.get().finished_at is not None
    first = step.produced.order_by("id").first()
    assert first is not None
    used, _ = producer._clip_and_transcript(Scene.objects.get(number=2))
    assert used.file == first.file


# --- When the worker restarts ----------------------------------------------------------------


def said_to_camera_after_a_restart() -> AgentFix:
    """Restart the worker; gives how the agent's fixing of scene 2 ended. The step it worked
    in is failed, as every step running is, and its give-up kept: scene 2 is said to camera,
    so no try the judge failed is in the ad."""
    tasks.carry_on_after_the_worker_starts()
    fixing = AgentFix.objects.get()
    assert fixing.step.status == SceneStep.Status.FAILED
    assert (fixing.finished_at is not None, fixing.said_to_camera) == (True, True)
    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.shows) == (LINE_2, "")
    assert work.waits_to_be_said_to_camera(scene)
    return fixing


def test_a_give_up_the_worker_stopped_before_keeping_is_kept_once_it_restarts(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond(
        "check_clip_motion",
        *judged(failing(FLOATS), failing(FLOATS)),
        *judged(failing(FLOATS), failing(FLOATS)),
    )
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again()]),
        turn(calls=[("say_to_camera", {"reason": "The stir never reads."})]),
    )

    def stopped(*_: object, **__: object) -> None:
        raise WorkerStopped()

    monkeypatch.setattr(work, "_ended", stopped)
    with pytest.raises(WorkerStopped):
        clip_of_scene_2(fake_model, steps, say)
    monkeypatch.undo()

    assert said_to_camera_after_a_restart().gave_up == "The stir never reads."


def test_a_fixing_the_worker_stopped_in_is_given_up_and_the_scene_put_back(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        *judged(failing(FLOATS), failing(FLOATS)),
        *judged(failing(FLOATS), failing(FLOATS)),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
        WorkerStopped(),
    )
    with pytest.raises(WorkerStopped):
        clip_of_scene_2(fake_model, steps, say)
    assert scene_row()[0] == NEW_LINE

    assert said_to_camera_after_a_restart().gave_up


def test_the_producer_never_takes_a_try_for_a_clip_already_made(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        *judged(failing(FLOATS), failing(FLOATS)),
        *judged(failing(FLOATS), failing(FLOATS)),
    )
    fake_model.respond("fix_scene", turn(calls=[film_again()]), WorkerStopped())
    with pytest.raises(WorkerStopped):
        clip_of_scene_2(fake_model, steps, say)
    fixing = said_to_camera_after_a_restart()
    # The scene shows the mug again, as before it was said to camera, from the same picture
    # and audio try 1 was filmed from.
    before = fixing.camera_before
    Scene.objects.filter(number=2).update(
        shows=before["shows"], **{field: before[field] for field in BROLL_FIELDS}
    )
    asked = len(handoffs("make_broll_clip"))

    calling(fake_model, say, ("make_clip", {"scene": 2}))

    # Try 1 is no clip of the scene's to hand back: the scene, with a clip, changes only
    # through the shop owner's notes.
    made = ToolCall.objects.filter(tool="make_clip").latest("id")
    assert "already made" not in made.result
    assert made.result.startswith("Refused: scene 2 already has a clip")
    assert len(handoffs("make_broll_clip")) == asked


# --- In the background ---------------------------------------------------------------------


def test_the_fixing_agent_runs_in_the_background_and_hands_its_door_the_outcome(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    handed: list[tuple[SceneStep, Outcome]] = []
    monkeypatch.setitem(
        fixing_agent.WHEN_DONE, "owner", lambda step, done: handed.append((step, done))
    )
    SceneStep.objects.filter(pk=failed_clip.pk).update(status=SceneStep.Status.RUNNING)
    first = failed_clip.produced.order_by("id").first()
    failed_clip.fix = Fix.objects.create(
        job=failed_clip.scene.job,
        scene=failed_clip.scene,
        fix=GOAL,
        redo=Fix.Redo.CLIP,
        estimate_usd=0,
        asked=failed_clip.tool_call,
    )
    failed_clip.save(update_fields=["fix"])
    AgentFix.objects.create(step=failed_clip, door="owner", note=NOTE, goal=GOAL, first_clip=first)
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    tasks.run_fixing_agent(failed_clip.pk, "owner", NOTE, GOAL)
    [(step, done)] = handed
    assert step == failed_clip and done.passed == done.tries[0].clip


def test_a_step_that_has_ended_is_never_fixed_in_the_background(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    tasks.run_fixing_agent(failed_clip.pk, "judge")
    assert not AgentFix.objects.exists()
