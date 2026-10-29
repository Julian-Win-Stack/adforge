"""The fixing agent (agents/fixing_agent.py): it owns one failed B-roll scene, films it again
as it writes it, and reads the judge's answer, until a try passes or it gives up. Every try
is a version the shop owner can see and pick. The agent's turns, the judge, the pictures and
the clips are faked at the gateway (spec: handoffs/fixing-agent/spec.md)."""

import json
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.db.models import Q, Sum

from adforge.retry import OutsideServiceDown
from agents import fixing_agent, producer, tasks
from agents.fixing_agent import Failure, Outcome, Try, fix_scene
from agents.models import ToolCall
from agents.money import dollars
from agents.snapshot import ad_as_it_stands
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Said, ToolUse
from jobs import work
from jobs.clip_judge import ASKS
from jobs.models import AgentFix, Fix, ProducedItem, Scene, SceneStep

from .conftest import (
    CLIP_MOTION_OK,
    PICTURE_OK,
    HeldSteps,
    WorkerStopped,
    facts_ok,
    handoffs,
    motion_answer,
    paid_for,
)
from .fixing import fixed as fixed_by_a_plan
from .sent_lists import holding_the_fixture
from .test_pick_from_tries import made_ad  # noqa: F401 (a fixture)
from .test_producer_auto_fix import FLOATS, REMADE, clip_of_scene_2, failing, rewrite
from .test_producer_broll import (
    LINE_2,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
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
            "root_cause": "The action stops too soon, so the model fills the clip.",
            "move": "Pick an action that keeps going for the whole clip.",
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


# --- Looking at a try ------------------------------------------------------------------------


def looks_at_try_0(fake_model: FakeModel, step: SceneStep) -> tuple[ToolUse, ToolCall]:
    """The agent looks at try 0 and films once: the try 0 it was given first, and its look."""
    fake_model.respond(
        "fix_scene", turn(calls=[("look_at_try", {"number": 0})]), turn(calls=[film_again()])
    )
    fixed(step)
    [first, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    given = first.handoff.conversation[1]
    assert isinstance(given, ToolUse)
    return given, ToolCall.objects.get(agent="fixing_agent", tool="look_at_try")


def test_a_try_whose_clip_has_a_sent_list_is_shown_by_its_references_then_its_frames(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    clip = failed_clip.produced.order_by("id").first()
    assert clip is not None
    sent = holding_the_fixture(clip)

    given, look = looks_at_try_0(fake_model, failed_clip)

    for labels in (
        [picture.label for picture in given.pictures],
        [picture["label"] for picture in look.pictures],
    ):
        assert labels[:9] == [f"Try 0, Image {each.image}: {each.label}" for each in sent.pictures]
        rest = labels[9:]
        pieces = [label for label in rest if label.startswith("Try 0, Video ")]
        frames = [label for label in rest if label.startswith("Try 0, clip, ")]
        assert pieces and frames and rest == pieces + frames
        # Each piece's frames are joined 4 to a picture, as the clip's are.
        for video in (1, 2):
            assert f"Try 0, Video {video}, Frames at 0 s, 0.2 s, 0.4 s, 0.6 s, left to right" in (
                pieces
            )
        assert not any("start picture" in label for label in labels)
    # Try 0 is the same pictures on every turn, so a paid turn is replayed and a refused
    # picture known again.
    [first, second, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    try_0 = [each.handoff.conversation[1] for each in (first, second)]
    assert all(isinstance(each, ToolUse) for each in try_0)
    assert [
        [shown.key for shown in each.pictures] for each in try_0 if isinstance(each, ToolUse)
    ] == [[shown.key for shown in given.pictures]] * 2


def test_a_try_whose_clip_has_no_sent_list_is_shown_by_its_start_picture(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    given, look = looks_at_try_0(fake_model, failed_clip)

    for labels in (
        [picture.label for picture in given.pictures],
        [picture["label"] for picture in look.pictures],
    ):
        assert labels[0] == "Try 0, start picture"
        assert labels[1:] and all(label.startswith("Try 0, clip, ") for label in labels[1:])
    assert look.result.startswith("Try 0. ")


# --- A try the judge passes ------------------------------------------------------------------


@pytest.fixture
def passed_on_try_2(fake_model: FakeModel, failed_clip: SceneStep) -> Outcome:
    """The agent films twice: the judge fails try 1 on the teapot floating, passes try 2."""
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[film_again()]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))
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


def test_the_agent_is_told_the_judges_words(passed_on_try_2: Outcome) -> None:
    first, second = told()
    assert first.startswith("Try 1.") and second.startswith("Try 2.")
    assert "The judge's verdict: fail." in first and FLOATS in first
    assert first.endswith("It fails.") and second.endswith("It passes.")


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
        turn(calls=[("give_up", {"reason": "The stir never reads.", "best_try": 1})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))
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
        turn(calls=[("give_up", {"reason": "It can't be filmed."})]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fixed(step)
    return told()[: len(calls)]


# Which words are bans is tested in tests/test_fixing_agent_rules.py.
def test_a_video_prompt_with_bans_is_refused(fake_model: FakeModel, failed_clip: SceneStep) -> None:
    prompt = "The tea pours and doesn't spill, with no drips."
    [answer] = refused_then_given_up(fake_model, failed_clip, film_again(video_prompt=prompt))
    assert answer.startswith("Refused: the video prompt has bans (n't, no).")


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


def test_giving_up_before_filming_is_refused(fake_model: FakeModel, failed_clip: SceneStep) -> None:
    [answer] = refused_then_given_up(fake_model, failed_clip, ("give_up", {"reason": "Too hard."}))
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


def test_a_new_line_that_cant_be_heard_back_is_refused_before_any_picture_is_drawn(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("transcribe_line", *[OutsideServiceDown("401 Unauthorized")] * 3)
    edits = fake_model.edits
    [answer] = refused_then_given_up(
        fake_model, failed_clip, a_new_picture(line=NEW_LINE, shows=STIRS)
    )
    assert answer.startswith(
        "Refused: the new line can't be voiced right now, so keep the scene's line as it is."
    )
    assert fake_model.edits == edits
    assert scene_row()[:2] == (LINE_2, SHOWS)


def test_a_refused_try_is_no_try(fake_model: FakeModel, failed_clip: SceneStep) -> None:
    refused_then_given_up(fake_model, failed_clip, film_again(video_prompt="Never spill."))
    assert [each.number for each in AgentFix.objects.get().tries.all()] == [1]


# --- The cap: silent, and code ends the fix ------------------------------------------------

# A count of tries or turns, as the agent might be told one: "5 tries", "tries left",
# "try 2 of 6", "at most 6 tries", "after 40 turns".
COUNTED = re.compile(
    r"\b\d+\s+(?:tries|turns)\b|\b(?:tries|turns)\s+left\b|\b(?:try|turn)\s+\d+\s+of\s+\d+",
    re.IGNORECASE,
)


def everything_the_agent_read(fake_model: FakeModel) -> list[str]:
    """Every text the agent was handed on every turn: its instructions, its tools'
    descriptions and argument schemas, and each line of its conversation."""
    read: list[str] = []
    for each in fake_model.turns_taken:
        if each.purpose != "fix_scene":
            continue
        read.append(each.instructions)
        for tool in each.tools:
            read += [tool.description, json.dumps(tool.arguments.model_json_schema())]
        for happened in each.handoff.conversation:
            read.append(happened.result if isinstance(happened, ToolUse) else happened.text)
    return read


@pytest.fixture
def ended_by_the_cap(fake_model: FakeModel, failed_clip: SceneStep) -> Outcome:
    """A whole fix with a cap of 2: a look, a refused try, a failing try, a turn with no tool,
    then a second failing try, which code ends the fix on."""
    fake_model.respond(
        "fix_scene",
        turn(calls=[("look_at_place", {})]),
        turn(calls=[film_again(video_prompt="Never spill.")]),
        turn(calls=[film_again(video_prompt=POURS)]),
        turn("Let me think about the pour."),
        turn(calls=[film_again()]),
        # Never taken: code ends the fix once the cap is reached.
        turn(calls=[("give_up", {"reason": "Two tries failed."})]),
    )
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 6)
    return fixed(failed_clip, cap=2)


def test_the_agent_is_never_told_a_count_of_tries_or_turns(
    fake_model: FakeModel, ended_by_the_cap: Outcome
) -> None:
    read = everything_the_agent_read(fake_model)
    assert [COUNTED.findall(text) for text in read if COUNTED.search(text)] == []
    assert [COUNTED.findall(text) for text in told() if COUNTED.search(text)] == []


def test_code_ends_the_fix_once_the_cap_is_reached_and_the_record_says_so(
    fake_model: FakeModel, ended_by_the_cap: Outcome
) -> None:
    # No turn after the second try: the agent never gets to give up or film a third.
    assert len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"]) == 5
    assert [(each.number, verdict(each)) for each in ended_by_the_cap.tries] == [
        (1, "fail"),
        (2, "fail"),
    ]
    assert (ended_by_the_cap.passed, ended_by_the_cap.ended_by_cap) == (None, True)
    fixing = AgentFix.objects.get()
    assert fixing.ended_by_cap and fixing.finished_at is not None
    assert fixing.gave_up == fixing_agent.ENDED_BY_CAP
    assert fixing.picked is None


def test_a_try_at_the_cap_that_passes_is_the_scenes_clip(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    outcome = fixed(failed_clip, cap=1)

    assert (outcome.passed, outcome.gave_up, outcome.ended_by_cap) == (
        outcome.tries[0].clip,
        None,
        False,
    )


def test_a_second_try_in_the_turn_that_reached_the_cap_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS), film_again()]))
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 6)

    outcome = fixed(failed_clip, cap=1)

    assert [each.number for each in outcome.tries] == [1]
    assert outcome.ended_by_cap
    assert told()[1] == "Refused: the scene's fixing has ended. Nothing was done."


