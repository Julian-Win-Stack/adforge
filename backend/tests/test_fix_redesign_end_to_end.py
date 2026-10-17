"""The fix redesign end to end, through the chat, with every model faked (ticket 09; test
layers 2 and 3 of decisions/fix-redesign.md section 7). One shop owner's ad, first made,
then fixed three ways in a row: a picture note planned by the decider, run by code after the
yes through the picture check, the fix check and the clip judge, and shown; then going back
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
from jobs.models import FixCheck, Scene, SceneStep

from .conftest import JUDGED_CLIP, HeldSteps, paid_for, producer_turns
from .test_fix_wiring import (
    decided,
    given_to_the_producer,
    planning,
    plans,
    row,
    tap_yes,
)
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


def test_an_ad_is_fixed_by_a_picture_note_then_gone_back_then_given_a_new_line(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns = producer_turns()
    first = work.versions(Scene.objects.get(number=2))[0]
    ads_before = len(ads())

    # A picture note: the decider looks and plans, code shows the plan, nothing is made yet.
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_picture", {"scene": 2}), decided(row(2, "new_picture", TABLE))),
    )
    say("In scene 2 put the mug on a wooden table.")
    [plan] = plans()
    assert Message.objects.filter(plan_event="shown", fix_plan=plan).exists()
    assert held.held == []

    # The yes: code makes the picture, checks it twice, then makes its clip and judges it.
    tap_yes(api, session_id, plan)
    paid_before = len(paid_for())
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    held.run_next()
    [clip_step] = SceneStep.objects.filter(pk__in=held.held)
    assert (clip_step.kind, clip_step.picture) == ("clip", picture(2))
    held.run_held()

    checks = paid_for()[paid_before:]
    assert "check_starting_picture" in checks
    assert "check_fix" in checks
    judged = [purpose for purpose in checks if purpose.startswith("check_clip_")]
    assert sorted(judged) == sorted(JUDGED_CLIP)
    assert FixCheck.objects.get().verdict == "passed"
    assert status(2) == "finished"
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
            ("look_at_picture", {"scene": 3}),
            decided(row(3, "new_line", "Say it warmer.", line=NEW_LINE)),
        ),
    )
    say(f'Scene 3 should say "{NEW_LINE}"')
    tap_yes(api, session_id, plans()[-1])
    held.run_held()

    assert Scene.objects.get(number=3).line == NEW_LINE
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


def test_a_note_asking_for_a_new_line_and_a_new_picture_on_one_scene_gets_both(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    turns, ads_before, old = producer_turns(), len(ads()), picture(2)
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_picture", {"scene": 2}),
            decided(row(2, "new_picture", TABLE, line=NEW_LINE)),
        ),
    )
    say(f'Scene 2 should say "{NEW_LINE}", with the mug on a wooden table.')
    [plan] = plans()
    shown = Message.objects.get(plan_event="shown", fix_plan=plan)
    assert f'New line: "{NEW_LINE}"' in shown.text

    tap_yes(api, session_id, plan)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    held.run_held()

    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.status) == (NEW_LINE, "finished")
    drawn = picture(2)
    assert drawn != old and drawn.step is not None
    assert (drawn.step.line, drawn.step.note) == (NEW_LINE, TABLE)
    made = clip(2)
    assert made.picture == drawn and made.step is not None and made.step.line == NEW_LINE
    assert FixCheck.objects.get().verdict == "passed"
    assert len(ads()) == ads_before + 1
    assert made.pk in [cut["clip"] for cut in ads()[-1].cuts]
    assert producer_turns() == turns


def test_a_question_mid_fix_is_the_producers_with_the_notes_and_the_fix_carries_on(
    fake_model: FakeModel,
    held: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_picture", {"scene": 2}), decided(row(2, "new_picture", TABLE))),
    )
    say("In scene 2 put the mug on a wooden table.")
    [plan] = plans()
    tap_yes(api, session_id, plan)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    # The new picture is made and checked; its clip is still to be made.
    held.run_next()
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
    fake_model.respond("produce", turn(says="Scene 2's new picture is done; its clip is next."))

    say("How is scene 2 coming along?")

    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"][-1]
    asked = [each.text for each in produced.handoff.conversation if isinstance(each, Said)]
    assert asked[-1] == "How is scene 2 coming along?"
    told = [
        text.split(":", 1)[1].split(".")[0].strip() for text in given_to_the_producer(fake_model)
    ]
    assert told[:2] == [
        "A fix plan is shown to the shop owner, waiting for their yes",
        "The shop owner said yes to the plan: scene 2's new starting picture is being made",
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
            ("look_at_picture", {"scene": 3}),
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
