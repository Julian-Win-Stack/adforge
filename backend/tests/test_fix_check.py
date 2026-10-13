"""Checking a fix (route B step 6, #131), through the chat: after a scene is made again for a
fix the shop owner said yes to, a judge is asked the complaint back about the new version,
shown beside the old one. A new starting picture is checked before its clip is paid for; a
new clip once the clip judge is in the app; a new line is never checked, its words are the
proof. A pass carries on as before. A fail makes nothing more: the chat shows the old and
new versions and why, and the producer waits for the shop owner's word. Every verdict is
kept with its fix and both versions.

The mug's first ad is made through the chat, then fixed, as in test_fix_loop.py; the
producer's turns are scripted, and every outside service is faked at the gateway."""

from typing import Any

import pytest
from django.db.models.signals import post_save
from rest_framework.test import APIClient

from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter
from jobs import fix_check
from jobs.models import Fix, FixCheck, Scene, SceneStep

from .conftest import FIX_MADE, HeldSteps, handoffs, paid_for, results_of
from .test_end_to_end import ad_made_through_the_chat  # noqa: F401 (a fixture)
from .test_fix_loop import (
    HANDLE_FIX,
    HANDLE_SHOWING,
    HOVERS,
    SALE,
    SALE_FIX,
    TURN_FIX,
    TURN_REWRITE,
    WRONG_SIDE,
    Call,
    Say,
    ads,
    clip_of,
    held_kinds,
    latest,
    owner_says,
    picture_of,
    restating,
    shown,  # noqa: F401 (a fixture)
    step_lands,
    told,
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


def picture_redone(fake_model: FakeModel, say: Say, steps: HeldSteps, *then: Call) -> None:
    """The shop owner notes scene 2's handle, says yes to its restated picture fix, and its
    new starting picture is made; told so, the producer makes `then`."""
    owner_says(fake_model, say, WRONG_SIDE, restating((2, HANDLE_FIX, "picture")))
    owner_says(fake_model, say, "Yes", picture_of(2, HANDLE_FIX))
    assert held_kinds(steps) == [(2, "starting_picture")]
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    step_lands(fake_model, steps, *then)


def messages_of(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    return api.get(f"/api/sessions/{session_id}/messages/").json()  # type: ignore[no-any-return]


# --- A new starting picture is checked before its clip is paid for -----------------------------


def test_a_fixs_new_picture_is_checked_beside_the_old_one_and_passing_carries_on(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    old_picture = latest("starting_picture", 2)

    picture_redone(fake_model, say, steps)

    new_picture = latest("starting_picture", 2)
    # Two passes settle it, so the third ask isn't made. The judge sees the old picture first,
    # then the new one, and is told the fix word for word.
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
    assert "The check of scene 2's fix found the change in the new picture." in told(
        2, "starting_picture"
    )

    # Its clip is made as before, and the new ad assembled.
    owner_says(fake_model, say, "Go on", clip_of(2))
    step_lands(fake_model, steps)
    assert [ad.version for ad in ads()] == [1, 2]
    # A picture fix's clip isn't checked: the complaint was about the picture.
    assert FixCheck.objects.count() == 1


def test_a_fixs_new_picture_that_fails_its_check_shows_both_and_waits_for_the_owner(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    old_picture = latest("starting_picture", 2)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), FIX_MADE, not_made(HANDLE_HIDDEN))

    picture_redone(fake_model, say, steps)

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
    notice = messages_of(api, session_id)[-2]
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
    assert "make its clip from this picture" not in told(2, "starting_picture")
    assert told(2, "starting_picture").endswith(
        f"The check of scene 2's fix found the change isn't in the new picture: {HANDLE_HIDDEN} "
        "The chat shows the shop owner the old picture and the new one, and why. Don't make "
        "its clip or say it's fixed: wait for their answer. If they say go ahead, make its "
        "clip with no note; if they say what to change, restate a new fix."
    )


def test_a_clip_isnt_paid_for_after_a_failed_picture_check_before_the_owner_answers(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))

    # Told the picture is ready, the producer makes its clip at once.
    picture_redone(fake_model, say, steps, clip_of(2))

    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's new starting picture failed the check of its fix, and the shop "
        "owner hasn't answered since they were shown it. Wait for their answer. Nothing was "
        "done."
    )
    assert steps.held == []