def test_a_worker_that_stopped_as_the_cap_ended_the_fix_ends_it_on_its_restart(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS)]))
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    give_up = fixing_agent.give_up

    def stops_once(*args: Any) -> None:
        monkeypatch.setattr(fixing_agent, "give_up", give_up)
        raise WorkerStopped()

    monkeypatch.setattr(fixing_agent, "give_up", stops_once)
    with pytest.raises(WorkerStopped):
        fixed(failed_clip, cap=1)
    turns = len(fake_model.turns_taken)

    outcome = fixed(failed_clip, cap=1)

    assert len(fake_model.turns_taken) == turns
    assert ([each.number for each in outcome.tries], outcome.ended_by_cap) == ([1], True)


def test_a_worker_stopped_while_the_judge_watched_the_last_try_films_no_try_past_the_cap(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS), film_again()]))
    fake_model.respond("check_clip_motion", WorkerStopped(), *[failing(FLOATS)] * 3)
    with pytest.raises(WorkerStopped):
        fixed(failed_clip, cap=1)

    outcome = fixed(failed_clip, cap=1)

    assert ([each.number for each in outcome.tries], outcome.ended_by_cap) == ([1], True)
    assert scene_clip() == clip_of(outcome.tries[0])
    assert told()[-1] == "Refused: the scene's fixing has ended. Nothing was done."


