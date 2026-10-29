"""The producer's fix_scene and the one asker (spec #247 points 5, 11 and 14a; ticket #261),
driven through the chat: the shop owner's note about what scene 2's clip shows, the producer
looks, then calls fix_scene, which starts the fix as a background step of its own, with no
fix plan, handing the fixing agent the note word for word, the producer's instruction and
the look's words. How the fix ends reaches the producer as the step's result. The fixing
agent's question comes back to the producer, which asks the shop owner in the chat and hands
their answer back through fix_scene. The producer, the look, the fixing agent, the judge and
the video API are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from adforge import file_store
from agents import fixing_agent, pause, producer
from agents.fold import fold_line
from agents.models import SystemNote, ToolCall
from agents.money import dollars
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.types import Said, StepFinished, ToolUse
from jobs import work
from jobs.library import OwnerPhoto, library_of
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep

from .conftest import CLIP_FIXED, CLIP_MOTION_OK, HeldSteps, picture
from .test_fixing_agent import POURS, STIRS, film_again
from .test_pick_from_tries import tap_use
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import (  # noqa: F401 (fixtures)
    assemble_through_the_chat,
    calling,
    checked,
    made_ready,
)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE = "In scene 2 the brush is the wrong colour: it should be the green one."
INSTRUCTION = "Film scene 2 again with the green brush held the way the page shows it."
LOOKED = {
    "sees": "A hand holds a red brush beside the mug from 0 s to 4 s.",
    "wrong": "The brush is red, from 0 s; the shop owner wants the green one.",
    "root_cause": "The start picture was drawn with a red brush.",
    "direction": "Draw the start picture again with the green brush from the shop photos.",
}


@pytest.fixture
def ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> ProducedItem:
    """The whole ad, made and shown through the chat, scene 2 showing the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


def owner_says(
    fake_model: FakeModel,
    say: Callable[..., None],
    text: str,
    *turns: Any,
    photos: list[tuple[str, bytes]] | None = None,
) -> None:
    """The shop owner's message once the ad has a clip, with any `photos`, answered by the
    producer with `turns`."""
    fake_model.respond("produce", *turns)
    say(text, *(photos or []))


def looks_then_fixes(fake_model: FakeModel, say: Callable[..., None]) -> None:
    """The shop owner's note on scene 2: the producer looks at the clip, then fixes it."""
    fake_model.respond("look", LOOKED)
    owner_says(
        fake_model,
        say,
        NOTE,
        turn(
            calls=[
                (
                    "look",
                    {"scene": 2, "what": "clip", "number": None, "question": "Which brush?"},
                )
            ]
        ),
        turn(calls=[("fix_scene", {"scene": 2, "instruction": INSTRUCTION})]),
        turn(says="I'm fixing scene 2."),
    )


def fix_step() -> SceneStep:
    """Scene 2's newest fix step: the one fix_scene started."""
    return (
        SceneStep.objects.filter(kind="clip", scene__number=2).exclude(owner_note="").latest("id")
    )


def fails_the_judge(problem: str) -> dict[str, Any]:
    """MOTION's answer on a try that fails the judge, the shop owner's change made."""
    return {**failing(problem), "owner_change": CLIP_FIXED}


