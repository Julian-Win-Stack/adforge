"""The fixing agent's ask_owner move (spec #210 point 23, ticket #238): as its last resort the
agent asks the shop owner its own question; code parks the fix, the scene keeps its last
try, the ad finishes, the question is stored on the scene and the chat says so in one red
line. The agent, the judge, the pictures and the clips are faked at the gateway."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents import fixing_agent
from agents.loop import ToolResult
from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.types import ToolUse
from jobs import work
from jobs.models import AgentFix, Scene, SceneStep

from .conftest import HeldSteps, WorkerStopped
from .test_fixing_agent import POURS, failed_clip, film_again, fixed, told  # noqa: F401
from .test_producer_auto_fix import FLOATS, clip_of_scene_2, failing
from .test_producer_broll import assemble_through_the_chat, checked  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

QUESTION = "Could you send a short video of the lid being screwed onto the mug?"


def ask(question: str = QUESTION, wants: str = "video") -> tuple[str, dict[str, str]]:
    return ("ask_owner", {"question": question, "wants": wants})


def fix_turns(fake_model: FakeModel) -> int:
    return len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"])


def test_an_ask_parks_the_fix_and_the_ad_finishes_with_the_last_try(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        # Never taken: the fix is parked.
        turn(calls=[("give_up", {"reason": "No answer came."})]),
    )

    ad = assemble_through_the_chat(fake_model, steps, say)

    assert fix_turns(fake_model) == 2
    fixing = AgentFix.objects.get()
    last_try = fixing.tries.get(number=1).clip
    assert last_try is not None
    # The record: parked with its question, on the scene, the ask awaiting its result.
    question = work.open_question(fixing.step.scene)
    assert question is not None
    assert (question.agent_fix, question.question, question.wants) == (fixing, QUESTION, "video")
    assert question.tool_call.tool == "ask_owner" and question.tool_call.finished_at is None
    assert work.is_parked(fixing)
    # The scene's last try is in the finished ad.
    assert {cut["scene"]: cut["clip"] for cut in ad.cuts}[2] == last_try.pk
    # The chat shows one red line, pointing at the scene's card.
    problems = Message.objects.filter(role=Message.Role.NOTICE, level=Message.Level.PROBLEM)
    assert [each.text for each in problems if "question" in each.text] == [
        "The fixing agent needs something from you for scene 2's clip, so the last try is in "
        "the ad until it has it. Its question is on scene 2's card."
    ]
    # The give-up notice with every try isn't shown for a parked fix.
    assert not Message.objects.filter(tries_of=fixing.step).exists()
    # The producer is told so too.
    step = fixing.step
    step.refresh_from_db()
    assert (
        "and after 1 try the fixing agent asked the shop owner a question about it, shown on "
        "the scene's card, so the last try is in the ad until they answer it. Parked: the fix "
        f'needs a video from the shop owner. The fixing agent asks: "{QUESTION}" Ask the shop '
        "owner with ask_owner, then, once they answer, call fix_scene for scene 2 to hand their "
        "answer back to the fix."
    ) in step.result


def asked_on_the_first_turn(fake_model: FakeModel, step: SceneStep, *more: object) -> AgentFix:
    fake_model.respond("fix_scene", turn(calls=[ask(), *more]))  # type: ignore[list-item]
    fixed(step)
    return AgentFix.objects.get()


def test_a_call_after_the_ask_in_its_turn_is_refused_while_the_fix_waits(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fixing = asked_on_the_first_turn(fake_model, failed_clip, film_again())

    assert fixing.tries.count() == 0
    assert told() == [
        "",
        "Refused: the fix waits for the shop owner's answer to your question. Nothing was done.",
    ]


def test_a_worker_restart_leaves_the_parked_fix_parked(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    asked_on_the_first_turn(fake_model, failed_clip)
    turns = fix_turns(fake_model)

    again = fixed(failed_clip)

    assert fix_turns(fake_model) == turns
    assert again.parked and again.gave_up == fixing_agent.ASKED_THE_OWNER
    assert work.open_question(failed_clip.scene) is not None
    assert ToolCall.objects.get(tool="ask_owner").finished_at is None


def test_a_step_a_restart_failed_after_an_ask_with_a_try_keeps_the_question_open(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=POURS)]), turn(calls=[ask()])
    )
    fixed(failed_clip)

    work.end_the_agents_fixing(failed_clip)

    failed_clip.scene.refresh_from_db()
    assert failed_clip.scene.status != Scene.Status.PLANNED
    assert work.open_question(failed_clip.scene) is not None


def test_a_step_a_restart_failed_after_an_ask_with_no_try_withdraws_the_question(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    asked_on_the_first_turn(fake_model, failed_clip)
    work.say_the_agent_gave_up(failed_clip)

    def questions_shown() -> list[object]:
        shown = api.get(f"/api/sessions/{session_id}/messages/").json()
        return [each["question"] for each in shown if each["question"] is not None]

    assert len(questions_shown()) == 1

    work.end_the_agents_fixing(failed_clip)

    # No card shows the withdrawn question.
    assert questions_shown() == []

    # The scene's clip is made again, so no card shows a question for the fix that failed.
    failed_clip.scene.refresh_from_db()
    assert failed_clip.scene.status == Scene.Status.PLANNED
    assert work.open_question(failed_clip.scene) is None
    assert not work.is_parked(AgentFix.objects.get())


def test_an_ask_the_worker_stopped_before_settling_parks_the_fix_on_its_restart(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    park = fixing_agent._park

    def stops_once(*args: object) -> None:
        monkeypatch.setattr(fixing_agent, "_park", park)
        raise WorkerStopped()

    monkeypatch.setattr(fixing_agent, "_park", stops_once)
    fake_model.respond("fix_scene", turn(calls=[ask()]))
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

    outcome = fixed(failed_clip)

    assert outcome.parked
    assert fix_turns(fake_model) == 1


def test_the_answer_is_the_asks_result_and_a_second_ask_is_refused_with_the_reason(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    asked_on_the_first_turn(fake_model, failed_clip)
    question = work.open_question(failed_clip.scene)
    assert question is not None
    answer = "There is no video; the lid screws on clockwise, a quarter turn."

    fixing_agent.answer_question(question, ToolResult(answer, []))
    fake_model.respond("fix_scene", turn(calls=[ask("Is it a quarter turn?")]))
    fake_model.respond("fix_scene", turn(calls=[film_again()]))
    outcome = fixed(failed_clip)

    # The agent read the answer as ask_owner's result, on the turn the fix resumed.
    resumed = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"][1]
    [ask_read] = [
        each
        for each in resumed.handoff.conversation
        if isinstance(each, ToolUse) and each.tool == "ask_owner"
    ]
    assert ask_read.result == answer
    assert told()[1] == (
        "Refused: you asked the shop owner once in this scene's fix already, and that is all: "
        "carry on with what you have. Nothing was done."
    )
    assert work.open_question(failed_clip.scene) is None
    assert (outcome.parked, outcome.passed) == (False, outcome.tries[0].clip)


def test_an_ask_with_no_question_is_refused_and_parks_nothing(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fix_scene", turn(calls=[ask(question="")]), turn(calls=[film_again()]))

    outcome = fixed(failed_clip)

    assert told()[0].startswith("Refused: it was called with arguments it can't use")
    assert not outcome.parked and outcome.passed is not None


def test_an_agent_fixing_an_owners_note_has_no_ask_and_is_told_never_to_ask() -> None:
    told_at = {door: fixing_agent.instructions(door) for door in ("judge", "owner")}
    tools_at = {door: [spec.name for spec in fixing_agent._tools(door)] for door in told_at}

    assert "ask_owner" in tools_at["judge"] and "ask_owner" not in tools_at["owner"]
    assert "call ask_owner" in told_at["judge"]
    assert "Never ask the shop owner anything." in told_at["owner"]
    assert "call ask_owner" not in told_at["owner"]


def test_an_agent_fixing_an_owners_note_that_asks_anyway_is_refused_and_parks_nothing(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    from .test_fixing_agent import owners_fix

    outcome = owners_fix(fake_model, failed_clip, ask(), film_again())

    assert told()[0] == "Refused: the shop owner can't be asked in this fix. Nothing was done."
    assert not outcome.parked
    assert work.open_question(failed_clip.scene) is None
    assert [each.number for each in outcome.tries] == [1]


def test_the_producer_is_told_plainly_of_an_ask_before_any_try(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS))
    fake_model.respond("fix_scene", turn(calls=[ask()]))

    step = clip_of_scene_2(fake_model, steps, say)

    step.refresh_from_db()
    assert (
        "and before filming a try the fixing agent asked the shop owner a question about it, "
        "shown on the scene's card, so the first clip is in the ad until they answer it."
    ) in step.result


def test_the_chat_carries_the_open_question_for_its_scenes_card(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=POURS)]), turn(calls=[ask()])
    )
    assemble_through_the_chat(fake_model, steps, say)

    def carried() -> list[tuple[str, dict[str, object]]]:
        shown = api.get(f"/api/sessions/{session_id}/messages/").json()
        return [(each["text"], each["question"]) for each in shown if each["question"] is not None]

    # Only the red line about the question carries it, open, in the agent's words.
    [(text, shown)] = carried()
    assert "Its question is on scene 2's card." in text
    assert shown == {"scene": 2, "text": QUESTION, "open": True}

    question = work.open_question(AgentFix.objects.get().step.scene)
    assert question is not None
    fixing_agent.answer_question(question, ToolResult("A quarter turn clockwise.", []))

    # Once answered, the card no longer shows it as waiting.
    assert carried()[0][1]["open"] is False