def test_a_give_up_after_the_last_try_in_a_restarted_turn_leaves_the_cap_on_the_record(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(), ("give_up", {"reason": "Tried enough.", "best_try": 1})]),
    )
    fake_model.respond("check_clip_motion", WorkerStopped(), *[failing(FLOATS)] * 3)
    with pytest.raises(WorkerStopped):
        fixed(failed_clip, cap=1)

    outcome = fixed(failed_clip, cap=1)

    assert (outcome.gave_up, outcome.ended_by_cap) == (fixing_agent.ENDED_BY_CAP, True)
    assert AgentFix.objects.get().picked is None
    assert told()[-1] == "Refused: the scene's fixing has ended. Nothing was done."


def test_a_worker_stopped_as_the_cap_ended_the_fix_films_no_later_try_of_that_turn(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS), film_again()]))
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    give_up = fixing_agent.give_up

    def stops_once(*args: Any) -> None:
        monkeypatch.setattr(fixing_agent, "give_up", give_up)
        raise WorkerStopped()

    monkeypatch.setattr(fixing_agent, "give_up", stops_once)
    with pytest.raises(WorkerStopped):
        fixed(failed_clip, cap=1)

    outcome = fixed(failed_clip, cap=1)

    assert ([each.number for each in outcome.tries], outcome.ended_by_cap) == ([1], True)
    assert scene_clip() == clip_of(outcome.tries[0])
    answer = told()[-1]
    assert answer == "Refused: the scene's fixing has ended. Nothing was done."
    assert not COUNTED.search(answer)


