"""The whole fix loop through the chat (route B steps 1-2, #140): the mug's ad is made from
the shop owner's first message, as in test_end_to_end.py, then the shop owner writes a note
on a scene, the producer restates it as a fix with what it will cost, the shop owner says
yes, only the smallest step of that scene is made again, and the new ad is assembled and
shown. Each ticket tested its own piece; this proves the pieces fit together.

The producer's turns are scripted to follow its instructions word for word: these tests
prove the tools, the yes guard and the messages, not the model's judgement. Every outside
service is faked at the gateway; ffmpeg runs for real on tiny clips."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents.producer import INSTRUCTIONS
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs.models import Fix, Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, given_to_the_producer, paid_for, producer_turns, results_of
from .test_end_to_end import TALKING, ad_made_through_the_chat  # noqa: F401 (a fixture)

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
SLOWER_SPIN = "In scene 2, the hand lifts the mug off the workbench, then turns it."
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
WAVE = "In scene 1 she should wave at the camera."
WAVE_FIX = "In scene 1, she waves at the camera."
BOTH_HANDS = "In scene 1, she holds the mug up beside her face with both hands."


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: Call) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, if any, then
    replies."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="On it."))
    say(text)


def restating(*fixes: tuple[int, str, str]) -> Call:
    """A restate_fix call for each (scene, fix, redo)."""
    return (
        "restate_fix",
        {"fixes": [{"scene": scene, "fix": fix, "redo": redo} for scene, fix, redo in fixes]},
    )


def clip_of(scene: int, note: str | None = None) -> Call:
    return ("make_clip", {"scene": scene, "note": note})


def picture_of(scene: int, note: str | None) -> Call:
    return ("make_starting_picture", {"scene": scene, "note": note})


def step_lands(fake_model: FakeModel, steps: HeldSteps, *calls: Call) -> None:
    """The oldest held step runs; told it finished, the producer makes `calls`, if any, then
    tells the shop owner."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="That part's ready."))
    steps.run_next()


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


def told(scene: int, kind: str = "clip") -> str:
    """What the producer was told when scene `scene`'s latest `kind` step finished."""
    step = SceneStep.objects.filter(kind=kind, scene__number=scene).latest("id")
    return step.result


@pytest.fixture
def shown(ad_made_through_the_chat: None) -> list[str]:  # noqa: F811
    """The mug's first ad, made through the chat and shown; what was paid for so far."""
    assert [ad.version for ad in ads()] == [1]
    return paid_for()


def clip_fixed(
    fake_model: FakeModel, say: Say, steps: HeldSteps, fix: str, rewrite: dict[str, Any]
) -> None:
    """The shop owner says yes to scene 2's restated clip fix; its clip is made again and
    lands."""
    owner_says(fake_model, say, "Yes, do that.", clip_of(2, fix))
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", rewrite)
    step_lands(fake_model, steps)


# --- 1. A clip note: restated, confirmed, one scene's clip made again, a new ad ------------------


def test_a_clip_note_makes_only_that_scenes_clip_again_and_a_new_ad(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    first_ad = ads()[0]
    untouched = made_for(1, 3)
    picture_2 = latest("starting_picture", 2)
    old_clip_2 = latest("clip", 2)

    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))

    # Restated with its cost, and nothing made before the yes.
    fix = Fix.objects.get()
    assert (fix.scene.number, fix.redo, fix.confirmed_by) == (2, "clip", None)
    assert [note["text"] for note in fix.notes] == [HOVERS]
    assert results_of("restate_fix")[-1].startswith("Recorded the fix. Scene 2: about $")
    assert steps.held == []
    assert paid_for() == shown

    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)

    fix.refresh_from_db()
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
    # The producer is told the clip's cost, the fix's running cost and that the ad is shown.
    assert " It cost $" in told(2)
    assert "Scene 2's fix has cost $" in told(2)
    assert told(2).endswith(
        f"the ad was assembled again with it (version 2, {new_ad.seconds:g} seconds) and shown "
        "to the shop owner in the chat. Ask them to tell you if it's fixed."
    )
    # Only the rewrite and the one clip were paid for.
    assert paid_for()[len(shown) :] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
    ]


def test_the_producer_assembling_after_the_fix_makes_and_shows_nothing_again(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str], api: APIClient
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)

    # As the instructions don't ask, but a model might.
    owner_says(fake_model, say, "Great, put it together", ("assemble_ad", {}))

    assert results_of("assemble_ad")[-1].startswith("The ad was already assembled")
    assert [ad.version for ad in ads()] == [1, 2]
    assert Message.objects.filter(attachments__file=ads()[1].file).count() == 1


