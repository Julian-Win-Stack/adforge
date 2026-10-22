"""The whole fix loop through the chat (route B steps 1-2, #140; fix redesign): the mug's ad
is made from the shop owner's first message, as in test_end_to_end.py, then the shop owner
writes a note on a scene, a plan for it is shown with what it will cost, the shop owner says
yes, code makes only the smallest step of that scene again, and the new ad is assembled and
shown. Each ticket tested its own piece; this proves the pieces fit together.

The plan is the decider's, recorded and run by code (tests/fixing.py), so these tests prove
the worker's part of a fix and the messages, not the decider's judgement (test_fix_wiring.py
drives the decider through the chat). Every outside service is faked at the gateway; ffmpeg
runs for real on tiny clips."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models.signals import post_save
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.models import SystemNote
from agents.money import dollars
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import work
from jobs.models import Fix, FixPlan, Job, ProducedItem, Scene, SceneStep

from .agent_tries import agent_films
from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    JUDGED_CLIP,
    HeldSteps,
    given_to_the_producer,
    paid_for,
    producer_turns,
    results_of,
)
from .fixing import fixed, plan_row, planned, say_yes
from .test_end_to_end import TALKING, ad_made_through_the_chat  # noqa: F401 (a fixture)
from .test_producer_auto_fix import shown_clips
from .test_producer_restarts import WorkerStopped, the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]
Call = tuple[str, dict[str, Any]]

# Scene 2 is the mug turned in a hand (B-roll); scenes 1 and 3 have her talking.
HOVERS = "Scene 2: the hand just hovers, it should turn the mug all the way round."
TURN_FIX = "In scene 2, the hand turns the mug all the way round on the workbench."
TURN_REWRITE: dict[str, Any] = {
    "motion_prompt": "The hand turns the mug all the way round; the camera holds still.",
    "motion_prompt_reason": "The note asks for a full turn.",
}
LIFT_FIX = "In scene 2, the hand lifts the mug off the workbench, then turns it."
LIFT_REWRITE: dict[str, Any] = {
    "motion_prompt": "The hand lifts the mug off the workbench, then turns it.",
    "motion_prompt_reason": "The second note asks for the lift.",
}
WRONG_SIDE = "Scene 2: the handle is on the wrong side, it should face the camera."
HANDLE_FIX = "In scene 2, the mug's handle faces the camera."
HANDLE_SHOWING: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its handle.",
    "prompt": "A hand holds the mug with its handle facing the camera in a sunny workshop.",
    "prompt_reason": "The fix asks for the handle to face the camera.",
    "motion_prompt": "The hand turns the mug slowly; the camera holds still.",
    "motion_prompt_reason": "Turning it shows the mug from every side.",
}
SALE = "On sale now for $19.00."
SALE_FIX = f'In scene 3, she says "{SALE}"'
BOTH_HANDS = "In scene 1, she holds the mug up beside her face with both hands."
# What the clip judge sees wrong with a redo of scene 2's clip, and the remake written from it.
HALF_TURN = "The hand stops at 1.5 s with the mug half turned. A real turn goes all the way round."
TURN_REMADE: dict[str, Any] = {
    "motion_prompt": "The hand turns the mug a full circle on the bench; the camera holds still.",
    "motion_prompt_reason": "The judge saw the turn stop halfway.",
}


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: Call) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, if any, then
    replies."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="On it."))
    say(text)


def ads() -> list[ProducedItem]:
    return list(Job.objects.get().produced.filter(kind="finished_ad").order_by("version"))


def latest(kind: str, scene: int) -> ProducedItem:
    return ProducedItem.objects.filter(kind=kind, scene__number=scene).latest("version")


def made_for(*scenes: int) -> set[tuple[str, int, int, str]]:
    """Everything made for `scenes`: kind, scene, version and file."""
    return {
        (item.kind, item.scene.number, item.version, item.file)  # type: ignore[union-attr]
        for item in ProducedItem.objects.filter(scene__number__in=scenes)
    }


def held_kinds(steps: HeldSteps) -> list[tuple[int, str]]:
    """Each held step's scene and kind, oldest first."""
    return [
        (step.scene.number, step.kind)
        for step in (SceneStep.objects.get(pk=pk) for pk in steps.held)
    ]


def half_turn() -> dict[str, Any]:
    """The clip judge's MOTION answer when it sees the turn stop halfway."""
    return {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": HALF_TURN, "back_to_normal_at": "never"},
    }