def test_a_scene_the_cap_ended_keeps_its_last_try_and_the_chat_says_the_cap_ended_it(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fixing_agent, "MOST_TRIES", 2)
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[film_again()]),
    )

    step = clip_of_scene_2(fake_model, steps, say)

    fixing = AgentFix.objects.get()
    second = fixing.tries.get(number=2).clip
    assert (fixing.ended_by_cap, fixing.kept, scene_clip()) == (True, second, second)
    assert work.agent_gave_up(step)
    (said,) = Message.objects.filter(role=Message.Role.NOTICE, tries_of=step)
    assert said.text == (
        "The clip judge found a problem in all 3 tries at scene 2's clip, and the fixing agent "
        "filmed as many tries as it may, so the last try is in the ad. Watch them, and tap one "
        "to use it instead."
    )
    step.refresh_from_db()
    assert (
        "and the fixing agent filmed as many tries as it may (2), none passing, so the last try "
        "is in the ad."
    ) in step.result


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


def test_a_tries_picture_is_never_priced_as_a_picture_with_its_clip_and_judge(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    scene = Scene.objects.get(number=2)
    before = producer._picture_only_estimate_usd(scene)
    fake_model.respond("fix_scene", turn(calls=[a_new_picture()]))
    fixed(failed_clip)
    assert producer._picture_only_estimate_usd(scene) == before


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
        ("give_up", {"reason": "The stir never shows."}),
    )
    [made] = outcome.tries
    assert made.ruling["verdict"] == "pass"  # type: ignore[index]
    assert made.fix_check is not None and made.fix_check["verdict"] == "failed"
    assert outcome.passed is None and outcome.gave_up == "The stir never shows."
    assert f"Whether the owner's change is made: no. {STILL_HOVERS}" in told()[0]


# --- Door 1 through the chat, the real agent on scripted turns ------------------------------


def paid_for_the_step(step: SceneStep) -> Decimal:
    """Every model call made for a clip step, the fixing agent's on it included."""
    paid: Decimal = ModelCall.objects.filter(
        Q(tool_call=step.tool_call) | Q(tool_call__agent_fix__step=step)
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    return paid


def test_through_the_chat_the_agent_films_until_a_try_passes(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=POURS)]), turn(calls=[film_again()])
    )

    step = clip_of_scene_2(fake_model, steps, say)

    step.refresh_from_db()
    passed = AgentFix.objects.get(step=step).passed
    assert passed is not None and passed.change == "try 2"
    assert [version.label for version in work.versions(Scene.objects.get(number=2))] == [
        "v1 first version (first clip)",
        "v2 try 1",
        "v3 try 2",
    ]
    used, _ = producer._clip_and_transcript(Scene.objects.get(number=2))
    assert used == passed
    # The producer is told what the step cost, the agent's turns and tries included.
    assert step.status == SceneStep.Status.FINISHED
    assert f"It cost {dollars(paid_for_the_step(step))}." in step.result
    assert paid_for_the_step(step) > step.tool_call.cost_usd()


# --- An old plan's new clip ----------------------------------------------------------------

SLOWER = "Pour the tea slower."