def test_the_same_fix_again_is_already_made_and_paid_for_once(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)
    paid = paid_for()

    owner_says(fake_model, say, "Do it again", clip_of(2, TURN_FIX))

    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio with this note "
        "(version 2), so nothing was made or paid for again."
    )
    assert paid_for() == paid


# --- 2. A picture note: a new picture un-finishes the scene, then its clip, then the ad ------


def test_a_picture_note_makes_the_picture_then_the_clip_then_the_ad(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    untouched = made_for(1, 3)
    owner_says(fake_model, say, WRONG_SIDE, restating((2, HANDLE_FIX, "picture")))
    assert Fix.objects.get().redo == "picture"

    owner_says(fake_model, say, "Yes", picture_of(2, HANDLE_FIX))
    assert held_kinds(steps) == [(2, "starting_picture")]
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING)
    step_lands(fake_model, steps)

    # The new picture plans the scene again: the ad can't be assembled from the old clip.
    assert latest("starting_picture", 2).version == 2
    assert Scene.objects.get(number=2).status == "planned"
    assert "Scene 2 is no longer finished: make its clip from this picture." in told(
        2, "starting_picture"
    )
    assert " It cost $" in told(2, "starting_picture")
    owner_says(fake_model, say, "Is it ready?", ("assemble_ad", {}))
    assert results_of("assemble_ad")[-1].startswith("Refused: scene 2 isn't finished")
    assert [ad.version for ad in ads()] == [1]

    # Told the picture is ready, the producer makes its clip with no note.
    owner_says(fake_model, say, "Go on", clip_of(2))
    assert held_kinds(steps) == [(2, "clip")]
    step_lands(fake_model, steps)

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


@pytest.mark.parametrize(
    "redo", [clip_of(2, TURN_FIX), picture_of(2, TURN_FIX)], ids=["clip", "picture"]
)
def test_a_redo_with_no_restatement_is_refused_and_nothing_is_paid(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str], redo: Call
) -> None:
    owner_says(fake_model, say, HOVERS, redo)

    assert results_of(redo[0])[-1] == (
        "Refused: scene 2 already has a clip, so changing it is a fix, and no fix was restated "
        "for it. Call restate_fix first and wait for the shop owner's answer. Nothing was done."
    )
    assert steps.held == []
    assert paid_for() == shown


