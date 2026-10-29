"""Checking a fix (route B step 6, #131), once the shop owner said yes to a fix plan: after a
scene is made again for it, a judge is asked the change back about the new version, shown
beside the old one. A new starting picture is checked before its clip is paid for; a new clip
by the clip judge; a new line is never checked, its words are the proof. A pass carries on as
before. A fail makes nothing more: the chat shows the old and new versions and why, the
producer is told by a system note, and the scene waits for the shop owner's word. Every
verdict is kept with its fix and both versions.

The mug's first ad is made through the chat, as in test_fix_loop.py, then fixed by a plan run
by code (tests/fixing.py); every outside service is faked at the gateway."""

import dataclasses
import re
from decimal import Decimal
from typing import Any

import pytest
from django.db.models.signals import post_save, pre_save
from rest_framework.test import APIClient

from agents import run_plan, tasks
from agents.models import SystemNote
from chat.models import Message
from chat.sent import NOTHING_SENT
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import clip_judge, library, work
from jobs.clip_judge import ASKS
from jobs.models import Fix, FixCheck, FixPlan, ProducedItem, Scene, SceneStep

from .agent_tries import agent_films, agent_stops
from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    FIX_MADE,
    HeldSteps,
    asked_with,
    handoffs,
    motion_answer,
    paid_for,
    results_of,
    sent_list_in_the_store,
)
from .fixing import fixed, plan_row, planned, say_yes
from .test_end_to_end import TALKING, ad_made_through_the_chat  # noqa: F401 (a fixture)
from .test_fix_loop import (
    BOTH_HANDS,
    HANDLE_FIX,
    HANDLE_SHOWING,
    HOVERS,
    LIFT_FIX,
    LIFT_REWRITE,
    SALE,
    SALE_FIX,
    TURN_FIX,
    TURN_REWRITE,
    WRONG_SIDE,
    Say,
    ads,
    held_kinds,
    latest,
    owner_says,
    shown,  # noqa: F401 (a fixture)
)
from .test_producer_restarts import WorkerStopped, the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)

HANDLE_HIDDEN = "The mug's handle still faces away from the camera, behind the mug."


def not_made(still_wrong: str) -> dict[str, Any]:
    """The judge's answer when the change isn't there."""
    return {
        "old_shows": "The mug's handle faces away from the camera.",
        "new_shows": "The mug's handle still faces away from the camera.",
        "verdict": "not fixed",
        "still_wrong": still_wrong,
    }


def picture_redone(fake_model: FakeModel, steps: HeldSteps, change: str = HANDLE_FIX) -> FixPlan:
    """The shop owner notes scene 2's handle and says yes to the plan of a new starting
    picture for it, and its new starting picture is made."""
    plan = fixed(2, "new_picture", change=change, note=WRONG_SIDE)
    assert held_kinds(steps) == [(2, "starting_picture")]
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    steps.run_next()
    return plan