def failing_first(clips: int) -> Callable[[Any], dict[str, Any]]:
    """MOTION failing every answer about the first `clips` clips judged, passing after,
    each with the shop owner's change made."""
    failed = iter([failing(FLOATS)] * ASKS * clips)

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

    fixing = AgentFix.objects.get()
    passed = fixing.passed
    assert passed is not None and scene_row()[0] == NEW_LINE
    # The shop owner's note goes with it, and the judge compares the try with the clip they
    # noted.
    assert (SLOWER in fixing.note, fixing.goal) == (True, SLOWER)
    noted = fixing.step.shown_before(ProducedItem.Kind.CLIP)
    assert noted is not None and (passed.ruling or {}).get("compared_with") == noted.pk
    row = plan.scenes.get()
    assert (row.landed_at is not None, row.error) == (True, "")
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [passed.pk]


def test_an_agent_fixing_an_old_plans_new_clip_is_told_giving_up_keeps_the_scenes_clip(
    made_ad: ProducedItem,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    fixed_by_a_plan(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.answer_unscripted("check_clip_motion", failing_first(50))
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again()]),
        turn(calls=[("give_up", {"reason": "The pour never slows."})]),
    )

    steps.run_held()

    # Door 1 took it, but a fix's scene keeps its clip from before, so that's what it's told.
    fixing = AgentFix.objects.get()
    assert (fixing.door, fixing.said_to_camera) == (AgentFix.Door.JUDGE, False)
    [turn_1, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    assert fixing_agent.GAVE_UP[AgentFix.Door.OWNER] in turn_1.instructions
    assert told()[-1].startswith(fixing_agent.GIVEN_UP[AgentFix.Door.OWNER])


def test_an_agent_fixing_an_owners_note_is_told_giving_up_keeps_the_scenes_clip(
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
    owners_fix(
        fake_model,
        failed_clip,
        film_again(),
        ("give_up", {"reason": "The stir never shows."}),
    )
    [turn_1, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    assert fixing_agent.GAVE_UP[AgentFix.Door.OWNER] in turn_1.instructions
    assert "said to camera" not in turn_1.instructions.split("The app's own prompt rules")[0]
    said = [tool.description for tool in turn_1.tools if tool.name == "give_up"]
    assert said == [fixing_agent.GIVE_UP_FOR_A_NOTE]
    assert told()[-1].startswith(fixing_agent.GIVEN_UP[AgentFix.Door.OWNER])


# --- When the agent stops ------------------------------------------------------------------


def test_an_agent_that_stops_after_a_try_puts_the_scene_back_and_keeps_the_first_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
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


def test_a_step_run_again_after_the_agent_stopped_keeps_the_first_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
        *[OutsideServiceDown("503 from the model")] * 6,
    )
    step = clip_of_scene_2(fake_model, steps, say)
    first = step.produced.order_by("id").first()

    # Run again, it finds the agent stopped with no try passed and no give-up: the first
    # clip stays.
    assert first is not None and work._auto_fixed(step, first).file == first.file
    fixing = AgentFix.objects.get()
    assert (fixing.said_to_camera, fixing.kept_id) == (False, None)
    assert scene_row() == (LINE_2, SHOWS, "", "showcase", "no face")


def test_a_give_up_keeps_the_last_try_as_it_was_filmed_new_line_and_all(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)]),
        turn(calls=[("give_up", {"reason": "The stir never reads."})]),
    )
    made_ready(fake_model, steps, say, (2,))
    # The old line has a fact problem left: try 1's new line, checked, has none.
    Scene.objects.filter(number=2).update(fact_problems=["unsettled"])

    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    step = SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id")[0]
    # The scene is a B-roll scene still, as try 1 filmed it, and try 1 is its clip: it says
    # the scene's line, so the ad is assembled from it with nothing made again.
    fixing = AgentFix.objects.get()
    (try_1,) = [each.clip for each in fixing.tries.all()]
    assert (fixing.said_to_camera, fixing.kept) == (False, try_1)
    assert scene_clip() == try_1
    assert scene_row() == (NEW_LINE, STIRS, "", "showcase", "no face")
    scene = Scene.objects.get(number=2)
    assert (scene.status, scene.fact_checked, scene.fact_problems) == (
        Scene.Status.FINISHED,
        True,
        [],
    )
    assert work._says_the_line_as_it_stands(scene, try_1)
    assert told()[-1].startswith(fixing_agent.GIVEN_UP[AgentFix.Door.JUDGE])
    assert step.status == SceneStep.Status.FINISHED
    assert "so the last try is in the ad" in step.result