def test_a_redo_in_the_same_turn_as_the_restatement_is_refused_and_nothing_is_paid(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")), clip_of(2, TURN_FIX))

    assert results_of("make_clip")[-1].startswith("Refused: ")
    assert "answer" in results_of("make_clip")[-1]
    assert steps.held == []
    assert paid_for() == shown
    assert Fix.objects.get().confirmed_by is None


# --- 4. An action in a talking scene can't be done ---------------------------------------------


def test_an_action_in_a_talking_scene_is_refused_and_the_instructions_say_it_cant(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, WAVE, restating((1, WAVE_FIX, "clip")))

    assert results_of("restate_fix")[-1] == (
        "Refused: scene 1 shows the person talking, and a talking clip's movement can't be "
        "changed: the video model ignores what it's asked about movement in a talking clip. "
        "Restate it as a new starting picture instead. Nothing was done."
    )
    assert not Fix.objects.exists()
    assert paid_for() == shown
    # What the producer is told to say plainly about it.
    assert "change what the person does in a scene where they talk" in " ".join(
        INSTRUCTIONS.split()
    )


# --- 5. Two notes in one message: one restatement, one yes, both redos at once, one ad -------


def test_two_notes_at_once_are_restated_together_and_make_one_new_ad(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    untouched = made_for(3)
    owner_says(
        fake_model,
        say,
        f"{HOVERS} And in the first scene she should hold the mug with both hands.",
        restating((1, BOTH_HANDS, "picture"), (2, TURN_FIX, "clip")),
    )
    assert Fix.objects.count() == 2
    assert "in all" in results_of("restate_fix")[-1]

    owner_says(fake_model, say, "Yes to both", picture_of(1, BOTH_HANDS), clip_of(2, TURN_FIX))
    # Both started at once.
    assert held_kinds(steps) == [(1, "starting_picture"), (2, "clip")]
    fake_model.respond("choose_starting_picture", TALKING)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    # Scene 1's picture lands; told, the producer makes its clip.
    step_lands(fake_model, steps, clip_of(1))
    # Scene 2's clip lands while scene 1's is still being made: nothing assembled yet.
    step_lands(fake_model, steps)
    assert [ad.version for ad in ads()] == [1]
    assert "once scene 1 is finished" in told(2)
    # Scene 1's clip lands last and assembles the one new ad, with both new clips.
    assert held_kinds(steps) == [(1, "clip")]
    step_lands(fake_model, steps)

    assert [ad.version for ad in ads()] == [1, 2]
    assert [cut["clip"] for cut in ads()[1].cuts] == [
        latest("clip", 1).pk,
        latest("clip", 2).pk,
        latest("clip", 3).pk,
    ]
    assert (latest("clip", 1).version, latest("clip", 2).version) == (2, 2)
    assert made_for(3) == untouched
    assert "the ad was assembled again" in told(1)


# --- 6. A note while the scene's redo is running waits for it ---------------------------------


def test_a_note_while_the_scenes_redo_runs_is_made_once_that_redo_lands(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    owner_says(fake_model, say, "Yes", clip_of(2, TURN_FIX))
    assert held_kinds(steps) == [(2, "clip")]

    # While it runs, the shop owner writes again about scene 2; restated and confirmed.
    owner_says(
        fake_model,
        say,
        "Actually, lift it off the bench first.",
        restating((2, SLOWER_SPIN, "clip")),
    )
    # A model that doesn't wait, as the instructions tell it to, is refused as busy.
    owner_says(fake_model, say, "Yes", clip_of(2, SLOWER_SPIN))
    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's clip is already being made. You'll be told when it's ready. "
        "Nothing was done."
    )
    assert held_kinds(steps) == [(2, "clip")]

    # The first redo lands; told, the producer makes the confirmed second fix then.
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    step_lands(fake_model, steps, clip_of(2, SLOWER_SPIN))
    assert held_kinds(steps) == [(2, "clip")]
    second = SceneStep.objects.get(pk=steps.held[0])
    assert second.note == SLOWER_SPIN
    fake_model.respond("rewrite_clip_prompt", LIFT_REWRITE)
    step_lands(fake_model, steps)

    assert latest("clip", 2).version == 3
    # The first fix's clip, already corrected, was never assembled into an ad: the one new
    # ad is assembled once the corrected fix's clip lands.
    first_redo = SceneStep.objects.filter(kind="clip", scene__number=2).order_by("id")[1]
    assert first_redo.result.endswith(
        "The ad will be assembled again, and shown to the shop owner, once scene 2 is finished."
    )
    assert [ad.version for ad in ads()] == [1, 2]
    assert [cut["clip"] for cut in ads()[1].cuts][1] == latest("clip", 2).pk
    assert paid_for()[len(shown) :].count("make_broll_clip") == 2


# --- 7. A second note on the same scene after its fix ------------------------------------------


def test_a_second_note_after_the_fix_makes_the_same_step_again(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)

    owner_says(
        fake_model,
        say,
        "Still not right: lift it off the bench first.",
        restating((2, SLOWER_SPIN, "clip")),
    )
    second = Fix.objects.latest("id")
    assert [note["text"] for note in second.notes] == [
        "Still not right: lift it off the bench first."
    ]
    clip_fixed(fake_model, say, steps, SLOWER_SPIN, LIFT_REWRITE)

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
    say: Say,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)

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


def test_the_producer_is_given_only_the_restatement_and_its_steps_not_the_chats_signals(
    fake_model: FakeModel, say: Say, steps: HeldSteps, shown: list[str]
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    clip_fixed(fake_model, say, steps, TURN_FIX, TURN_REWRITE)

    given = str(given_to_the_producer(producer_turns()))
    assert "Remaking scene 2's clip." not in given
    assert "restate_fix" in given


# --- A redo that fails ---------------------------------------------------------------------------


def test_a_fixs_redo_that_fails_is_tried_again_without_a_new_yes(
    fake_model: FakeModel,
    say: Say,
    steps: HeldSteps,
    shown: list[str],
    api: APIClient,
    session_id: str,
) -> None:
    owner_says(fake_model, say, HOVERS, restating((2, TURN_FIX, "clip")))
    owner_says(fake_model, say, "Yes", clip_of(2, TURN_FIX))
    # The rewrite's answer can't be used, so the step fails before any video is paid for.
    fake_model.respond("rewrite_clip_prompt", {"motion_prompt": " ", "nonsense": 1})
    step_lands(fake_model, steps)

    assert "make_broll_clip" not in paid_for()[len(shown) :]
    assert SceneStep.objects.filter(kind="clip").latest("id").status == "failed"
    assert [ad.version for ad in ads()] == [1]
    # The chat is told nothing was made, so it stops showing the clip as being made.
    texts = [message["text"] for message in api.get(f"/api/sessions/{session_id}/messages/").json()]
    assert texts.index("Scene 2's clip couldn't be made.") > texts.index("Remaking scene 2's clip.")

    # The shop owner already said yes to this fix: the producer tries again on their word.
    owner_says(fake_model, say, "Try again", clip_of(2, TURN_FIX))
    assert held_kinds(steps) == [(2, "clip")]
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    step_lands(fake_model, steps)

    assert latest("clip", 2).version == 2
    assert [ad.version for ad in ads()] == [1, 2]