def told_of_the_step(fake_model: FakeModel) -> str:
    """The newest step result the producer was given."""
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    return [
        each.text
        for each in produced[-1].handoff.conversation
        if isinstance(each, StepFinished) and each.text.startswith("Background step")
    ][-1]


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def test_fix_scene_hands_the_note_the_instruction_and_the_look_to_the_fixing_agent(
    fake_model: FakeModel, ad: ProducedItem, steps: HeldSteps, say: Callable[..., None]
) -> None:
    looks_then_fixes(fake_model, say)

    [started] = ToolCall.objects.filter(tool="fix_scene", agent="producer")
    assert started.result.startswith("Started scene 2's fix"), started.result
    # A step of its own, holding what the agent is handed.
    step = fix_step()
    assert step.pk in steps.held
    assert (step.owner_note, step.fix_goal) == (NOTE, INSTRUCTION)
    assert LOOKED["root_cause"] in step.look_words

    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=STIRS)]))
    run(fake_model, steps)

    # The agent's first message: the note word for word, the instruction, the look's words.
    [first] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    intro = first.handoff.conversation[0]
    assert isinstance(intro, Said)
    assert f'"{NOTE}"' in intro.text
    assert INSTRUCTION in intro.text
    assert LOOKED["root_cause"] in intro.text and LOOKED["direction"] in intro.text
    # One try, paid once; it passed and is scene 2's clip.
    fixing = AgentFix.objects.get(step=step)
    [made] = fixing.tries.all()
    assert fixing.passed == made.clip and made.clip is not None
    assert work.versions(Scene.objects.get(number=2))[-1].clip == made.clip
    assert told_of_the_step(fake_model).startswith(
        "Background step finished: Scene 2's fix passed: the fixing agent filmed 1 try, and "
        "try 1 passed the clip judge with the change made."
    )
    assert Job.objects.get().produced.filter(kind="finished_ad").count() == 1


def test_a_fix_whose_tries_run_out_keeps_its_last_try_and_shows_every_try_to_swap(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fixing_agent, "MOST_TRIES", 2)
    looks_then_fixes(fake_model, say)
    fake_model.respond("check_clip_motion", *[fails_the_judge(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[film_again(video_prompt=POURS)]),
    )
    run(fake_model, steps)

    step = fix_step()
    fixing = AgentFix.objects.get(step=step)
    assert fixing.ended_by_cap and fixing.passed is None
    first, last = (each.clip for each in fixing.tries.order_by("number"))
    assert first is not None and last is not None
    # The last try is in the ad, as the scene's newest version.
    assert work.versions(Scene.objects.get(number=2))[-1].clip == last
    # The shop owner is told so, with every try to tap.
    [shown] = Message.objects.filter(tries_of=step)
    assert shown.text == (
        "None of the 2 tries at scene 2's clip both passed the clip judge and made your "
        "change, and the fixing agent filmed as many tries as it may, so the last try is in "
        "the ad. Watch them, and tap one to use it instead."
    )
    told = told_of_the_step(fake_model)
    assert "Scene 2's fix ended with no try passing (2 tries)" in told
    assert "the last try is in the ad" in told

    # The tap reaches the producer as the shop owner's message: it keeps that try.
    fake_model.respond(
        "produce",
        turn(calls=[("keep_try", {"scene": 2, "try_number": 1})]),
        turn(says="Swapped."),
    )
    tapped = tap_use(api, session_id, first)
    assert tapped.status_code == 201, tapped.json()
    assert work.versions(Scene.objects.get(number=2))[-1].clip.file == first.file  # type: ignore[union-attr]


QUESTION = "Could you send a photo of the green brush?"
ANSWER = "Here it is, the green one."
WITH_IT = "Film the brush as the shop owner's photo shows it."


def test_a_fix_the_fixing_agent_gives_up_keeps_its_last_try_as_the_agent_is_told(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # One rule for both doors (spec #247 point 5, Julian 03:58): the last try stays.
    looks_then_fixes(fake_model, say)
    fake_model.respond("check_clip_motion", fails_the_judge(FLOATS))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[("give_up", {"reason": "The brush never reads green."})]),
    )
    run(fake_model, steps)

    fixing = AgentFix.objects.get(step=fix_step())
    assert fixing.gave_up == "The brush never reads green." and fixing.passed is None
    [last] = (each.clip for each in fixing.tries.all())
    assert work.versions(Scene.objects.get(number=2))[-1].clip == last
    # The agent was told so, in its instructions, its give_up tool and give_up's answer.
    [first, *_] = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    assert fixing_agent.GAVE_UP in first.instructions
    [give_up] = [spec for spec in first.tools if spec.name == "give_up"]
    assert "its last try stays in the ad" in give_up.description
    answered = ToolCall.objects.filter(agent="fixing_agent", tool="give_up").get().result
    assert answered == fixing_agent.GIVEN_UP
    assert "the last try is in the ad" in told_of_the_step(fake_model)
    # What the fix cost, the agent's turns and tries included, in its result and in its
    # call's folded line.
    step = fix_step()
    assert f"It cost {dollars(step.cost_usd())}." in told_of_the_step(fake_model)
    assert fold_line(ToolCall.objects.get(tool="fix_scene")).endswith(
        f"; cost {dollars(step.cost_usd())}."
    )
    assert step.cost_usd() > 0


