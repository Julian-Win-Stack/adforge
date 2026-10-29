"""The fix redesign end to end, through the chat, with every model faked (ticket 09; test
layers 2 and 3 of decisions/fix-redesign.md section 7). One shop owner's ad, first made,
then fixed three ways in a row: a clip note planned by the decider, run by code after the
yes through the clip judge, and shown; then going back
to the first version; then a new line. And the producer's lane: a question asked mid-fix is
the producer's, given with the notes code wrote, and the fix carries on by code (decision
20). What the producer says from them is its model's, scripted here."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents.models import SystemNote
from chat.models import Message
from gateway.fake import FakeModel, turn
from gateway.types import Said
from jobs import work
from jobs.models import AgentFix, FixCheck, ProducedItem, Scene

from .conftest import SENT_LIST_JUDGED, HeldSteps, facts_ok, paid_for, producer_turns
from .fixing import fixed, plan_row
from .test_fix_wiring import (
    decided,
    given_to_the_producer,
    planning,
    plans,
    row,
    stored,
    tap_yes,
)
from .test_fixing_agent import SLOWER, STIRS, failing_first
from .test_fixing_agent_moves import NEW_LINE as MOVES_LINE
from .test_fixing_agent_moves import change_action
from .test_pick_from_tries import made_ad  # noqa: F401 (a fixture)
from .test_producer_auto_fix import REMADE, rewrite
from .test_producer_broll import BROLL_CHOICE, TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
    picture,
    status,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "assembled"),
]

TABLE = "The mug stands on a wooden table."
NEW_LINE = "Stoneware that keeps your tea hot."


@pytest.fixture
def held(steps: HeldSteps, fake_model: FakeModel) -> HeldSteps:
    return steps


def test_an_ad_is_fixed_by_a_clip_note_then_gone_back_then_given_a_new_line(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns = producer_turns()
    first = work.versions(Scene.objects.get(number=2))[0]
    ads_before = len(ads())

    # A clip note: a plan stored before the fixing agent, of a new clip of B-roll scene 2
    # (spec #210 point 19: it has no starting picture to draw again); code shows the plan,
    # nothing is made yet.
    stored(plan_row(2, "new_clip", change=TABLE), note="In scene 2 put the mug on a wooden table.")
    [plan] = plans()
    assert Message.objects.filter(plan_event="shown", fix_plan=plan).exists()
    assert held.held == []

    # The yes: code makes the clip from its sent list and judges it, the change with it.
    tap_yes(api, session_id, plan)
    paid_before = len(paid_for())
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    held.run_held()

    checks = paid_for()[paid_before:]
    assert "check_starting_picture" not in checks
    assert "make_starting_picture" not in checks
    judged = [purpose for purpose in checks if purpose.startswith("check_clip_")]
    assert sorted(set(judged)) == sorted(set(SENT_LIST_JUDGED))
    assert FixCheck.objects.get().verdict == "passed"
    assert status(2) == "finished"
    assert clip(2).picture is None
    assert len(ads()) == ads_before + 1
    assert clip(2).pk in [cut["clip"] for cut in ads()[-1].cuts]
    assert work.versions(Scene.objects.get(number=2))[-1].label == f"v2 {TABLE}"

    # Going back to the first version: free, nothing made again, a new ad at once.
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_version", {"scene": 2, "version": 1}),
            decided(row(2, "go_back", go_back_to=1)),
        ),
    )
    say("I liked the first one better.")
    back = plans()[-1]
    assert back.scenes.get().estimate_usd == 0
    paid_before = len(paid_for())
    tap_yes(api, session_id, back)

    assert held.held == []
    assert paid_for()[paid_before:] == []
    assert clip(2).file == first.clip.file  # type: ignore[union-attr]
    assert work.versions(Scene.objects.get(number=2))[-1].label == "v3 back to v1"
    assert len(ads()) == ads_before + 2

    # A new line for the talking scene: code makes its voice, transcript and clip from the
    # starting picture it has, and shows the ad.
    kept = picture(3)
    paid_before = len(paid_for())
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_references", {"scene": 3}),
            decided(row(3, "new_line", "Say it warmer.", line=NEW_LINE)),
        ),
    )
    say(f'Scene 3 should say "{NEW_LINE}"')
    tap_yes(api, session_id, plans()[-1])
    held.run_held()

    assert Scene.objects.get(number=3).line == NEW_LINE
    # The decider gave no new overlay: the price still shows over the new line.
    assert ads()[-1].cuts[-1]["overlay"] == "$24.00"
    assert (picture(3), clip(3).picture) == (kept, kept)
    assert paid_for()[paid_before:] == [
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]
    assert ads()[-1].cuts[-1]["clip"] == clip(3).pk
    assert len(ads()) == ads_before + 3
    # Code did all of it: the producer took no turn.
    assert producer_turns() == turns


# Spec #210 point 19: the test of a new line and a new picture on one B-roll scene was
# deleted: the scene has no starting picture. A B-roll new picture is a new clip (#242).


def test_a_new_picture_of_a_broll_scene_is_a_new_clip_from_its_sent_list_fix_checked(
    made_ad: ProducedItem,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    before = clip(2)
    paid_before = len(paid_for())
    fixed(2, "new_picture", change=TABLE, note=TABLE)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))

    steps.run_held()

    made = clip(2)
    assert made != before and made.picture is None and made.sent is not None
    paid = paid_for()[paid_before:]
    assert "make_starting_picture" not in paid and "check_starting_picture" not in paid
    assert "make_broll_clip" in paid
    # It is fixed as a clip: made from the owner's note, and its fix check is the clip's.
    assert made.step is not None and made.step.note == TABLE
    assert made.step.fix is not None and made.step.fix.redo == "clip"
    assert FixCheck.objects.filter(step=made.step).exists()


def test_a_question_mid_fix_is_the_producers_with_the_notes_and_the_fix_carries_on(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # A plan stored before the fixing agent: a new picture of B-roll scene 2, which has no
    # starting picture, so it is a new clip (spec #210 point 19).
    stored(
        plan_row(2, "new_picture", change=TABLE), note="In scene 2 put the mug on a wooden table."
    )
    [plan] = plans()
    tap_yes(api, session_id, plan)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    fake_model.respond("produce", turn(says="Scene 2's new clip is being made."))

    say("How is scene 2 coming along?")

    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"][-1]
    asked = [each.text for each in produced.handoff.conversation if isinstance(each, Said)]
    assert asked[-1] == "How is scene 2 coming along?"
    told = [
        text.split(":", 1)[1].split(".")[0].strip() for text in given_to_the_producer(fake_model)
    ]
    assert told[:2] == [
        "A fix plan is shown to the shop owner, waiting for their yes",
        "The shop owner said yes to the plan: scene 2's new clip is being made",
    ]
    # Not a note: no new plan is shown, and the plan said yes to stays confirmed.
    assert plans() == [plan]
    assert not Message.objects.filter(plan_event="shown").exclude(fix_plan=plan).exists()
    # The scene carries on by code once the producer has answered.
    held.run_held()
    assert status(2) == "finished"
    assert "scene_done" in SystemNote.objects.values_list("kind", flat=True)


def test_a_new_price_line_changes_the_price_on_screen_too(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    # Scene 3 says "Yours for $24.00." with "$24.00" drawn over it. The shop owner changes the
    # price: the decider gives the new line and the overlay that shows the new price.
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_references", {"scene": 3}),
            decided(
                row(
                    3,
                    "new_line",
                    "Say the new price.",
                    line="Yours for $19.99.",
                    overlay=" $19.99 ",
                )
            ),
        ),
    )
    say("Change the price to $19.99.")
    tap_yes(api, session_id, plans()[-1])
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()

    assert ads()[-1].cuts[-1]["overlay"] == "$19.99"


def test_a_new_clip_the_judge_fails_is_fixed_by_the_agent_changing_the_action(
    made_ad: ProducedItem,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
    # The shop owner's note plans a new clip of scene 2; the judge fails it, and the fixing
    # agent films another action with a new line, which the judge passes and the ad gets.
    fixed(2, "new_clip", change=SLOWER, note=SLOWER)
    fake_model.respond("rewrite_clip_prompt", rewrite(REMADE))
    fake_model.answer_unscripted("check_clip_motion", failing_first(1))
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[change_action()]))

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