def test_go_ahead_after_a_failed_picture_check_makes_its_clip_and_the_ad(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    picture_redone(fake_model, say, steps)
    assert FixCheck.objects.get().verdict == "failed"
    assert steps.held == []

    owner_says(fake_model, say, "It's fine, go ahead", clip_of(2))
    assert held_kinds(steps) == [(2, "clip")]
    step_lands(fake_model, steps)

    assert latest("clip", 2).picture == latest("starting_picture", 2)
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_new_note_after_a_failed_picture_check_redraws_the_picture_and_checks_again(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    picture_redone(fake_model, say, steps)
    failed = latest("starting_picture", 2)

    closer = "In scene 2, the mug is turned so its handle faces the camera, close up."
    owner_says(fake_model, say, HANDLE_HIDDEN, restating((2, closer, "picture")))
    owner_says(fake_model, say, "Yes", picture_of(2, closer))
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    step_lands(fake_model, steps)

    # The new fix's picture is judged against the one the shop owner just saw.
    second = FixCheck.objects.latest("id")
    assert (second.fix.fix, second.old, second.new, second.verdict) == (
        closer,
        failed,
        latest("starting_picture", 2),
        "passed",
    )
    assert [check.verdict for check in FixCheck.objects.order_by("id")] == ["failed", "passed"]


def test_a_picture_check_that_cant_be_had_carries_on_and_says_so(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    blocked = BlockedBySafetyFilter("blocked")
    fake_model.respond("check_fix", blocked, blocked, blocked)

    picture_redone(fake_model, say, steps)

    check = FixCheck.objects.get()
    assert (check.verdict, check.answers) == ("unchecked", [])
    assert "Scene 2's fix couldn't be checked, so tell the shop owner it wasn't checked." in told(
        2, "starting_picture"
    )
    owner_says(fake_model, say, "Go on", clip_of(2))
    assert held_kinds(steps) == [(2, "clip")]


def test_a_check_run_again_after_the_worker_stopped_pays_and_shows_everything_once(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    owner_says(fake_model, say, WRONG_SIDE, restating((2, HANDLE_FIX, "picture")))
    owner_says(fake_model, say, "Yes", picture_of(2, HANDLE_FIX))
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))

    # The worker stops just after the verdict is kept, before the step finishes.
    with the_worker_stops(post_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    fake_model.respond("produce", turn(says="That part's ready."))
    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert FixCheck.objects.get().verdict == "failed"
    assert paid_for()[len(shown) :].count("check_fix") == 2
    texts = [message["text"] for message in messages_of(api, session_id)]
    assert sum(text.startswith("Scene 2's new starting picture may not") for text in texts) == 1


def test_an_owner_message_before_the_failed_checks_notice_doesnt_unlock_its_clip(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    # Found by the review: the verdict is kept before the notice is posted. A message the
    # shop owner sends in between was sent before they saw it, so it isn't their answer.
    owner_says(fake_model, say, WRONG_SIDE, restating((2, HANDLE_FIX, "picture")))
    owner_says(fake_model, say, "Yes", picture_of(2, HANDLE_FIX))
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    with the_worker_stops(post_save, FixCheck, when=lambda _: True), pytest.raises(WorkerStopped):
        steps.run_next()

    owner_says(fake_model, say, "How's it going?")
    # The step is handed out again; told the picture is ready, the producer makes its clip.
    fake_model.respond("produce", turn(calls=[clip_of(2)]))
    fake_model.respond("produce", turn(says="That part's ready."))
    tasks.run_scene_step(step_id)

    assert results_of("make_clip")[-1].startswith(
        "Refused: scene 2's new starting picture failed the check of its fix"
    )
    assert steps.held == []


def test_a_fix_isnt_checked_when_quality_checks_are_switched_off(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    settings: Any,
) -> None:
    settings.QUALITY_CHECKS = False

    picture_redone(fake_model, say, steps)

    assert not FixCheck.objects.exists()
    assert "check_fix" not in paid_for()
    owner_says(fake_model, say, "Go on", clip_of(2))
    assert held_kinds(steps) == [(2, "clip")]


# --- A new clip is checked once the clip judge is in the app ------------------------------------


def a_clip_redone(fake_model: FakeModel, say: Say, steps: HeldSteps) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    owner_says(fake_model, say, "Yes", clip_of(2, TURN_FIX))
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    step_lands(fake_model, steps)


def test_a_clip_fix_isnt_checked_until_the_clip_judge_is_in_the_app(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    assert fix_check.clip_judge is None

    a_clip_redone(fake_model, say, steps)

    assert not FixCheck.objects.exists()
    assert "check_fix" not in paid_for()
    assert [ad.version for ad in ads()] == [1, 2]


def test_a_clip_fix_that_passes_the_clip_judge_shows_the_new_ad(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, str, str]] = []

    def judge(_job: Any, old: str, new: str, fix: str) -> fix_check.Settled:
        asked.append((old, new, fix))
        return fix_check.Settled("passed", "", [{"verdict": "fixed"}])

    monkeypatch.setattr(fix_check, "clip_judge", judge)
    old_clip = latest("clip", 2)

    a_clip_redone(fake_model, say, steps)

    new_clip = latest("clip", 2)
    assert asked == [(old_clip.file, new_clip.file, TURN_FIX)]
    check = FixCheck.objects.get()
    assert (check.old, check.new, check.verdict) == (old_clip, new_clip, "passed")
    assert [ad.version for ad in ads()] == [1, 2]
    assert "The check of scene 2's fix found the change in the new clip." in told(2)


def test_a_clip_fix_that_fails_the_clip_judge_shows_both_clips_and_no_new_ad(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    api: APIClient,
    session_id: str,
) -> None:
    hovering = "The hand still only hovers beside the mug; it never turns it."
    monkeypatch.setattr(
        fix_check,
        "clip_judge",
        lambda *_: fix_check.Settled("failed", hovering, [{"verdict": "not fixed"}]),
    )
    old_clip = latest("clip", 2)

    a_clip_redone(fake_model, say, steps)

    new_clip = latest("clip", 2)
    assert FixCheck.objects.get().verdict == "failed"
    # No new ad is shown from a clip that failed: the shop owner decides first.
    assert [ad.version for ad in ads()] == [1]
    notice = messages_of(api, session_id)[-2]
    assert notice["text"] == (
        f"Scene 2's new clip may not have your change: {hovering} Here are the old clip and "
        "the new one. Say go ahead to use the new clip in the ad anyway, or tell me what to "
        "change."
    )
    assert [file["url"].rsplit("/", 1)[-1] for file in notice["attachments"]] == [
        old_clip.file.rsplit("/", 1)[-1],
        new_clip.file.rsplit("/", 1)[-1],
    ]
    assert told(2).endswith(
        f"The check of scene 2's fix found the change isn't in the new clip: {hovering} The "
        "chat shows the shop owner the old clip and the new one, and why. Don't assemble the "
        "ad or say it's fixed: wait for their answer. If they say go ahead, assemble the ad "
        "with assemble_ad; if they say what to change, restate a new fix."
    )

    owner_says(fake_model, say, "Go ahead", ("assemble_ad", {}))
    assert [ad.version for ad in ads()] == [1, 2]


# --- A line fix asks no judge ------------------------------------------------------------------


def test_a_line_fix_is_never_checked(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],  # noqa: F811
) -> None:
    owner_says(fake_model, say, "Scene 3 should mention the sale", restating((3, SALE_FIX, "line")))
    owner_says(fake_model, say, "Yes", ("change_line", {"scene": 3, "line": SALE}))
    owner_says(fake_model, say, "Go", picture_of(3, None))
    steps.run_held()

    assert Scene.objects.get(number=3).line == SALE
    assert not FixCheck.objects.exists()
    assert "check_fix" not in paid_for()


# --- The verdict from the judge's answers -------------------------------------------------------


FIXED = {"verdict": "fixed", "still_wrong": None}


def unfixed(why: str) -> dict[str, Any]:
    return {"verdict": "not fixed", "still_wrong": why}


@pytest.mark.parametrize(
    ("answers", "verdict", "reason"),
    [
        ([FIXED, FIXED], "passed", ""),
        ([unfixed("a"), FIXED, FIXED], "passed", ""),
        ([unfixed("a"), unfixed("b")], "failed", "a"),
        ([FIXED, unfixed("a"), unfixed("b")], "failed", "a"),
        ([None, unfixed("a"), unfixed("b")], "failed", "a"),
        ([None, None, None], "unchecked", ""),
        # Two answers that agree settle it; fewer, and it wasn't checked.
        ([None, None, unfixed("a")], "unchecked", ""),
        ([None, FIXED, None], "unchecked", ""),
        ([FIXED, None, unfixed("a")], "unchecked", ""),
    ],
)
def test_the_verdict_is_settled_by_two_answers_of_three(
    answers: list[dict[str, Any] | None], verdict: str, reason: str
) -> None:
    given = iter(answers)
    settled = fix_check.settle(lambda _ask: next(given))

    assert (settled.verdict, settled.reason) == (verdict, reason)
    assert settled.answers == [answer for answer in answers if answer is not None]


def test_a_not_fixed_answer_must_say_what_is_still_wrong() -> None:
    with pytest.raises(ValueError, match="must say what is still wrong"):
        fix_check.FixVerdict(old_shows="a", new_shows="b", verdict="not fixed", still_wrong=" ")