def test_the_fixing_agents_question_is_asked_by_the_producer_and_the_answer_resumes_the_fix(
    fake_model: FakeModel, ad: ProducedItem, steps: HeldSteps, say: Callable[..., None]
) -> None:
    looks_then_fixes(fake_model, say)
    fake_model.respond("check_clip_motion", fails_the_judge(FLOATS))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[("ask_owner", {"question": QUESTION, "wants": "photo"})]),
    )
    # Woken by the parked fix, the producer asks the shop owner in the chat.
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))
    steps.run_held()

    step = fix_step()
    question = work.open_question(step.scene)
    assert question is not None and question.question == QUESTION
    told = told_of_the_step(fake_model)
    assert "Scene 2's fix waits after 1 try, so the last try is in the ad meanwhile." in told
    assert f'The fixing agent asks: "{QUESTION}"' in told
    assert "call fix_scene for scene 2 to hand their answer back" in told
    asked = Message.objects.filter(role=Message.Role.AGENT, text=QUESTION)
    assert asked.count() == 1

    # The shop owner answers in the chat with a photo; the producer hands it back.
    owner_says(
        fake_model,
        say,
        ANSWER,
        turn(calls=[("fix_scene", {"scene": 2, "instruction": WITH_IT})]),
        turn(says="Thanks, the fix carries on."),
        photos=[("green.png", picture(40, 30, (20, 160, 40)))],
    )
    [photo] = Attachment.objects.filter(message__text=ANSWER)
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS)]))
    # Told the fix passed, the producer assembles the ad: code doesn't too.
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))
    steps.run_held()

    # The photo is in the library, as the shop owner's photo, and the agent read the answer.
    job = Job.objects.get()
    assert [each for each in library_of(job) if each.source == OwnerPhoto(original=photo.file)]
    turns = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    [ask_read] = [
        each
        for each in turns[-1].handoff.conversation
        if isinstance(each, ToolUse) and each.tool == "ask_owner"
    ]
    assert ANSWER in ask_read.result and WITH_IT in ask_read.result
    assert photo.file in [each.key for each in ask_read.pictures]
    # The fix carried on and passed; the question is answered.
    fixing = AgentFix.objects.get(step=step)
    assert fixing.passed is not None and fixing.passed == fixing.tries.get(number=2).clip
    assert work.open_question(step.scene) is None
    # One new ad, the producer's, with the passing try: never a second one made by code.
    ads = Job.objects.get().produced.filter(kind="finished_ad").order_by("version")
    assert [each.pk for each in ads][0] == ad.pk and ads.count() == 2
    assert {cut["scene"]: cut["clip"] for cut in ads.last().cuts}[2] == fixing.passed.pk  # type: ignore[union-attr]