def messages_of(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    return api.get(f"/api/sessions/{session_id}/messages/").json()  # type: ignore[no-any-return]


def notice_of(api: APIClient, session_id: str, check: FixCheck) -> dict[str, Any]:
    """The chat's notice of the failed check, as the chat shows it."""
    assert check.notice is not None
    return next(each for each in messages_of(api, session_id) if each["seq"] == check.notice.seq)


def notes(kind: str) -> list[str]:
    """The system notes of `kind` the producer is told, oldest first."""
    return list(
        SystemNote.objects.filter(kind=kind)
        .order_by("created_at", "id")
        .values_list("text", flat=True)
    )


def row_of(plan: FixPlan, number: int) -> Any:
    return plan.scenes.get(scene__number=number)


# --- A new starting picture is checked before its clip is paid for -----------------------------


def test_a_fixs_new_picture_is_checked_beside_the_old_one_and_passing_carries_on(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    old_picture = latest("starting_picture", 2)

    picture_redone(fake_model, steps)

    new_picture = latest("starting_picture", 2)
    # Two passes settle it, so the third ask isn't made. The judge sees the old picture first,
    # then the new one, and is told the change word for word.
    assert paid_for()[len(shown) :] == [
        "choose_broll_picture",
        "make_starting_picture",
        "check_starting_picture",
        "check_starting_picture",
        "check_fix",
        "check_fix",
    ]
    assert [(ask["fix"], ask["ask"]) for ask in handoffs("check_fix")] == [
        (HANDLE_FIX, 1),
        (HANDLE_FIX, 2),
    ]
    check = FixCheck.objects.get()
    assert (check.fix, check.old, check.new, check.verdict) == (
        Fix.objects.get(),
        old_picture,
        new_picture,
        "passed",
    )
    assert len(check.answers) == 2
    assert notes(SystemNote.Kind.CHECK_FAILED) == []

    # Its clip is made by code as before, and the new ad assembled.
    assert held_kinds(steps) == [(2, "clip")]
    steps.run_next()
    assert [ad.version for ad in ads()] == [1, 2]
    # A picture fix's clip isn't checked: the complaint was about the picture.
    assert FixCheck.objects.count() == 1


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_fixs_new_picture_that_fails_its_check_shows_both_and_waits_for_the_owner(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    old_picture = latest("starting_picture", 2)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), FIX_MADE, not_made(HANDLE_HIDDEN))

    picture_redone(fake_model, steps)

    new_picture = latest("starting_picture", 2)
    check = FixCheck.objects.get()
    assert (check.verdict, check.reason, check.old, check.new) == (
        "failed",
        HANDLE_HIDDEN,
        old_picture,
        new_picture,
    )
    assert len(check.answers) == 3
    # The chat shows the old picture and the new one, and why, with the reason as the note
    # the scene's card is filled with.
    notice = notice_of(api, session_id, check)
    assert notice["text"] == (
        "Scene 2's new starting picture may not have your change: "
        f"{HANDLE_HIDDEN} Here are the old picture and the new one. Say go ahead to make its "
        "clip anyway, or tell me what to change."
    )
    assert [file["url"].rsplit("/", 1)[-1] for file in notice["attachments"]] == [
        old_picture.file.rsplit("/", 1)[-1],
        new_picture.file.rsplit("/", 1)[-1],
    ]
    assert notice["suggested_note"] == {"scene": 2, "text": HANDLE_HIDDEN}
    # The scene is planned again, but no clip is made of it: the producer is told why.
    assert Scene.objects.get(number=2).status == "planned"
    assert steps.held == []
    assert notes(SystemNote.Kind.CHECK_FAILED) == [
        f"Scene 2's new starting picture (v2 {HANDLE_FIX}) may not have the change, so its "
        f"clip wasn't made: {HANDLE_HIDDEN} The chat showed the shop owner v1 first version "
        f"and v2 {HANDLE_FIX}, and waits for their answer."
    ]


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_clip_isnt_paid_for_after_a_failed_picture_check_before_the_owner_answers(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    picture_redone(fake_model, steps)

    # Another step of the scene ending, or a message that isn't the shop owner's go ahead,
    # carries nothing on.
    run_plan.carry_on_scene(Scene.objects.get(number=2))
    fake_model.respond("produce", turn(says="It's being looked at."))
    say("How's it going?")

    assert steps.held == []
    assert "make_broll_clip" not in paid_for()[len(shown) :]


@pytest.mark.usefixtures("no_fix_redraws")
def test_an_owner_message_before_the_failed_checks_notice_doesnt_unlock_its_clip(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    # Found by the review: the verdict is kept before the notice is posted. A message the
    # shop owner sends in between was sent before they saw it, so it isn't their answer.
    fixed(2, "new_picture", change=HANDLE_FIX, note=WRONG_SIDE)
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    with the_worker_stops(post_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()

    # Even read as a go ahead, it doesn't carry the scene on.
    fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "go_ahead"})]))
    fake_model.respond("produce", turn(says="Sure."))
    say("Go ahead")
    # The step is handed out again, and its check fails.
    tasks.run_scene_step(step_id)

    assert held_kinds(steps) == []
    assert FixCheck.objects.get().gone_ahead_at is None


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_new_note_after_a_failed_picture_check_redraws_the_picture_and_checks_again(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    picture_redone(fake_model, steps)
    failed = latest("starting_picture", 2)

    closer = "In scene 2, the mug is turned so its handle faces the camera, close up."
    fixed(2, "new_picture", change=closer, note=HANDLE_HIDDEN)
    assert held_kinds(steps) == [(2, "starting_picture")]
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    steps.run_next()

    # The new fix's picture is judged against the one the shop owner just saw.
    second = FixCheck.objects.latest("id")
    assert (second.fix.fix, second.old, second.new, second.verdict) == (
        closer,
        failed,
        latest("starting_picture", 2),
        "passed",
    )
    assert [check.verdict for check in FixCheck.objects.order_by("id")] == ["failed", "passed"]


def test_a_picture_check_whose_asks_fail_unexpectedly_carries_on_and_says_so(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    broken = RuntimeError("the picture couldn't be read")
    fake_model.respond("check_fix", broken, broken, broken)

    picture_redone(fake_model, steps)

    check = FixCheck.objects.get()
    assert (check.verdict, check.answers) == ("unchecked", [])
    assert held_kinds(steps) == [(2, "clip")]
    # Never silent: the shop owner is told it wasn't checked, and the producer that it was.
    assert Message.objects.filter(
        role="notice",
        text="Scene 2's new starting picture couldn't be checked for your change, so it is "
        "kept unchecked.",
    ).exists()
    assert notes("check_unchecked") == [
        "Scene 2's fix couldn't be checked in its new picture, so it carries on unchecked. "
        "The chat told the shop owner it wasn't checked."
    ]


def test_a_not_fixed_answer_with_no_reason_is_no_answer_but_is_still_paid_for(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    # The judge's first answer breaks the answer's rules, so it can't be read; the gateway
    # doesn't ask again for one, and the next two asks settle it.
    fake_model.respond("check_fix", not_made(""), FIX_MADE, FIX_MADE)

    picture_redone(fake_model, steps)

    check = FixCheck.objects.get()
    assert (check.verdict, check.answers) == ("passed", [FIX_MADE, FIX_MADE])
    asked = ModelCall.objects.filter(purpose="check_fix").order_by("id")
    assert [(call.handoff["ask"], call.outcome) for call in asked] == [
        (1, "failed"),
        (2, "succeeded"),
        (3, "succeeded"),
    ]
    # An unusable answer was still billed: the fake's 1,000 tokens in and 100 out at sol's.
    assert asked[0].cost_usd == Decimal("0.003") and "could not be read" in asked[0].error
    assert paid_for()[len(shown) :].count("check_fix") == 3


@pytest.mark.usefixtures("no_fix_redraws")
def test_a_check_run_again_after_the_worker_stopped_pays_and_shows_everything_once(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fixed(2, "new_picture", change=HANDLE_FIX, note=WRONG_SIDE)
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))

    # The worker stops just after the verdict is kept, before the step finishes.
    with the_worker_stops(post_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert FixCheck.objects.get().verdict == "failed"
    assert paid_for()[len(shown) :].count("check_fix") == 2
    texts = [message["text"] for message in messages_of(api, session_id)]
    assert sum(text.startswith("Scene 2's new starting picture may not") for text in texts) == 1
    assert len(notes(SystemNote.Kind.CHECK_FAILED)) == 1
    assert steps.held == []


def test_a_fix_isnt_checked_when_quality_checks_are_switched_off(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    settings: Any,
) -> None:
    settings.QUALITY_CHECKS = False

    picture_redone(fake_model, steps)

    assert not FixCheck.objects.exists()
    assert "check_fix" not in paid_for()
    assert held_kinds(steps) == [(2, "clip")]


# --- A new clip is checked once, on the clip the clip judge kept -------------------------------

HOVERING = "The hand still only hovers beside the mug; it never turns it."


def a_clip_redone(fake_model: FakeModel, steps: HeldSteps) -> FixPlan:
    """The shop owner says yes to the plan of a new clip for scene 2, and it is made."""
    plan = fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    steps.run_next()
    return plan


def labels(purpose: str) -> list[list[str]]:
    """The labels of the pictures each call for `purpose` was shown."""
    calls = ModelCall.objects.filter(purpose=purpose).order_by("id")
    return [[image["label"] for image in call.images] for call in calls]


def test_a_fixs_new_clip_is_judged_beside_the_old_one_and_passing_shows_the_ad(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    requests = asked_with(fake_model, "check_clip_sides", "check_clip_motion")
    old_clip = latest("clip", 2)

    a_clip_redone(fake_model, steps)

    new_clip = latest("clip", 2)
    # The fix check is the clip judge's own asks, one per check: nothing else is asked about
    # the clip.
    asked = paid_for()[len(shown) :]
    assert sorted(each for each in asked if each.startswith("check_")) == sorted(
        ["check_clip_sides"] * ASKS + ["check_clip_motion"] * ASKS
    )
    # Each MOTION ask is told the owner's change word for word, asked the extra question, and
    # shown the old clip's frames before the new one's; SIDES is asked as for any clip.
    # The fix's clip is the last one judged.
    motions = [each for each in requests if each.purpose == "check_clip_motion"][-ASKS:]
    assert len(motions) == ASKS
    for motion in motions:
        assert motion.handoff.model_dump()["owner_change"] == TURN_FIX
        assert motion.instructions == (
            f"{clip_judge.MOTION_CHECK}\n\n{clip_judge.MOTION_FIX_QUESTION}"
        )
        assert "owner_change" in motion.output.model_fields
        given = [image.label for image in motion.images]
        old_first = next(n for n, label in enumerate(given) if "old clip" in label)
        new_first = next(n for n, label in enumerate(given) if "new clip" in label)
        assert given[old_first].startswith(clip_judge.OLD_FRAMES_LABEL)
        assert given[new_first].startswith(clip_judge.NEW_FRAMES_LABEL)
        assert 0 < old_first < new_first < len(given) <= clip_judge.MOST_PICTURES
        # Both clips at MOTION's own frame rate.
        for first, last in ((old_first, new_first), (new_first, len(given))):
            times = [
                float(t) for label in given[first:last] for t in re.findall(r"([\d.]+) s", label)
            ]
            assert times[1] - times[0] == pytest.approx(1 / clip_judge.FRAMES_PER_SECOND)
    for sides in [each for each in requests if each.purpose == "check_clip_sides"][-ASKS:]:
        assert "owner_change" not in sides.handoff.model_dump()
        assert sides.instructions == clip_judge.SIDES_CHECK
        assert sides.output is clip_judge.SidesVerdict
        assert not any("old clip" in image.label for image in sides.images)
    check = FixCheck.objects.get()
    assert (check.old, check.new, check.verdict) == (old_clip, new_clip, "passed")
    # The judge's one MOTION answer settles it, and is kept.
    assert check.answers == [CLIP_FIXED]
    assert [ad.version for ad in ads()] == [1, 2]
    assert notes(SystemNote.Kind.CHECK_FAILED) == []


def test_the_clip_checks_cost_is_the_clip_steps(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    a_clip_redone(fake_model, steps)

    step = (
        SceneStep.objects.filter(kind="clip", scene__number=2)
        .exclude(tool_call__agent="fixing_agent")
        .latest("id")
    )
    asked = ModelCall.objects.filter(purpose__startswith="check_").filter(tool_call=step.tool_call)
    assert asked.count() == 2 * ASKS
    # The prompt written at $0.006, the 5-second clip at Boreal's $0.0792 a second, and the
    # judge's 2 asks at $0.003 each, which check the fix too.
    assert notes(SystemNote.Kind.SCENE_DONE) == ["Scene 2's new clip is done. It cost $0.408."]


def test_a_fixs_new_clip_that_fails_its_check_shows_both_clips_and_no_new_ad(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )
    old_clip = latest("clip", 2)
    sent = sent_list_in_the_store()
    library.keep_sent_list(old_clip, sent)

    plan = a_clip_redone(fake_model, steps)

    new_clip = latest("clip", 2)
    check = FixCheck.objects.get()
    assert check.verdict == "failed"
    # No new ad is shown from a clip that failed: the shop owner decides first.
    assert [ad.version for ad in ads()] == [1]
    notice = notice_of(api, session_id, check)
    assert notice["text"] == (
        f"Scene 2's new clip may not have your change: {HOVERING} Here are the old clip and "
        "the new one. Say go ahead to use the new clip in the ad anyway, or tell me what to "
        "change."
    )
    assert [file["url"].rsplit("/", 1)[-1] for file in notice["attachments"]] == [
        old_clip.file.rsplit("/", 1)[-1],
        new_clip.file.rsplit("/", 1)[-1],
    ]
    # Each clip shows what it was sent: the old one its sent list, the new one none yet.
    old_shown, new_shown = (file["sent"] for file in notice["attachments"])
    assert [each["label"] for each in old_shown["pictures"]] == [
        each.label for each in sent.pictures
    ]
    assert new_shown == NOTHING_SENT
    assert notice["suggested_note"] == {"scene": 2, "text": HOVERING}
    assert notes(SystemNote.Kind.CHECK_FAILED) == [
        f"Scene 2's new clip (v2 {TURN_FIX}) may not have the change, so the new ad waits: "
        f"{HOVERING} The chat showed the shop owner v1 first version and v2 {TURN_FIX}, and "
        "waits for their answer."
    ]

    run_plan.go_ahead(row_of(plan, 2))
    assert [ad.version for ad in ads()] == [1, 2]
    # The new ad plays the new clip, as they said to, not the old one.
    assert {cut["scene"]: cut["clip"] for cut in ads()[1].cuts}[2] == new_clip.pk


def test_the_producer_cant_assemble_the_ad_over_a_clip_whose_check_waits(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Say,
    shown: list[str],  # noqa: F811
) -> None:
    # The scene is finished with the new clip, but the shop owner hasn't said to use it.
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )
    a_clip_redone(fake_model, steps)

    owner_says(fake_model, say, "Can I see the ad now?", ("assemble_ad", {}))

    assert results_of("assemble_ad")[-1].startswith(
        "Refused: the shop owner hasn't answered the fix check on scene 2 yet."
    )
    assert [ad.version for ad in ads()] == [1]


def test_a_new_note_after_a_failed_clip_check_remakes_the_clip_and_checks_again(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )
    a_clip_redone(fake_model, steps)
    failed = latest("clip", 2)
    assert [ad.version for ad in ads()] == [1]

    # The new fix's clip has the change.
    fake_model.answer_unscripted("check_clip_motion", motion_answer(CLIP_MOTION_OK))
    fixed(2, "new_clip", change=LIFT_FIX, note=HOVERING)
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", LIFT_REWRITE)
    steps.run_next()

    # The new fix's clip is judged against the one the shop owner just saw.
    second = FixCheck.objects.latest("id")
    assert (second.fix.fix, second.old, second.new, second.verdict) == (
        LIFT_FIX,
        failed,
        latest("clip", 2),
        "passed",
    )
    assert [check.verdict for check in FixCheck.objects.order_by("id")] == ["failed", "passed"]
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_change_not_made_never_remakes_the_clip_by_itself(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )

    a_clip_redone(fake_model, steps)

    # The judge passed the clip: the shop owner decides about their change, not the auto-fix.
    assert "remake_clip_prompt" not in paid_for()
    assert latest("clip", 2).remake is None
    assert (latest("clip", 2).ruling or {})["verdict"] == "pass"
    assert FixCheck.objects.get().verdict == "failed"


def test_a_try_the_agent_films_is_asked_the_owners_change_again_and_its_answer_is_kept(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floats = "The mug rises at 1.2 s with no hand on it; a real one stays put."
    failing = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": floats, "back_to_normal_at": "never"},
        "owner_change": not_made(HOVERING),
    }
    passing = {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}
    # The fix's clip fails the judge (and its change isn't made); the agent's try passes both.
    fake_model.respond("check_clip_motion", failing, passing)
    agent = agent_films(monkeypatch, TURN_REWRITE["motion_prompt"])
    old_clip = latest("clip", 2)

    a_clip_redone(fake_model, steps)

    (tried,) = ProducedItem.objects.filter(scene__number=2, remake__isnull=False)
    assert latest("clip", 2) == tried
    # The agent is handed the judge's ruling of the fix's clip.
    ((_, door, failure, _),) = agent.handed
    assert door == "judge" and failure.ruling is not None
    assert failure.ruling["verdict"] == "fail"
    # Both clips were asked the extra question, shown the same old clip.
    motions = ModelCall.objects.filter(purpose="check_clip_motion").order_by("id")
    fixs = list(motions)[-2 * ASKS :]
    assert all((call.output or {})["owner_change"] for call in fixs)
    # The fix check is the passing try's answers, once.
    check = FixCheck.objects.get()
    assert (check.old, check.new, check.verdict) == (old_clip, tried, "passed")
    assert check.answers == [CLIP_FIXED] * ASKS


def test_a_change_not_made_with_no_reason_never_costs_the_clip_its_own_checks(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floats = "The mug rises at 1.2 s with no hand on it; a real one stays put."
    # Found by the review: a "not fixed" that forgets to say why.
    no_reason = {**not_made(HOVERING), "still_wrong": None}
    failing = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": floats, "back_to_normal_at": "never"},
        "owner_change": no_reason,
    }
    passing = {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}
    fake_model.respond("check_clip_motion", failing, passing)
    agent_films(monkeypatch, TURN_REWRITE["motion_prompt"])

    a_clip_redone(fake_model, steps)

    # The clip's own fault still fails it, and the agent films it again.
    first = ProducedItem.objects.get(
        scene__number=2, step__isnull=False, remake__isnull=True, version__gt=1
    )
    assert (first.ruling or {})["verdict"] == "fail"
    (tried,) = ProducedItem.objects.filter(scene__number=2, remake__isnull=False)
    assert FixCheck.objects.get().new == tried


def test_a_failed_clip_check_also_says_what_the_clip_judge_still_sees(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floats = "The mug rises at 1.2 s with no hand on it; a real one stays put."
    # The clip judge fails the fix's clip, and the agent can't carry on, so the fix's clip is
    # kept and checked, and the judge's problem is still seen in it.
    fake_model.answer_unscripted(
        "check_clip_motion",
        motion_answer(
            {
                **CLIP_MOTION_OK,
                "real_motion": {"verdict": "fail", "problem": floats, "back_to_normal_at": "never"},
            },
            not_made(HOVERING),
        ),
    )
    agent_stops(monkeypatch, RuntimeError("the model fell over"))

    a_clip_redone(fake_model, steps)

    check = FixCheck.objects.get()
    assert (check.new, check.new.remake) == (latest("clip", 2), None)
    # One notice says both.
    assert notice_of(api, session_id, check)["text"].endswith(
        f"or tell me what to change. The clip's own check also still sees: {floats}"
    )
    # That notice is the only one: the judge's kept clip isn't said again on top of it.
    told = [each.text for each in Message.objects.filter(role="notice", level="info")]
    assert not any("though the judge found a fault" in text for text in told)


def test_a_fixs_clip_is_judged_alone_when_the_old_clips_frames_cant_be_cut(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_clip = latest("clip", 2)
    cutting = clip_judge.cut_pictures

    def cant_cut_the_old_one(clip: str, **kwargs: Any) -> Any:
        if clip == old_clip.file:
            raise RuntimeError("ffmpeg couldn't read the clip")
        return cutting(clip, **kwargs)

    monkeypatch.setattr(clip_judge, "cut_pictures", cant_cut_the_old_one)
    requests = asked_with(fake_model, "check_clip_motion")

    a_clip_redone(fake_model, steps)

    # The new clip, paid for, is still judged, as any clip: no extra question.
    fixs = requests[-ASKS:]
    assert [each.instructions for each in fixs] == [clip_judge.MOTION_CHECK] * ASKS
    assert all(each.output is clip_judge.MotionVerdict for each in fixs)
    assert (latest("clip", 2).ruling or {})["verdict"] == "pass"
    assert FixCheck.objects.get().verdict == "unchecked"
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_clip_brought_back_in_the_fixs_step_is_never_the_old_one(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_clip = latest("clip", 2)
    making = tasks.STEP_WORK[SceneStep.Kind.CLIP]

    def made_then_brought_back(step: SceneStep) -> ProducedItem:
        # As the clip judge's redos do (#155): the clip kept comes back as the newest.
        made = making.make(step)
        assert made is not None
        return work.bring_back_clip(made)

    monkeypatch.setitem(
        tasks.STEP_WORK,
        SceneStep.Kind.CLIP,
        dataclasses.replace(making, make=made_then_brought_back),
    )

    a_clip_redone(fake_model, steps)

    check = FixCheck.objects.get()
    assert (check.old, check.new) == (old_clip, latest("clip", 2))
    assert check.new.version == old_clip.version + 2


def test_a_fixs_clip_the_agent_gave_up_on_isnt_checked_as_the_scene_keeps_its_old_clip(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    floats = "The mug rises at 1.2 s with no hand on it; a real one stays put."
    failing = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": floats, "back_to_normal_at": "never"},
        "owner_change": CLIP_FIXED,
    }
    # The fix's clip and both the agent's tries fail; the agent gives up.
    fake_model.respond("check_clip_motion", *[failing] * 3)
    agent_films(monkeypatch, TURN_REWRITE["motion_prompt"], TURN_REWRITE["motion_prompt"])
    old_clip = latest("clip", 2)

    a_clip_redone(fake_model, steps)

    fixs_clip, *tried, brought_back = ProducedItem.objects.filter(
        kind="clip", scene__number=2, version__gt=old_clip.version
    ).order_by("id")
    assert [made.remake for made in tried] == [1, 2]
    assert (fixs_clip.remake, brought_back.file) == (None, old_clip.file)
    assert latest("clip", 2) == brought_back
    assert not FixCheck.objects.exists()


def test_another_scenes_fix_landing_shows_no_ad_over_a_clip_that_failed_its_check(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )
    plan = say_yes(
        planned(
            plan_row(1, "new_picture", change=BOTH_HANDS),
            plan_row(2, "new_clip", change=TURN_FIX),
            note=f"{HOVERS} And in the first scene she should hold the mug with both hands.",
        )
    )
    assert held_kinds(steps) == [(1, "starting_picture"), (2, "clip")]
    fake_model.respond("choose_starting_picture", TALKING)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    # Scene 1's picture lands and code makes its clip; scene 2's clip fails its check, then
    # scene 1's clip lands last.
    steps.run_next()
    steps.run_next()
    assert FixCheck.objects.get(step__scene__number=2).verdict == "failed"
    assert held_kinds(steps) == [(1, "clip")]
    steps.run_next()

    # The shop owner decides on scene 2's clip before any ad shows it.
    assert [ad.version for ad in ads()] == [1]
    assert notes(SystemNote.Kind.AD_SHOWN) == []
    run_plan.go_ahead(row_of(plan, 2))
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_clip_check_whose_asks_fail_unexpectedly_shows_the_ad(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    broken = RuntimeError("the picture couldn't be read")
    fake_model.respond("check_clip_motion", broken)

    a_clip_redone(fake_model, steps)

    check = FixCheck.objects.get()
    assert (check.verdict, check.answers) == ("unchecked", [])
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_clip_fix_isnt_checked_when_quality_checks_are_switched_off(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    settings: Any,
) -> None:
    settings.QUALITY_CHECKS = False

    a_clip_redone(fake_model, steps)

    assert not FixCheck.objects.exists()
    assert not [purpose for purpose in paid_for()[len(shown) :] if purpose.startswith("check_")]
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_clip_check_run_again_after_the_worker_stopped_keeps_its_verdict_and_shows_it_once(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.answer_unscripted(
        "check_clip_motion", motion_answer(CLIP_MOTION_OK, not_made(HOVERING))
    )
    fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    (step_id,) = steps.held
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    # The worker stops just after the verdict is kept, before the step finishes.
    with the_worker_stops(post_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert FixCheck.objects.get().verdict == "failed"
    paid = paid_for()[len(shown) :]
    assert (paid.count("make_broll_clip"), paid.count("check_clip_motion")) == (1, ASKS)
    texts = [message["text"] for message in messages_of(api, session_id)]
    assert sum(text.startswith("Scene 2's new clip may not") for text in texts) == 1
    assert len(notes(SystemNote.Kind.CHECK_FAILED)) == 1
    assert [ad.version for ad in ads()] == [1]


def test_a_fixs_clip_judged_again_after_the_worker_stopped_pays_once_and_cuts_once(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Scene 2's clip was made before the clip judge was in the app.
    old_clip = latest("clip", 2)
    ProducedItem.objects.filter(pk=old_clip.pk).update(ruling=None)
    cutting = clip_judge.cut_pictures
    cut: list[str] = []

    def counted(clip: str, **kwargs: Any) -> Any:
        cut.append(clip)
        return cutting(clip, **kwargs)

    monkeypatch.setattr(clip_judge, "cut_pictures", counted)
    fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    (step_id,) = steps.held
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    # The worker stops once the judge has answered, before the fix's verdict is kept.
    with the_worker_stops(pre_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()
    assert not FixCheck.objects.exists()
    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert FixCheck.objects.get().verdict == "passed"
    paid = paid_for()[len(shown) :]
    assert (paid.count("check_clip_sides"), paid.count("check_clip_motion")) == (ASKS, ASKS)
    # The old clip's frames are cut once, and kept on it.
    assert cut.count(old_clip.file) == 1
    assert (ProducedItem.objects.get(pk=old_clip.pk).ruling or {}).keys() == {"pictures_beside"}


# --- A line fix asks no judge ------------------------------------------------------------------


def test_a_line_fix_is_never_checked(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fixed(3, "new_line", change=SALE_FIX, line=SALE, note="Scene 3 should mention the sale")
    fake_model.respond("choose_starting_picture", TALKING)
    steps.run_held()

    assert Scene.objects.get(number=3).line == SALE
    assert latest("clip", 3).step.line == SALE  # type: ignore[union-attr]
    assert not FixCheck.objects.exists()
    assert "check_fix" not in paid_for()


# --- The verdict from the judge's answers -------------------------------------------------------


FIXED = {"verdict": "fixed", "still_wrong": None}


def unfixed(why: str) -> dict[str, Any]:
    return {"verdict": "not fixed", "still_wrong": why}