@pytest.fixture
def shown(ad_made_through_the_chat: None) -> list[str]:  # noqa: F811
    """The mug's first ad, made through the chat and shown; what was paid for so far."""
    assert [ad.version for ad in ads()] == [1]
    return paid_for()


def clip_fixed(
    fake_model: FakeModel,
    steps: HeldSteps,
    plan: FixPlan,
    yes: str = "yes",
    rewrite: dict[str, Any] = TURN_REWRITE,
) -> None:
    """The shop owner says yes to `plan`, of a new clip for scene 2; its clip is made again
    and lands."""
    say_yes(plan, yes)
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", rewrite)
    steps.run_next()


def notes(kind: str) -> list[str]:
    """The system notes of `kind` the producer is told, oldest first."""
    return list(
        SystemNote.objects.filter(kind=kind)
        .order_by("created_at", "id")
        .values_list("text", flat=True)
    )


# --- 1. A clip note: planned, confirmed, one scene's clip made again, a new ad ------------------


def test_a_clip_note_makes_only_that_scenes_clip_again_and_a_new_ad(
    fake_model: FakeModel, steps: HeldSteps, shown: list[str]
) -> None:
    first_ad = ads()[0]
    untouched = made_for(1, 3)
    picture_2 = latest("starting_picture", 2)
    old_clip_2 = latest("clip", 2)

    plan = planned(plan_row(2, "new_clip", change=TURN_FIX), note=HOVERS)

    # Planned with its cost, and nothing made before the yes.
    row = plan.scenes.get()
    assert (row.scene.number, row.action, plan.state) == (2, "new_clip", "proposed")
    assert row.estimate_usd > 0
    assert not Fix.objects.exists()
    assert steps.held == []
    assert paid_for() == shown

    clip_fixed(fake_model, steps, plan, "Yes, do that.")

    fix = Fix.objects.get()
    assert (fix.scene.number, fix.fix, fix.redo) == (2, TURN_FIX, "clip")
    assert [note["text"] for note in fix.notes] == [HOVERS]
    assert fix.confirmed_by is not None and fix.confirmed_by.text == "Yes, do that."
    new_clip_2 = latest("clip", 2)
    assert new_clip_2.version == 2
    assert new_clip_2.picture == picture_2
    assert new_clip_2.step.fix == fix  # type: ignore[union-attr]
    # Scenes 1 and 3, and scene 2's picture and audio, are as they were.
    assert made_for(1, 3) == untouched
    assert latest("starting_picture", 2) == picture_2
    # The new ad, from scene 1's and 3's old clips and scene 2's new one, assembled by code.
    assert [ad.version for ad in ads()] == [1, 2]
    new_ad = ads()[1]
    old_cuts = {cut["scene"]: cut["clip"] for cut in first_ad.cuts}
    assert [(cut["scene"], cut["clip"]) for cut in new_ad.cuts] == [
        (1, old_cuts[1]),
        (2, new_clip_2.pk),
        (3, old_cuts[3]),
    ]
    assert old_cuts[2] == old_clip_2.pk
    # The producer is told the clip's cost and that the ad is shown.
    (done,) = notes(SystemNote.Kind.SCENE_DONE)
    assert done == f"Scene 2's new clip is done. It cost {dollars(fix.cost_usd())}."
    assert notes(SystemNote.Kind.AD_SHOWN) == [
        "Every scene of the plan is done, so the new ad (version 2) was assembled and shown "
        "to the shop owner."
    ]
    # Only the rewrite and the one clip were paid for.
    assert paid_for()[len(shown) :] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]


# --- 2. A picture note: a new picture un-finishes the scene, then its clip, then the ad ------


