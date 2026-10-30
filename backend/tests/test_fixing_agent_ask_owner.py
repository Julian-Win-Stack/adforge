"""The fixing agent's ask_owner move (spec #210 point 23, ticket #238, as the round 7 spec's
section 4 changed them): as its last resort the agent asks the shop owner its own question,
for a photo or a video; the question is stored on the scene and put to the shop owner once,
in plain words, the producer is told, and the fix keeps trying: the ad never waits for the
answer. The agent, the judge, the pictures and the clips are faked at the gateway."""

from collections.abc import Callable

import pytest
from django.db.models.signals import pre_save
from rest_framework.test import APIClient

from agents import fixing_agent
from agents.models import SystemNote, ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import owner_answers, work
from jobs.models import AgentFix, OwnerQuestion, Scene, SceneStep

from .conftest import HeldSteps, WorkerStopped
from .test_fixing_agent import POURS, failed_clip, film_again, fixed, told  # noqa: F401
from .test_producer_auto_fix import FLOATS, clip_of_scene_2, failing
from .test_producer_broll import assemble_through_the_chat, checked  # noqa: F401 (a fixture)
from .test_producer_restarts import WorkerStopped as Stopped
from .test_producer_restarts import the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)

QUESTION = "Could you send a short video of the lid being screwed onto the mug?"


def ask(question: str = QUESTION, wants: str = "video") -> tuple[str, dict[str, str]]:
    return ("ask_owner", {"question": question, "wants": wants})


def fix_turns(fake_model: FakeModel) -> int:
    return len([each for each in fake_model.turns_taken if each.purpose == "fix_scene"])


def test_an_ask_after_try_1_is_followed_by_try_2_and_the_ad_finishes_with_it_unanswered(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # The first clip and try 1 fail the judge; try 2 passes.
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        turn(calls=[film_again()]),
    )

    ad = assemble_through_the_chat(fake_model, steps, say)

    # The agent was told at once that the fix doesn't wait, and filmed try 2.
    fixing = AgentFix.objects.get()
    assert told()[1] == fixing_agent.ASKED
    assert fix_turns(fake_model) == 3
    passed = fixing.tries.get(number=2).clip
    assert fixing.passed == passed and passed is not None
    # The question is kept on the scene, open, and the ad was made with it unanswered.
    question = work.open_question(fixing.step.scene)
    assert question is not None
    assert (question.agent_fix, question.question, question.wants) == (fixing, QUESTION, "video")
    assert {cut["scene"]: cut["clip"] for cut in ad.cuts}[2] == passed.pk
    # The shop owner was asked once, in plain words, never in red.
    asked = Message.objects.filter(role=Message.Role.NOTICE, text__contains=QUESTION)
    assert [(each.level, each.owner_question_id) for each in asked] == [
        (Message.Level.INFO, question.pk)
    ]
    assert not Message.objects.filter(level=Message.Level.PROBLEM).exists()
    # The producer was told of the question, so it doesn't ask it again.
    [note] = SystemNote.objects.filter(kind=SystemNote.Kind.AGENT_ASKED)
    assert QUESTION in note.text and "scene 2" in note.text


def asked_on_the_first_turn(fake_model: FakeModel, step: SceneStep, *more: object) -> AgentFix:
    """The agent asks on its first turn, with `more` calls in that turn, and films on its
    next, which passes."""
    fake_model.respond(
        "fix_scene",
        turn(calls=[ask(), *more]),  # type: ignore[list-item]
        turn(calls=[film_again()]),
    )
    fixed(step)
    return AgentFix.objects.get()


