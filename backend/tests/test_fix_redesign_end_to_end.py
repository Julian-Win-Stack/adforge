"""The fix redesign end to end, through the chat, with every model faked (ticket 09; test
layers 2 and 3 of decisions/fix-redesign.md section 7). One shop owner's ad, first made,
then fixed three ways in a row: a picture note planned by the decider, run by code after the
yes through the picture check, the fix check and the clip judge, and shown; then going back
to the first version; then a new line. And the producer's lane: asked mid-fix, it answers
from the notes code wrote and never asks for the yes again (decision 20)."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents.models import SystemNote, ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
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

    # A new line for the talking scene: code runs the line's chain and shows the ad.
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_picture", {"scene": 3}),
            decided(row(3, "new_line", "Say it warmer.", line=NEW_LINE)),
        ),
    )
    say(f'Scene 3 should say "{NEW_LINE}"')
    tap_yes(api, session_id, plans()[-1])
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()

    assert Scene.objects.get(number=3).line == NEW_LINE
    assert ads()[-1].cuts[-1]["clip"] == clip(3).pk
    assert len(ads()) == ads_before + 3
    # Code did all of it: the producer took no turn.
    assert producer_turns() == turns


def test_asked_mid_fix_the_producer_answers_from_the_notes_and_never_asks_for_the_yes_again(
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
    fake_model.respond("produce", turn(says="Scene 2's new picture is done; its clip is next."))
    calls = ToolCall.objects.filter(agent="producer").count()

    say("How is scene 2 coming along?")

    told = [
        text.split(":", 1)[1].split(".")[0].strip() for text in given_to_the_producer(fake_model)
    ]
    assert told[:2] == [
        "A fix plan is shown to the shop owner, waiting for their yes",
        "The shop owner said yes to the plan: scene 2's new starting picture is being made",
    ]
    # The producer only answered: no tool call, no new plan, no second yes asked for.
    assert ToolCall.objects.filter(agent="producer").count() == calls
    assert plans() == [plan]
    assert not Message.objects.filter(plan_event="shown").exclude(fix_plan=plan).exists()
    assert Message.objects.filter(role="agent").last().text == (  # type: ignore[union-attr]
        "Scene 2's new picture is done; its clip is next."
    )
    # The scene carries on by code once the producer has answered.
    held.run_held()
    assert status(2) == "finished"
    assert "scene_done" in SystemNote.objects.values_list("kind", flat=True)