def test_a_pause_lets_the_try_finish_parks_the_fix_and_the_producer_carries_it_on(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)
    session = Session.objects.get(pk=session_id)

    def paused_while_judged() -> dict[str, Any]:
        pause.pause(session, wake=False)
        return fails_the_judge(FLOATS)

    fake_model.respond("check_clip_motion", paused_while_judged)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        # Never taken while the pause holds.
        turn(calls=[film_again(video_prompt=POURS)]),
    )
    fake_model.respond("produce", turn(says="Paused. Scene 2's fix stopped between tries."))
    steps.run_held()

    step = fix_step()
    fixing = AgentFix.objects.get(step=step)
    # The try being filmed finished and was judged; no next try started.
    [made] = fixing.tries.all()
    assert made.clip is not None and made.clip.ruling["verdict"] == "fail"  # type: ignore[index]
    assert len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"]) == 1
    assert fixing.paused_at is not None
    assert work.versions(Scene.objects.get(number=2))[-1].clip == made.clip
    # The pause note named it, and the producer was told how it stopped.
    [note] = SystemNote.objects.filter(kind=SystemNote.Kind.PAUSED)
    assert "scene 2's clip, from fix_scene" in note.text
    assert "carry it on with fix_scene" in note.text
    told = told_of_the_step(fake_model)
    assert "Scene 2's fix stopped after 1 try for the shop owner's pause" in told
    # Not yet a change to tell the shop owner of: it waits for them.
    assert "Once the new ad is shown" not in told
    # The chat says so in one grey line on the scene's clip card, with no tries to tap.
    [stopped] = Message.objects.filter(text__startswith="Scene 2's fix stopped between tries")
    assert (stopped.level, stopped.step_scene, stopped.step_kind, stopped.step_state) == (
        "info",
        2,
        "clip",
        "made",
    )
    assert stopped.tries_of is None

    # After the shop owner's message the producer carries it on.
    owner_says(
        fake_model,
        say,
        "Go on with the brush.",
        turn(calls=[("fix_scene", {"scene": 2, "instruction": "Carry on with the brush."})]),
        turn(says="Carrying on."),
    )
    # Its step runs again from where it stopped, the chat told so.
    step.refresh_from_db()
    assert (step.status, step.finished_at) == ("running", None)
    assert Message.objects.filter(text="Scene 2's fix carries on.").exists()
    run(fake_model, steps)

    fixing.refresh_from_db()
    assert fixing.paused_at is None and fixing.resumed_at is not None
    assert fixing.passed == fixing.tries.get(number=2).clip
    assert "Scene 2's fix passed" in told_of_the_step(fake_model)
    turns = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    said = [each.text for each in turns[-1].handoff.conversation if isinstance(each, Said)]
    assert any(
        each.startswith(fixing_agent.CARRIED_ON) and "Carry on with the brush." in each
        for each in said
    )
    # Said as code's word, the user's, after the try filmed before the pause, and no reminder
    # to call a tool follows it.
    conversation = turns[-1].handoff.conversation
    [at] = [
        number
        for number, each in enumerate(conversation)
        if isinstance(each, Said) and each.text.startswith(fixing_agent.CARRIED_ON)
    ]
    assert conversation[at].by == "user"
    assert any(isinstance(each, ToolUse) for each in conversation[:at])
    assert not [
        each
        for each in conversation[at + 1 : at + 2]
        if isinstance(each, Said) and each.text == fixing_agent.REMINDER
    ]


def test_a_try_that_passes_as_the_pause_is_pressed_ends_the_fix_passed_not_parked(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)
    session = Session.objects.get(pk=session_id)

    def paused_while_judged() -> dict[str, Any]:
        pause.pause(session, wake=False)
        return {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}

    fake_model.respond("check_clip_motion", paused_while_judged)
    # A second call in the same turn, after the try that passes.
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=STIRS), film_again(video_prompt=POURS)])
    )
    fake_model.respond("produce", turn(says="Scene 2 is fixed."))
    steps.run_held()

    fixing = AgentFix.objects.get(step=fix_step())
    assert fixing.passed == fixing.tries.get(number=1).clip
    assert fixing.paused_at is None
    assert "Scene 2's fix passed" in told_of_the_step(fake_model)


def test_a_fix_carried_on_after_the_pause_that_gives_up_keeps_its_newest_try(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)
    session = Session.objects.get(pk=session_id)

    def paused_while_judged() -> dict[str, Any]:
        pause.pause(session, wake=False)
        return fails_the_judge(FLOATS)

    fake_model.respond("check_clip_motion", paused_while_judged, fails_the_judge(FLOATS))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[("give_up", {"reason": "The brush never reads green."})]),
    )
    fake_model.respond("produce", turn(says="Paused. Scene 2's fix stopped between tries."))
    steps.run_held()
    owner_says(
        fake_model,
        say,
        "Go on with the brush.",
        turn(calls=[("fix_scene", {"scene": 2, "instruction": "Carry on with the brush."})]),
        turn(says="Carrying on."),
    )
    run(fake_model, steps)

    fixing = AgentFix.objects.get(step=fix_step())
    assert fixing.gave_up == "The brush never reads green."
    # The try filmed after the pause is in the ad, not the one the pause left there.
    assert work.versions(Scene.objects.get(number=2))[-1].clip == fixing.tries.get(number=2).clip