# --- When the worker restarts ----------------------------------------------------------------


def test_a_try_the_worker_stopped_while_it_was_judged_is_passed_over_and_the_clip_made_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Try 1, with a new line, is filmed; the worker stops as the judge watches it, so it is
    # no try: its clip is never judged.
    fake_model.respond("check_clip_motion", failing(FLOATS), WorkerStopped())
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(line=NEW_LINE, shows=STIRS, video_prompt=STIRS)])
    )
    with pytest.raises(WorkerStopped):
        step = clip_of_scene_2(fake_model, steps, say)
    step = SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id")[0]
    first = work.clip_tries(step)[0]

    tasks.carry_on_after_the_worker_starts()

    # No try was filmed: the first clip, judged, is kept on top of the unjudged one, with the
    # scene's line as it was, but its step failed, so the scene waits for its clip again, as
    # after any clip step a restart failed (making it again is PR #206's dead-end fix).
    fixing = AgentFix.objects.get()
    assert (fixing.said_to_camera, fixing.gave_up) == (False, work.STOPPED_BY_A_RESTART)
    assert fixing.kept is not None and fixing.kept.file == first.file
    assert "verdict" in (fixing.kept.ruling or {})
    assert scene_clip() == fixing.kept
    assert scene_row() == (LINE_2, SHOWS, "", "showcase", "no face")
    assert Scene.objects.get(number=2).status == Scene.Status.PLANNED
    assert "gave up after 0 tries, so the first clip is in the ad" in work.what_the_judge_did(
        step, fixing.kept
    )


def last_try_kept_after_a_restart() -> AgentFix:
    """Restart the worker; gives how the agent's fixing of scene 2 ended. The step it worked
    in is failed, as every step running is, and its give-up kept: scene 2 keeps its last try,
    finished, still showing the product."""
    tasks.carry_on_after_the_worker_starts()
    fixing = AgentFix.objects.get()
    assert fixing.step.status == SceneStep.Status.FAILED
    assert (fixing.finished_at is not None, fixing.said_to_camera) == (True, False)
    scene = Scene.objects.get(number=2)
    assert (scene.shows != "", scene.status) == (True, Scene.Status.FINISHED)
    assert not work.waits_to_be_said_to_camera(scene)
    assert fixing.kept == scene_clip()
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
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again()]),
        turn(calls=[("give_up", {"reason": "The stir never reads."})]),
    )

    def stopped(*_: object, **__: object) -> None:
        raise WorkerStopped()

    monkeypatch.setattr(work, "_ended", stopped)
    with pytest.raises(WorkerStopped):
        clip_of_scene_2(fake_model, steps, say)
    monkeypatch.undo()

    assert last_try_kept_after_a_restart().gave_up == "The stir never reads."