def test_a_call_after_the_ask_in_its_turn_runs_as_the_fix_goes_on(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fix_scene", turn(calls=[ask(), film_again()]))

    outcome = fixed(failed_clip)

    assert told()[0] == fixing_agent.ASKED
    assert outcome.passed is not None and outcome.passed == outcome.tries[0].clip


def test_an_ask_the_worker_stopped_before_its_call_was_kept_asks_once_and_carries_on(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fix_scene", turn(calls=[ask()]), turn(calls=[film_again()]))
    # It stops once the question is in the chat, before the ask's call is kept as finished.
    stops = the_worker_stops(
        pre_save, ToolCall, when=lambda call: call.tool == "ask_owner" and call.finished_at
    )
    with stops, pytest.raises(Stopped):
        fixed(failed_clip)

    outcome = fixed(failed_clip)

    assert outcome.passed is not None
    assert told()[0] == fixing_agent.ASKED
    assert OwnerQuestion.objects.count() == 1
    assert Message.objects.filter(text__contains=QUESTION).count() == 1
    assert SystemNote.objects.filter(kind=SystemNote.Kind.AGENT_ASKED).count() == 1


def test_a_step_a_restart_failed_after_an_ask_with_a_try_keeps_the_question_open(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        WorkerStopped(),
    )
    with pytest.raises(WorkerStopped):
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
    fake_model.respond("fix_scene", turn(calls=[ask()]), WorkerStopped())
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)

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


def test_a_second_ask_is_refused_with_the_reason_and_the_fix_goes_on(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[ask()]),
        turn(calls=[ask("Is it a quarter turn?")]),
        turn(calls=[film_again()]),
    )

    outcome = fixed(failed_clip)

    assert told()[1] == (
        "Refused: you asked the shop owner once in this scene's fix already, and that is all: "
        "carry on with what you have. Nothing was done."
    )
    assert OwnerQuestion.objects.count() == 1
    assert outcome.passed is not None


def test_an_ask_with_no_question_or_for_words_is_refused_and_asks_nothing(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[ask(question="")]),
        turn(calls=[ask(wants="words")]),
        turn(calls=[film_again()]),
    )

    outcome = fixed(failed_clip)

    assert told()[0].startswith("Refused: it was called with arguments it can't use")
    assert told()[1].startswith("Refused: it was called with arguments it can't use")
    assert not OwnerQuestion.objects.exists() and outcome.passed is not None


def test_an_agent_fixing_an_owners_note_has_no_ask_and_is_told_never_to_ask() -> None:
    told_at = {door: fixing_agent.instructions(door) for door in ("judge", "owner")}
    tools_at = {door: [spec.name for spec in fixing_agent._tools(door)] for door in told_at}

    assert "ask_owner" in tools_at["judge"] and "ask_owner" not in tools_at["owner"]
    assert "call ask_owner" in told_at["judge"]
    assert "Never ask the shop owner anything." in told_at["owner"]
    assert "call ask_owner" not in told_at["owner"]


def test_the_producer_is_told_how_the_fix_ended_and_nothing_of_a_wait(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene",
        turn(calls=[ask()]),
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[("give_up", {"reason": "The pour can't be filmed.", "best_try": 1})]),
    )

    step = clip_of_scene_2(fake_model, steps, say)

    step.refresh_from_db()
    assert "the fixing agent gave up after 1 tries" in step.result
    assert "answer" not in step.result and "Parked" not in step.result
    # The give-up shows the shop owner every try, as any give-up does.
    assert Message.objects.filter(tries_of=step).exists()


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
        "fix_scene",
        turn(calls=[film_again(video_prompt=POURS)]),
        turn(calls=[ask()]),
        turn(calls=[film_again()]),
    )
    assemble_through_the_chat(fake_model, steps, say)

    def carried() -> list[tuple[str, dict[str, object]]]:
        shown = api.get(f"/api/sessions/{session_id}/messages/").json()
        return [(each["text"], each["question"]) for each in shown if each["question"] is not None]

    # Only the line asking the question carries it, open, in the agent's words.
    [(text, shown)] = carried()
    assert QUESTION in text
    assert shown == {"scene": 2, "text": QUESTION, "open": True}

    question = work.open_question(AgentFix.objects.get().step.scene)
    assert question is not None
    owner_answers.answer(question, words="I don't have one.")

    # Once answered, the card no longer shows it as waiting.
    assert carried()[0][1]["open"] is False


def test_a_message_ending_the_pause_leaves_the_fixing_agents_question_open(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    # Round 7 unlisted, item 4: the question the fix is parked on is still to be asked.
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 2)
    fake_model.respond(
        "fix_scene", turn(calls=[film_again(video_prompt=POURS)]), turn(calls=[ask()])
    )
    assemble_through_the_chat(fake_model, steps, say)
    fake_model.respond("produce", turn(says="Paused."), turn(says="Asking again."))

    assert api.post(f"/api/sessions/{session_id}/pause/", {}, format="json").status_code == 200
    sent = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "Please go ahead."}, format="json"
    )
    assert sent.status_code == 201, sent.json()

    (note,) = SystemNote.objects.filter(kind=SystemNote.Kind.STILL_OPEN)
    assert f'"{QUESTION}"' in note.text and note.text.count(QUESTION) == 1