def test_a_second_fix_after_one_that_gave_up_starts_afresh(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    looks_then_fixes(fake_model, say)
    fake_model.respond("check_clip_motion", fails_the_judge(FLOATS))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[("give_up", {"reason": "The brush never reads green."})]),
    )
    run(fake_model, steps)
    first = fix_step()

    owner_says(
        fake_model,
        say,
        "Still the wrong brush: try again.",
        turn(calls=[("fix_scene", {"scene": 2, "instruction": INSTRUCTION})]),
        turn(says="Fixing scene 2 again."),
    )

    answer = ToolCall.objects.filter(tool="fix_scene").latest("id").result
    assert answer.startswith("Started scene 2's fix")
    assert fix_step() != first


def a_fix_the_pause_parked(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], session_id: str
) -> AgentFix:
    """Scene 2's fix, parked by a pause pressed while its first try was judged."""
    looks_then_fixes(fake_model, say)
    session = Session.objects.get(pk=session_id)

    def paused_while_judged() -> dict[str, Any]:
        pause.pause(session, wake=False)
        return fails_the_judge(FLOATS)

    fake_model.respond("check_clip_motion", paused_while_judged)
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=STIRS)]))
    fake_model.respond("produce", turn(says="Paused. Scene 2's fix stopped between tries."))
    steps.run_held()
    fixing = AgentFix.objects.get(step=fix_step())
    assert fixing.paused_at is not None
    return fixing


CARRY_ON = ("fix_scene", {"scene": 2, "instruction": "Carry on with the brush."})