def test_a_fixing_the_worker_stopped_in_is_given_up_and_the_scene_put_back(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
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

    fixing = last_try_kept_after_a_restart()
    # Try 1, filmed with the new line, is the clip kept: the scene says its line again.
    assert fixing.gave_up and fixing.kept is not None
    assert scene_row()[:2] == (NEW_LINE, STIRS)
    assert work._says_the_line_as_it_stands(Scene.objects.get(number=2), fixing.kept)


def test_the_producer_never_takes_a_try_for_a_clip_already_made(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond("fix_scene", turn(calls=[film_again()]), WorkerStopped())
    with pytest.raises(WorkerStopped):
        clip_of_scene_2(fake_model, steps, say)
    last_try_kept_after_a_restart()
    asked = len(handoffs("make_broll_clip"))

    calling(fake_model, say, ("make_clip", {"scene": 2}))

    # Try 1 is no clip of the scene's to hand back: the scene, with a clip, changes only
    # through the shop owner's notes.
    made = ToolCall.objects.filter(tool="make_clip").latest("id")
    assert "already made" not in made.result
    assert made.result.startswith("Refused: scene 2 already has a clip")
    assert len(handoffs("make_broll_clip")) == asked


# --- Run again after the worker stopped ----------------------------------------------------


def test_a_try_the_worker_stopped_in_is_carried_on_from_its_checkpoint(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    fake_model.respond("check_clip_motion", WorkerStopped())
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    [stopped] = ToolCall.objects.filter(tool="film_again", finished_at__isnull=True)
    clips_paid = paid_for().count("make_broll_clip")

    outcome = fixed(failed_clip)

    # The film_again call left running is run again first, with no turn asked for it, and
    # its clip, fetched already, isn't paid for again.
    stopped.refresh_from_db()
    assert stopped.finished_at is not None
    assert len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"]) == 1
    assert paid_for().count("make_broll_clip") == clips_paid
    assert [each.number for each in outcome.tries] == [1]
    assert outcome.passed == outcome.tries[0].clip


def test_a_new_picture_drawn_before_the_worker_stopped_isnt_drawn_again(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("fix_scene", turn(calls=[a_new_picture()]))
    fake_model.respond("check_starting_picture", WorkerStopped())
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    edits = fake_model.edits

    outcome = fixed(failed_clip)

    assert fake_model.edits == edits
    [made] = outcome.tries
    assert made.picture is not None and made.picture.step is not None
    assert made.picture.step.prompt == PICTURE_PROMPT


def test_no_number_of_turns_stops_the_agent(fake_model: FakeModel, failed_clip: SceneStep) -> None:
    fake_model.respond(
        "fix_scene",
        *[turn(calls=[("look_at_place", {})]) for _ in range(45)],
        turn(calls=[film_again()]),
    )

    outcome = fixed(failed_clip)

    assert len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"]) == 46
    assert outcome.gave_up is None and outcome.passed == outcome.tries[-1].clip


def test_a_give_up_kept_already_is_left_as_it_is_when_the_worker_restarts(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        failing(FLOATS),
        failing(FLOATS),
    )
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again()]),
        turn(calls=[("give_up", {"reason": "The stir never reads."})]),
    )
    step = clip_of_scene_2(fake_model, steps, say)
    kept = AgentFix.objects.get()
    assert (kept.said_to_camera, kept.kept) == (False, scene_clip())

    # The worker restarted after the give-up was kept, before the step finished.
    work.end_the_agents_fixing(step)

    again = AgentFix.objects.get()
    assert again.kept_id == kept.kept_id
    assert scene_clip() == kept.kept


def test_a_new_picture_from_a_photo_the_job_doesnt_have_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    [answer] = refused_then_given_up(
        fake_model,
        failed_clip,
        film_again(new_picture=True, photo=3, picture_prompt="The mug on the counter."),
    )
    assert answer.startswith("Refused: Image 1 must be one of the shop photos: [1, 2].")


def test_the_ad_snapshot_counts_a_fixed_scenes_money_once(new_line_passes: Outcome) -> None:
    session = Scene.objects.get(number=2).job.session
    assert session is not None

    snapshot = ad_as_it_stands(session)

    # Every model call of the scene's work once: its steps', the fixing agent's turns and
    # its try's steps, which share the try's tool call.
    (scene_2,) = [line for line in snapshot.splitlines() if line.startswith("Scene 2:")]
    assert scene_2.endswith("It cost $0.867575.")


def test_a_move_the_safety_filter_blocks_is_answered_as_a_cant_and_the_fix_carries_on(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The loop answers every agent's tool the same (spec #247 point 6): a move the safety
    # filter blocks is a can't the agent reads, never the end of the fix. The decider's tools
    # are settled by the same loop.
    def blocked(*_: object) -> list[ProducedItem]:
        raise BlockedBySafetyFilter("Image processing blocked due to content policy")

    monkeypatch.setattr(work, "edit_scene_place", blocked)
    place = ("place_edit", {"picture": "room", "edit": "Clear the shelf.", "new_place": None})

    outcome = owners_fix(fake_model, failed_clip, place, film_again())

    assert told()[0].startswith("Failed: the AI service's safety filter refused what it was sent")
    assert [each.number for each in outcome.tries] == [1]
