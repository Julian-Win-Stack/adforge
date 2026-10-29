"""Fixing an ad once it has a clip, end to end through the chat, on the producer's path (spec
#247, ticket #262): every message of the shop owner's is the producer's, with no decider, no
plan shown and no yes button. One ad, first made, then fixed: a clip note the producer looks
at and hands to the fixing agent with fix_scene, the new ad shown with one line of what the
change cost; then going back to the first version for nothing. A question asked while a fix
runs is the producer's to answer, and the fix carries on. A clip the producer makes from a
note, which the judge fails, is fixed by the agent changing the action. Every model is faked
at the gateway; what the producer says is its model's, scripted here."""

from collections.abc import Callable

import pytest

from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.types import Said
from jobs import work
from jobs.models import AgentFix, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, facts_ok, paid_for, results_of
from .test_fix_scene import ad, fix_step, looks_then_fixes  # noqa: F401 (a fixture)
from .test_fixing_agent import SLOWER, STIRS, failing_first, film_again
from .test_fixing_agent_moves import NEW_LINE as MOVES_LINE
from .test_fixing_agent_moves import change_action
from .test_producer_auto_fix import REMADE, rewrite
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