def test_a_picture_note_makes_the_picture_then_the_clip_then_the_ad(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    untouched = made_for(1, 3)
    fixed(2, "new_picture", change=HANDLE_FIX, note=WRONG_SIDE)
    assert Fix.objects.get().redo == "picture"
    assert held_kinds(steps) == [(2, "starting_picture")]
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    steps.run_next()

    # The new picture plans the scene again, and passed its checks, so code makes its clip,
    # with no note: the ad can't be assembled from the old clip meanwhile.
    assert latest("starting_picture", 2).version == 2
    assert Scene.objects.get(number=2).status == "planned"
    assert held_kinds(steps) == [(2, "clip")]
    owner_says(fake_model, say, "Is it ready?", ("assemble_ad", {}))
    assert results_of("assemble_ad")[-1].startswith("Refused: scene 2's clip is still being made.")
    assert [ad.version for ad in ads()] == [1]
    assert SceneStep.objects.get(pk=steps.held[0]).note == ""
    steps.run_next()

    new_clip_2 = latest("clip", 2)
    assert new_clip_2.picture == latest("starting_picture", 2)
    assert new_clip_2.step.fix == Fix.objects.get()  # type: ignore[union-attr]
    assert Scene.objects.get(number=2).status == "finished"
    assert made_for(1, 3) == untouched
    assert [ad.version for ad in ads()] == [1, 2]
    assert [cut["clip"] for cut in ads()[1].cuts][1] == new_clip_2.pk
    # No rewrite: the clip moves as the new picture was planned to.
    assert "rewrite_clip_prompt" not in paid_for()[len(shown) :]
    assert paid_for()[len(shown) :].count("make_broll_clip") == 1


# --- 3. Nothing is made again before the shop owner's yes --------------------------------------
# --- 4. An action in a talking scene can't be done ---------------------------------------------
# Both are proved by code's plan: test_run_plan.py (the rule can'ts) and test_fix_wiring.py
# (nothing made before the yes).

# --- 5. Two notes in one message: one plan, one yes, both redos at once, one ad --------------
# Proved by code's plan: test_run_plan.py (every scene lands, the ad is assembled once, and
# the plan's estimate in all).

# --- 7. A second note on the same scene after its fix ------------------------------------------


def test_a_second_note_after_the_fix_makes_the_same_step_again(
    fake_model: FakeModel, steps: HeldSteps, shown: list[str]
) -> None:
    clip_fixed(fake_model, steps, planned(plan_row(2, "new_clip", change=TURN_FIX), note=HOVERS))

    again = "Still not right: lift it off the bench first."
    clip_fixed(
        fake_model,
        steps,
        planned(plan_row(2, "new_clip", change=LIFT_FIX), note=again),
        rewrite=LIFT_REWRITE,
    )
    second = Fix.objects.latest("id")
    assert [note["text"] for note in second.notes] == [again]

    assert latest("clip", 2).version == 3
    assert latest("clip", 2).step.fix == second  # type: ignore[union-attr]
    assert [ad.version for ad in ads()] == [1, 2, 3]
    assert [cut["clip"] for cut in ads()[2].cuts][1] == latest("clip", 2).pk
    # Each fix's cost is its own.
    first = Fix.objects.earliest("id")
    assert first.cost_usd() > 0 and second.cost_usd() > 0
    assert set(first.steps.all()).isdisjoint(second.steps.all())


# --- 8. The chat sees each clip with its scene, and when it is being made -----------------------


def test_the_chat_is_shown_the_new_clip_with_its_scene_and_when_it_was_being_made(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
) -> None:
    clip_fixed(fake_model, steps, planned(plan_row(2, "new_clip", change=TURN_FIX), note=HOVERS))

    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    # The scene's clips, each shown with its scene number, the new one last.
    videos = [
        (attached["url"], attached["scene"])
        for message in messages
        for attached in message["attachments"]
        if attached["kind"] == "video"
    ]
    scene_2 = [url for url, scene in videos if scene == 2]
    assert scene_2[-1] == f"/media/{latest('clip', 2).file}"
    assert len(scene_2) == 2
    # The finished ads carry no scene.
    assert {scene for url, scene in videos if url.endswith(tuple(ad.file for ad in ads()))} == {
        None
    }
    # Being remade is signalled before the new clip, for scene 2's clip.
    remaking = [message for message in messages if message["text"] == "Remaking scene 2's clip."]
    assert len(remaking) == 1
    shown_at = next(
        message["seq"]
        for message in messages
        for attached in message["attachments"]
        if attached["url"] == scene_2[-1]
    )
    assert remaking[0]["seq"] < shown_at


def test_the_producer_is_given_the_plans_notes_not_the_chats_signals(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    clip_fixed(fake_model, steps, planned(plan_row(2, "new_clip", change=TURN_FIX), note=HOVERS))

    owner_says(fake_model, say, "Is it done?")

    given = str(given_to_the_producer(producer_turns()))
    assert "Remaking scene 2's clip." not in given
    assert "A fix plan is shown to the shop owner" in given
    assert "Scene 2's new clip is done." in given


# --- A redo that fails ---------------------------------------------------------------------------


def test_a_fixs_redo_that_fails_pays_for_no_clip_and_the_producer_is_told(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
) -> None:
    turns = producer_turns()
    fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    # The rewrite's answer can't be used, so the step fails before any video is paid for.
    fake_model.respond("rewrite_clip_prompt", {"motion_prompt": " ", "nonsense": 1})
    steps.run_next()

    assert "make_broll_clip" not in paid_for()[len(shown) :]
    assert (
        SceneStep.objects.filter(kind="clip")
        .exclude(tool_call__agent="fixing_agent")
        .latest("id")
        .status
        == "failed"
    )
    assert [ad.version for ad in ads()] == [1]
    # The chat is told nothing was made, and why in plain words, so it stops showing the
    # clip as being made.
    texts = [message["text"] for message in api.get(f"/api/sessions/{session_id}/messages/").json()]
    assert texts.index(
        "Scene 2's clip couldn't be made: a model gave an answer we couldn't use."
    ) > texts.index("Remaking scene 2's clip.")
    # The producer is told by a note, not woken.
    (error,) = notes(SystemNote.Kind.ERROR)
    assert error.startswith("Scene 2's new clip couldn't be made: ")
    assert producer_turns() == turns


# --- A worker restart in the middle of a fix's redo ---------------------------------------------


def test_a_fixs_redo_run_again_after_the_worker_stopped_pays_and_shows_everything_once(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
) -> None:
    fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    (step_id,) = steps.held
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    # The worker stops just after the new clip is kept, before the step finishes.
    with (
        the_worker_stops(
            post_save, ProducedItem, when=lambda item: item.kind == "clip" and item.version == 2
        ),
        pytest.raises(WorkerStopped),
    ):
        steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    # The step is handed out again, as Celery does with a task its worker never finished.
    tasks.run_scene_step(step_id)

    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert latest("clip", 2).version == 2
    assert paid_for()[len(shown) :] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]
    assert [ad.version for ad in ads()] == [1, 2]
    assert Message.objects.filter(attachments__file=ads()[1].file).count() == 1
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    texts = [message["text"] for message in messages]
    assert texts.count("Remaking scene 2's clip.") == 1
    assert len(notes(SystemNote.Kind.AD_SHOWN)) == 1


# --- 9. A line note: the scene's picture, audio, transcript and clip are made again ----------


def test_a_line_note_remakes_that_scene_for_its_new_line_and_captions_the_new_ad(
    fake_model: FakeModel, steps: HeldSteps, shown: list[str]
) -> None:
    untouched = made_for(1, 2)
    plan = planned(
        plan_row(3, "new_line", change=SALE_FIX, line=SALE),
        note="The last scene should say it's on sale, not just the price.",
    )
    assert plan.scenes.get().estimate_usd > 0
    assert paid_for() == shown

    say_yes(plan)
    scene = Scene.objects.get(number=3)
    assert (scene.line, scene.status) == (SALE, "planned")

    # Code makes the audio, then the transcript, then the clip, from the picture it has.
    steps.run_next()
    steps.run_next()
    assert [ad.version for ad in ads()] == [1]
    assert held_kinds(steps) == [(3, "clip")]
    steps.run_next()

    assert {
        kind: latest(kind, 3).version
        for kind in ("starting_picture", "line_audio", "transcript", "clip")
    } == {"starting_picture": 1, "line_audio": 2, "transcript": 2, "clip": 2}
    assert made_for(1, 2) == untouched
    fix = Fix.objects.get()
    assert {step.kind for step in fix.steps.all()} == {"line_audio", "transcript", "clip"}
    # The fix's clip assembled the new ad, captioned with the new words.
    assert [ad.version for ad in ads()] == [1, 2]
    captioned = " ".join(caption["text"] for caption in ads()[1].captions)
    assert "On sale now" in captioned
    assert "Just $24.00" not in captioned
    # Only that scene's steps were paid for: no fact check, no judge.
    assert paid_for()[len(shown) :] == [
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]


# --- 10. The clip judge fails the fix's redo: the fixing agent films it again, in its step ----


def test_a_fixs_redo_the_judge_fails_is_filmed_again_by_the_agent_and_its_try_makes_the_new_ad(
    fake_model: FakeModel,
    steps: HeldSteps,
    shown: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    # The judge fails the redo on 2 of its 3 asks, and passes the agent's try on all 3.
    # A fix's clip: each answer also says the owner's change is made.
    fixed_ = {"owner_change": CLIP_FIXED}
    fake_model.respond(
        "check_clip_motion",
        half_turn() | fixed_,
        half_turn() | fixed_,
        *[CLIP_MOTION_OK | fixed_] * 4,
    )
    agent_films(monkeypatch, TURN_REMADE["motion_prompt"])
    steps.run_next()

    # The redo and the try are the step's, the try the scene's clip.
    step = (
        SceneStep.objects.filter(kind="clip", scene__number=2)
        .exclude(tool_call__agent="fixing_agent")
        .latest("id")
    )
    redo, tried = work.clip_tries(step)
    assert (redo.version, redo.remake, tried.version, tried.remake) == (2, None, 3, 1)
    assert latest("clip", 2) == tried
    assert tried.motion_prompt == TURN_REMADE["motion_prompt"]
    # The new ad holds the try, shown once; the chat never shows the clip the judge failed.
    assert [ad.version for ad in ads()] == [1, 2]
    assert [cut["clip"] for cut in ads()[1].cuts][1] == tried.pk
    assert Message.objects.filter(attachments__file=ads()[1].file).count() == 1
    assert shown_clips().count(tried.file) == 1
    assert redo.file not in shown_clips()
    assert paid_for()[len(shown) :] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
        # The redo's two failing answers are asked whether they name the same fault.
        "agree_on_fault",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]


# --- 11. The video service fails the fix's redo ------------------------------------------------


@pytest.mark.parametrize(
    ("purpose", "answers", "reason", "told", "videos_asked"),
    [
        (
            "collect_clip",
            [{"state": "failed", "error": "The picture was too dark."}],
            "the video model couldn't make the clip (The picture was too dark.).",
            "the video model couldn't make it.",
            # Waiting on the one that failed would only fail again: a fresh one is asked for.
            2,
        ),
        (
            "make_clip",
            [OutsideServiceDown("fal answered 503")] * 3,
            "an outside service stayed down after several tries (The model provider was still "
            "down after 3 tries: fal answered 503).",
            "an outside service stayed down.",
            # Nothing was paid for while the service was down.
            1,
        ),
        (
            "collect_clip",
            [OutsideServiceDown("fal answered 503")] * 3,
            "an outside service stayed down after several tries (The model provider was still "
            "down after 3 tries: fal answered 503).",
            "an outside service stayed down.",
            # The clip paid for is waited for again rather than paid for again.
            1,
        ),
    ],
    ids=["clip_failed", "outage_asking", "outage_collecting"],
)
def test_a_fixs_redo_the_video_service_fails_fails_its_step_and_planned_again_is_made(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
    purpose: str,
    answers: list[Any],
    reason: str,
    told: str,
    videos_asked: int,
) -> None:
    plan = fixed(2, "new_clip", change=TURN_FIX, note=HOVERS)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    fake_model.respond(purpose, *answers)
    asked_before = len(fake_model.clips_submitted)
    steps.run_next()

    step = (
        SceneStep.objects.filter(kind="clip", scene__number=2)
        .exclude(tool_call__agent="fixing_agent")
        .latest("id")
    )
    assert (step.status, step.reason) == ("failed", reason)
    assert notes(SystemNote.Kind.ERROR) == [f"Scene 2's new clip couldn't be made: {reason}"]
    assert plan.scenes.get().error == f"Scene 2's new clip couldn't be made: {reason}"
    assert latest("clip", 2).version == 1
    assert [ad.version for ad in ads()] == [1]
    assert not [each for each in paid_for()[len(shown) :] if each.startswith("check_")]
    # The chat is told nothing was made, and why in plain words, so it stops showing the
    # clip as being made.
    texts = [message["text"] for message in api.get(f"/api/sessions/{session_id}/messages/").json()]
    assert texts.index(f"Scene 2's clip couldn't be made: {told}") > texts.index(
        "Remaking scene 2's clip."
    )
    # The fix stays said yes to, with nothing made for it.
    fix = Fix.objects.get()
    assert fix.confirmed_by is not None and fix.confirmed_by.text == "yes"
    assert not ProducedItem.objects.filter(step__fix=fix)
    # A producer that puts the ad together anyway finds the old one, already shown.
    owner_says(fake_model, say, "Put it together then", ("assemble_ad", {}))
    assert results_of("assemble_ad")[-1].startswith("The ad was already assembled")
    assert Message.objects.filter(attachments__file=ads()[0].file).count() == 1

    # The shop owner asks for the same change again, and says yes to its plan.
    again = fixed(2, "new_clip", change=TURN_FIX, note="Try again")
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    steps.run_next()

    assert SceneStep.objects.filter(kind="clip", scene__number=2).exclude(
        tool_call__agent="fixing_agent"
    ).latest("id").fix == (again.scenes.get().fix)
    assert latest("clip", 2).version == 2
    assert [ad.version for ad in ads()] == [1, 2]
    assert len(fake_model.clips_submitted) - asked_before == videos_asked