def test_a_parked_fix_whose_scene_moved_past_it_is_started_afresh(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    parked = a_fix_the_pause_parked(fake_model, steps, say, session_id)

    # The scene's first version is brought back first: the try the fix left is no longer
    # the scene's newest.
    owner_says(
        fake_model,
        say,
        "Go back to the first one, then fix the brush.",
        turn(calls=[("use_version", {"scene": 2, "version": 1})]),
        turn(calls=[CARRY_ON]),
        turn(says="Fixing scene 2 afresh."),
    )

    answer = ToolCall.objects.filter(tool="fix_scene").latest("id").result
    assert answer.startswith("Started scene 2's fix")
    parked.refresh_from_db()
    assert parked.paused_at is None


def test_a_parked_fix_isnt_carried_on_while_its_scenes_clip_is_made(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    a_fix_the_pause_parked(fake_model, steps, say, session_id)

    owner_says(
        fake_model,
        say,
        "Make it slower, and fix the brush.",
        turn(calls=[("make_clip", {"scene": 2, "note": "A slower stir."}), CARRY_ON]),
        turn(says="Making scene 2's clip."),
    )

    answer = ToolCall.objects.filter(tool="fix_scene").latest("id").result
    assert answer.startswith("Refused: scene 2's clip is being made.")


def test_a_pause_pressed_again_as_a_parked_fix_is_carried_on_holds_it(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parked = a_fix_the_pause_parked(fake_model, steps, say, session_id)
    found = producer._paused_fix

    def pressed_meanwhile(scene: Scene) -> AgentFix | None:
        # The tool was let run before the pause, pressed again as it carries the fix on.
        pause.pause(Session.objects.get(pk=session_id), wake=False)
        return found(scene)

    monkeypatch.setattr(producer, "_paused_fix", pressed_meanwhile)
    owner_says(fake_model, say, "Go on with the brush.", turn(calls=[CARRY_ON]), turn(says="Ok."))

    answer = ToolCall.objects.filter(tool="fix_scene").latest("id").result
    assert answer.startswith("Refused: paused")
    parked.refresh_from_db()
    assert parked.paused_at is not None


def test_a_fix_carried_on_and_paused_again_before_the_worker_takes_it_is_cancelled(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    a_fix_the_pause_parked(fake_model, steps, say, session_id)
    owner_says(fake_model, say, "Go on with the brush.", turn(calls=[CARRY_ON]), turn(says="Ok."))

    pause.pause(Session.objects.get(pk=session_id), wake=False)

    assert fix_step().status == "cancelled"


def the_pause_note() -> str:
    [note] = SystemNote.objects.filter(kind=SystemNote.Kind.PAUSED)
    return note.text


def test_a_fix_the_worker_took_is_named_in_the_pause_note_as_parking_between_tries(
    fake_model: FakeModel,
    ad: ProducedItem,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)
    # Taken by the worker, its fixing agent not started yet.
    now = timezone.now()
    SceneStep.objects.filter(pk=fix_step().pk).update(taken_at=now, seen_at=now)

    pause.pause(Session.objects.get(pk=session_id), wake=False)

    _, running = the_pause_note().split("Still running")
    assert "scene 2's clip, from fix_scene" in running
    assert "carry it on with fix_scene for scene 2." in running


def test_a_fix_the_pause_cancelled_is_never_said_to_be_filming(
    fake_model: FakeModel,
    ad: ProducedItem,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)

    pause.pause(Session.objects.get(pk=session_id), wake=False)

    assert fix_step().status == "cancelled"
    cancelled, _ = the_pause_note().split("Still running")
    assert "scene 2's clip, from fix_scene" in cancelled
    assert "fixing agent" not in cancelled


def test_the_chat_takes_a_video_as_it_takes_a_photo(
    fake_model: FakeModel, api: APIClient, session_id: str
) -> None:
    fake_model.respond("produce", turn(says="Thanks for the video."))
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {"text": "How it moves", "videos": [SimpleUploadedFile("lid.mp4", b"0", "video/mp4")]},
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()
    [kept] = Attachment.objects.filter(message__text="How it moves")
    assert kept.kind == Attachment.Kind.VIDEO and kept.file.endswith(".mp4")
    assert file_store.read(kept.file) == b"0"

    refused = api.post(
        f"/api/sessions/{session_id}/messages/",
        {"text": "And this", "videos": [SimpleUploadedFile("lid.txt", b"0", "text/plain")]},
        format="multipart",
    )
    assert refused.status_code == 400
    assert "isn't an MP4, MOV or WebM video" in str(refused.json())


def test_a_pause_pressed_while_the_fixing_agent_decides_starts_no_try(
    fake_model: FakeModel,
    ad: ProducedItem,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    looks_then_fixes(fake_model, say)
    session = Session.objects.get(pk=session_id)
    fake_model.respond(
        "fix_scene",
        meanwhile(
            lambda: pause.pause(session, wake=False),
            turn(calls=[film_again(video_prompt=STIRS)]),
        ),
    )
    fake_model.respond("produce", turn(says="Paused."))
    steps.run_held()

    fixing = AgentFix.objects.get(step=fix_step())
    assert fixing.paused_at is not None and not fixing.tries.exists()
    assert [
        each.result for each in ToolCall.objects.filter(agent="fixing_agent", tool="film_again")
    ] == [f"Refused: {fixing_agent.PAUSED} Nothing was done."]
    # Nothing was filmed: the clip the shop owner noted is still the scene's.
    assert work.versions(Scene.objects.get(number=2))[-1].clip == fixing.first_clip


def test_a_note_older_than_the_latest_ad_is_still_the_fixs_note(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    # The producer assembles the ad before it fixes, so the owner's note is older than
    # the latest ad; it is still the note the fix gets.
    fake_model.respond("look", LOOKED)
    fake_model.respond(
        "produce",
        turn(calls=[("assemble_ad", {})]),
        turn(calls=[("look", {"scene": 2, "what": "clip", "number": None, "question": "?"})]),
        turn(calls=[("fix_scene", {"scene": 2, "instruction": INSTRUCTION})]),
        turn(says="Here's your ad. I'm fixing scene 2."),
    )
    say(NOTE)

    ad = Job.objects.get().produced.get(kind="finished_ad")
    assert ad.created_at > Message.objects.get(text=NOTE).created_at
    [started] = ToolCall.objects.filter(tool="fix_scene", agent="producer")
    assert started.result.startswith("Started scene 2's fix"), started.result
    assert fix_step().owner_note == NOTE
