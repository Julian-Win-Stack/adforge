"""Fixing an ad once it has a clip, end to end through the chat, on the producer's path (spec
#247, ticket #262): every message of the shop owner's is the producer's, with no decider, no
plan shown and no yes button. One ad, first made, then fixed: a clip note the producer looks
at and hands to the fixing agent with fix_scene, the new ad shown with one line of what the
change cost; then going back to the first version for nothing. A question asked while a fix
runs is the producer's to answer, and the fix carries on. A clip the producer makes from a
note, which the judge fails, is fixed by the agent changing the action. Every model is faked
at the gateway; what the producer says is its model's, scripted here."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents.models import SystemNote, ToolCall
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import Said, ToolUse
from jobs import work
from jobs.library import OwnerPhoto, library_of, sent_list_of
from jobs.models import AgentFix, Job, ProducedItem, Scene, SceneStep

from .conftest import (
    HeldSteps,
    ScriptedProducer,
    ScriptStep,
    facts_ok,
    given_to_the_producer,
    paid_for,
    picture,
    producer_turns,
    results_of,
)
from .test_fix_scene import (  # noqa: F401 (a fixture)
    INSTRUCTION,
    LOOKED,
    NOTE,
    WITH_IT,
    ad,
    fails_the_judge,
    fix_step,
    looks_then_fixes,
)
from .test_fixing_agent import POURS, SLOWER, STIRS, failing_first, film_again
from .test_fixing_agent_moves import NEW_LINE as MOVES_LINE
from .test_fixing_agent_moves import change_action
from .test_producer_auto_fix import FLOATS, REMADE, rewrite
from .test_producer_broll import calling, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import ads, clip

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_clip_note_is_fixed_by_the_producer_then_gone_back_for_nothing(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    first = work.versions(Scene.objects.get(number=2))[0]
    # The note reaches the producer, which looks, then hands it to the fixing agent: nothing
    # is shown for a yes, and no other model reads the note.
    looks_then_fixes(fake_model, say)
    assert not Message.objects.filter(role="notice", text__contains="plan").exists()
    step = fix_step()
    assert steps.held == [step.pk]

    # The agent's one try passes; the producer is told, and assembles the new ad.
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=STIRS)]))
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))
    steps.run_held()

    passed = AgentFix.objects.get(step=step).passed
    assert passed is not None and clip(2) == passed
    assert SceneStep.objects.get(pk=step.pk).result.endswith(
        "Once the new ad is shown, tell the shop owner in one line what changed and what it cost."
    )
    assert len(ads()) == 2
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [passed.pk]
    # One line of what the change cost, said once the new ad is shown.
    assembled = results_of("assemble_ad")[-1]
    assert assembled.count("what it cost") == 1
    assert " The changes since ad version 1 cost $" in assembled

    # Going back to the first version: free, nothing made again, a new ad at once.
    paid = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("use_version", {"scene": 2, "version": 1})]),
        turn(says="Scene 2 is back to the first version."),
    )
    say("I liked the first one better.")

    assert paid_for() == paid
    assert clip(2).file == first.clip.file  # type: ignore[union-attr]
    assert work.versions(Scene.objects.get(number=2))[-1].label.endswith("back to v1")
    assert len(ads()) == 3
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [clip(2).pk]


def test_a_question_while_a_fix_runs_is_the_producers_and_the_fix_carries_on(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    looks_then_fixes(fake_model, say)
    fake_model.respond("produce", turn(says="Scene 2 is being filmed again."))

    say("How is scene 2 coming along?")

    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"][-1]
    asked = [each.text for each in produced.handoff.conversation if isinstance(each, Said)]
    assert asked[-1] == "How is scene 2 coming along?"
    assert ToolCall.objects.filter(tool="fix_scene").count() == 1
    # The fix carries on once the producer has answered.
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=STIRS)]))
    fake_model.respond("produce", turn(says="Done."))
    steps.run_held()
    assert AgentFix.objects.get(step=fix_step()).passed is not None


def test_a_clip_made_from_a_note_the_judge_fails_is_fixed_by_the_agent_changing_the_action(
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # The producer makes scene 2's clip again from the shop owner's note; the judge fails it,
    # and the fixing agent films another action with a new line, which the judge passes.
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": SLOWER}))
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.answer_unscripted("check_clip_motion", failing_first(1))
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[change_action()]))
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here it is."))

    steps.run_held()

    fixing = AgentFix.objects.get()
    passed = fixing.passed
    assert passed is not None
    [made] = fixing.tries.all()
    assert (made.tool_call.tool, made.clip) == ("change_action", passed)
    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.shows) == (MOVES_LINE, STIRS)
    audio = passed.made_from
    assert audio is not None and audio.step is not None and audio.step.line == MOVES_LINE
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [passed.pk]


# --- Whole-build check C (spec #247 Testing Decisions, ticket #266): each row driven by the
# scripted producer through the chat, with the look, the fixing agent, the judge and the video
# API faked at the gateway. ------------------------------------------------------------------

WHICH_BRUSH = "Which brush does the clip show, and why that one?"


def asks_the_look_then_fixes() -> list[ScriptStep]:
    """The producer's answer to the brush note: it looks at scene 2's clip, then hands the
    note to the fixing agent with its instruction."""
    return [
        ("look", {"scene": 2, "what": "clip", "number": None, "question": WHICH_BRUSH}),
        ("fix_scene", {"scene": 2, "instruction": INSTRUCTION}),
        "I'm fixing scene 2's brush.",
    ]


def test_the_brush_is_the_wrong_colour_is_looked_at_against_what_was_sent_and_fixed_for_one_try(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
) -> None:
    noted = clip(2)
    sent = sent_list_of(noted)
    assert sent is not None and sent.pictures, "scene 2's clip was made from a sent list"
    paid = len(paid_for())
    fake_model.respond("look", LOOKED)

    producer.told(NOTE, *asks_the_look_then_fixes())

    # The look was handed what the clip was sent, then the clip's frames, and its question.
    [looked] = ModelCall.objects.filter(purpose="look")
    shown = [image["key"] for image in looked.images]
    assert shown[: len(sent.pictures)] == [each.file for each in sent.pictures]
    assert [image["label"] for image in looked.images][len(sent.pictures) :]
    assert all("Frame at" in image["label"] for image in looked.images[len(sent.pictures) :])
    assert f"The prompt it was sent: {sent.prompt}" in looked.handoff["shown"]
    assert WHICH_BRUSH in str(looked.handoff)
    # It answered in words only, and the producer read words.
    [row] = ToolCall.objects.filter(tool="look")
    assert row.pictures == [] and LOOKED["root_cause"] in row.result
    # fix_scene handed the note, word for word, to the owner-note door, with the instruction
    # and the look's words, and returned at once: nothing but the look paid so far.
    assert results_of("fix_scene")[0].startswith("Started scene 2's fix")
    step = fix_step()
    assert (step.owner_note, step.fix_goal) == (NOTE, INSTRUCTION)
    assert LOOKED["direction"] in step.look_words
    assert paid_for()[paid:] == ["look"]

    # The worker runs the fix: the agent films one try, the judge passes it; the producer,
    # told how the fix ended as the step's answer, assembles the new ad.
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=STIRS)]))
    producer.woken(("assemble_ad", {}), "Scene 2 now shows the green brush.")

    fixing = AgentFix.objects.get(step=step)
    assert fixing.door == AgentFix.Door.OWNER and fixing.note == NOTE
    [made] = fixing.tries.all()
    assert fixing.passed == made.clip and made.clip is not None
    [finished] = [
        each["text"]
        for each in given_to_the_producer(producer_turns() - 1)
        if each["kind"] == "step_finished"
    ][-1:]
    assert finished.startswith("Background step finished: Scene 2's fix passed")
    # One try paid: one clip filmed, judged once.
    after = paid_for()[paid:]
    assert after.count("make_broll_clip") == 1
    assert after.count("check_clip_motion") == 1
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [made.clip.pk]
    assert producer.said()[-1] == "Scene 2 now shows the green brush."


GREEN_PHOTO = "Here it is, the green one."
PHOTO_QUESTION = "Could you send a photo of the green brush?"


def test_the_fixing_agents_question_is_asked_in_the_chat_and_a_photo_answer_resumes_the_fix(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("look", LOOKED)
    producer.told(NOTE, *asks_the_look_then_fixes())
    step = fix_step()

    # The agent's first try fails the judge, and it asks the shop owner: the fix parks, and
    # the producer, woken with the question as fix_scene's answer, asks it in the chat.
    fake_model.respond("check_clip_motion", fails_the_judge(FLOATS))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        turn(calls=[("ask_owner", {"question": PHOTO_QUESTION, "wants": "photo"})]),
    )
    # Asking ends the producer's turn, so it says nothing after it.
    producer.woken(turn(calls=[("ask_owner", {"question": PHOTO_QUESTION})]))

    fixing = AgentFix.objects.get(step=step)
    assert work.is_parked(fixing)
    told = [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ][-1]
    assert f'The fixing agent asks: "{PHOTO_QUESTION}"' in told
    assert producer.said()[-1] == PHOTO_QUESTION
    # The scene card shows the question as text, and nothing to answer it with.
    cards = [
        message["question"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["question"] is not None
    ]
    assert cards == [{"scene": 2, "text": PHOTO_QUESTION, "open": True}]

    # The shop owner answers in the chat with a photo; the producer hands it back.
    producer.told(
        GREEN_PHOTO,
        ("fix_scene", {"scene": 2, "instruction": WITH_IT}),
        "Thanks, the fix carries on.",
        photos=[("green.png", picture(40, 30, (20, 160, 40)))],
    )
    fake_model.respond("fix_scene", turn(calls=[film_again(video_prompt=POURS)]))
    producer.woken(("assemble_ad", {}), "Here it is.")

    # The photo entered the library as the shop owner's, and the parked fix resumed and passed.
    [photo] = Attachment.objects.filter(message__text=GREEN_PHOTO)
    library = library_of(Job.objects.get())
    [item] = [each for each in library if each.source == OwnerPhoto(original=photo.file)]
    # As the clean picture drawn from it, never the photo as sent.
    assert item.file != photo.file
    # The fixing agent read the answer: the owner's words, the producer's instruction and the
    # photo, in its ask_owner call's result.
    turns = [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]
    [ask_read] = [
        each
        for each in turns[-1].handoff.conversation
        if isinstance(each, ToolUse) and each.tool == "ask_owner"
    ]
    assert GREEN_PHOTO in ask_read.result and WITH_IT in ask_read.result
    assert item.file in [each.key for each in ask_read.pictures]

    fixing.refresh_from_db()
    assert fixing.passed is not None and fixing.passed == fixing.tries.get(number=2).clip
    assert work.open_question(step.scene) is None
    cards = [
        message["question"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["question"] is not None
    ]
    # The question's notice and the one saying the fix carries on, both answered now.
    assert cards == [{"scene": 2, "text": PHOTO_QUESTION, "open": False}] * 2
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [fixing.passed.pk]


def test_a_pause_during_a_fix_lets_its_try_finish_parks_it_and_the_producer_carries_it_on(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    ad: ProducedItem,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("look", LOOKED)
    producer.told(NOTE, *asks_the_look_then_fixes())
    step = fix_step()

    def paused_while_judged() -> dict[str, Any]:
        # The shop owner presses pause while the first try is judged; the producer, woken by
        # the pause, reads the note and says one line (the first scripted below), then the
        # fix's end wakes it.
        fake_model.respond("produce", turn(says="Scene 2's fix stopped between tries."))
        pressed = api.post(f"/api/sessions/{session_id}/pause/", {}, format="json")
        assert pressed.status_code == 200, pressed.json()
        return fails_the_judge(FLOATS)

    fake_model.respond("check_clip_motion", paused_while_judged)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(video_prompt=STIRS)]),
        # Never taken while the pause holds.
        turn(calls=[film_again(video_prompt=POURS)]),
    )
    producer.woken("Paused. Scene 2's fix is finishing its try.")

    fixing = AgentFix.objects.get(step=step)
    # The try being filmed finished and was judged; no next try started; the fix is parked.
    [made] = fixing.tries.all()
    assert made.clip is not None and made.clip.ruling["verdict"] == "fail"  # type: ignore[index]
    assert fake_model.left("fix_scene") == 1
    assert fixing.paused_at is not None
    # The pause note named it.
    [note] = SystemNote.objects.filter(kind=SystemNote.Kind.PAUSED)
    assert "scene 2's clip, from fix_scene" in note.text
    assert "carry it on with fix_scene" in note.text
    assert producer.said()[-2:] == [
        "Paused. Scene 2's fix is finishing its try.",
        "Scene 2's fix stopped between tries.",
    ]

    # After the shop owner's message the producer carries it on, and the next try passes.
    producer.told(
        "Go on with the brush.",
        ("fix_scene", {"scene": 2, "instruction": "Carry on with the brush."}),
        "Carrying on.",
    )
    producer.woken(("assemble_ad", {}), "Here it is.")

    fixing.refresh_from_db()
    assert fixing.paused_at is None and fixing.resumed_at is not None
    assert fixing.passed == fixing.tries.get(number=2).clip
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [fixing.passed.pk]
