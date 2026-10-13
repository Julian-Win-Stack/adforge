"""The auto-fix (#155), driven through the chat: a B-roll clip the clip judge fails is made
again by code, from the same starting picture, up to twice, its video prompt written again
from what the judge saw wrong; the clip with the fewest failed answers is kept, and the
producer is told once, when it's done. The producer's model, the scene models, the video
model and the judge are faked at the gateway (change 9, Clip judge discussion; Julian's yes
2026-10-13 05:03-05:28)."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.types import UnusableReply
from jobs.models import ProducedItem, SceneStep
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_JUDGE_AGAIN,
    CLIP_FROM_JUDGE_RULES,
    SAME_SIDE,
)

from .conftest import (
    CLIP_MOTION_OK,
    JUDGED_CLIP,
    HeldSteps,
    WorkerStopped,
    handoffs,
    paid_for,
    said_yes_to_a_fix,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    LINE_2,
    MOTION,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
)

pytestmark = pytest.mark.django_db(transaction=True)

FLOATS = "The teapot rises at 1.2 s with no hand on it. A real one stays on the bench."
SPINS = "The mug spins at 2 s and shows its back. A real one keeps its front to the camera."
REMADE = "A hand tips the teapot and tea pours into the mug while the teapot stays in the hand."
REMADE_AGAIN = "A hand holds the teapot by its handle and pours tea into the mug."


def failing(problem: str, question: str = "real_motion") -> dict[str, Any]:
    """MOTION's answer when it sees `problem`."""
    return {
        **CLIP_MOTION_OK,
        question: {"verdict": "fail", "problem": problem, "back_to_normal_at": "never"},
    }


def rewrite(prompt: str) -> dict[str, Any]:
    return {"motion_prompt": prompt, "motion_prompt_reason": "It keeps the teapot in the hand."}


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> SceneStep:
    """Have the producer make scene 2's clip, showing the mug; gives its step."""
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    return SceneStep.objects.get(kind="clip", scene__number=2)


def clips() -> list[int]:
    """The version of each of scene 2's clips, oldest first."""
    return list(
        ProducedItem.objects.filter(kind="clip", scene__number=2)
        .order_by("id")
        .values_list("version", flat=True)
    )


def shown_clips() -> list[str]:
    """The file of each clip shown in the chat, oldest first."""
    return list(
        Attachment.objects.filter(kind="video", message__role=Message.Role.AGENT)
        .order_by("id")
        .values_list("file", flat=True)
    )


# --- Fail, then a remake the judge passes -------------------------------------------------


@pytest.fixture
def remade_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    instructed: dict[str, list[str]],  # noqa: F811
) -> SceneStep:
    """Scene 2's first clip fails on the teapot floating; its remake passes."""
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    fake_model.respond("remake_clip_prompt", rewrite(REMADE))
    return clip_of_scene_2(fake_model, steps, say)


def test_a_failed_clip_is_made_again_from_the_same_picture_and_judged_again(
    remade_once: SceneStep,
) -> None:
    assert paid_for()[-(5 + 2 * len(JUDGED_CLIP)) :] == [
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
        "remake_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]
    assert clips_asked("prompt") == [
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {MOTION}",
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {REMADE}",
    ]
    assert clips_asked("starting_picture")[0] == clips_asked("starting_picture")[1]
    assert clips() == [1, 2]
    assert ProducedItem.objects.filter(kind="starting_picture").count() == 1


def test_the_remake_is_written_from_what_the_judge_saw_wrong_with_no_conversation(
    remade_once: SceneStep,
    instructed: dict[str, list[str]],  # noqa: F811
) -> None:
    (handoff,) = handoffs("remake_clip_prompt")
    assert handoff == {
        "scene": 2,
        "line": LINE_2,
        "shows": SHOWS,
        "broll_kind": "showcase",
        "person_shown": "no face",
        "usage": "",
        "result": "",
        "picture_prompt": BROLL_CHOICE["prompt"],
        "motion_prompt": MOTION,
        # Both failed answers saw the same: each is handed over once.
        "faults": [FLOATS],
        "judge_saw": [failing(FLOATS)],
        "owner_fix": None,
        "earlier_try": None,
    }
    assert instructed["remake_clip_prompt"] == [
        f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES['showcase']}\n{CLIP_FROM_JUDGE_RULES}"
    ]
    # No note is rewritten for it: the owner-note path isn't used.
    assert handoffs("rewrite_clip_prompt") == []


def test_the_remake_the_judge_passed_is_kept_shown_and_the_producer_told_once(
    remade_once: SceneStep,
) -> None:
    first, remake = ProducedItem.objects.filter(kind="clip").order_by("id")
    assert (first.remake, remake.remake, remake.motion_prompt) == (None, 1, REMADE)
    assert remake.ruling is not None and remake.ruling["verdict"] == "pass"
    # Only the clip kept reaches the chat.
    assert shown_clips() == [remake.file]
    assert remade_once.status == "finished"
    assert remade_once.result.startswith(
        "Background step finished: scene 2's clip is ready (version 2, 5 seconds)"
    )
    assert (
        f'The clip judge failed the first clip it made ("{FLOATS}"), so it was made again '
        "once, and the judge passed the new one. Scene 2 is finished."
    ) in remade_once.result


# --- Fail, then 2 remakes the judge fails too ----------------------------------------------


@pytest.fixture
def remade_twice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    instructed: dict[str, list[str]],  # noqa: F811
) -> SceneStep:
    """Scene 2's first clip fails 2 answers on the teapot floating, and each remake 3, on the
    mug spinning: the first clip has the fewest failed answers."""
    fake_model.respond(
        "check_clip_motion",
        *[failing(FLOATS)] * 2,
        CLIP_MOTION_OK,
        *[failing(SPINS)] * 3,
        *[failing(SPINS)] * 3,
    )
    fake_model.respond("remake_clip_prompt", rewrite(REMADE), rewrite(REMADE_AGAIN))
    return clip_of_scene_2(fake_model, steps, say)


def test_a_clip_is_made_again_at_most_twice(remade_twice: SceneStep) -> None:
    assert paid_for().count("make_broll_clip") == 3
    assert paid_for().count("check_clip_motion") == 9
    assert clips_asked("prompt")[1:] == [
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {REMADE}",
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {REMADE_AGAIN}",
    ]


def test_the_second_remake_is_also_handed_the_first_remakes_prompt_and_its_faults(
    remade_twice: SceneStep,
    instructed: dict[str, list[str]],  # noqa: F811
) -> None:
    first, second = handoffs("remake_clip_prompt")
    # The first clip's prompt and faults, as the first remake was handed them.
    assert {key: second[key] for key in second if key != "earlier_try"} == {
        key: first[key] for key in first if key != "earlier_try"
    }
    assert second["earlier_try"] == {
        "motion_prompt": REMADE,
        "faults": [SPINS],
        "judge_saw": [failing(SPINS)],
    }
    rules = f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES['showcase']}\n{CLIP_FROM_JUDGE_RULES}"
    assert instructed["remake_clip_prompt"] == [rules, f"{rules}\n{CLIP_FROM_JUDGE_AGAIN}"]


def test_the_clip_with_the_fewest_failed_answers_is_the_scenes_clip_again(
    remade_twice: SceneStep,
) -> None:
    first = ProducedItem.objects.filter(kind="clip").order_by("id").first()
    assert first is not None
    # Brought back as the newest version, so the ad uses it, with nothing paid again.
    assert clips() == [1, 2, 3, 4]
    kept = ProducedItem.objects.get(kind="clip", version=4)
    assert (kept.file, kept.remake, kept.ruling) == (first.file, None, first.ruling)
    assert shown_clips() == [first.file]
    assert remade_twice.result.startswith(
        "Background step finished: scene 2's clip is ready (version 4, 5 seconds)"
    )
    assert (
        f'The clip judge failed the first clip it made ("{FLOATS}"), so it was made again 2 '
        "times, and it failed each remake too. The clip kept is the first clip, the one with "
        f'the fewest faults, and the judge still sees: "{FLOATS}" Scene 2 is finished.'
    ) in remade_twice.result


def test_of_tied_clips_the_newest_is_kept(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_clip_motion",
        *[failing(FLOATS)] * 2,
        CLIP_MOTION_OK,
        *[failing(SPINS)] * 2,
        CLIP_MOTION_OK,
        *[failing(SPINS)] * 2,
    )
    fake_model.respond("remake_clip_prompt", rewrite(REMADE), rewrite(REMADE_AGAIN))

    step = clip_of_scene_2(fake_model, steps, say)

    # Each remake failed 2 answers, as the first clip did: the second remake is kept.
    assert clips() == [1, 2, 3]
    kept = ProducedItem.objects.get(kind="clip", version=3)
    assert (kept.remake, shown_clips()) == (2, [kept.file])
    assert "The clip kept is remake 2, the one with the fewest faults" in step.result


# --- No remake --------------------------------------------------------------------------


def test_a_clip_the_judge_passes_isnt_made_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    step = clip_of_scene_2(fake_model, steps, say)

    assert paid_for().count("make_broll_clip") == 1
    assert handoffs("remake_clip_prompt") == []
    assert clips() == [1]
    assert "judge" not in step.result


def test_a_talking_scene_is_left_as_it_is(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Answers that would fail a clip, were one judged.
    fake_model.respond("check_clip_motion", *[failing(FLOATS)] * 3)
    made_ready(fake_model, steps, say, (1,))
    calling(fake_model, say, ("make_clip", {"scene": 1}))
    run(fake_model, steps)

    assert paid_for().count("make_talking_clip") == 1
    assert not [purpose for purpose in paid_for() if purpose.startswith("check_clip_")]
    assert handoffs("remake_clip_prompt") == []
    step = SceneStep.objects.get(kind="clip", scene__number=1)
    assert (step.status, "judge" in step.result) == ("finished", False)


# --- A clip made from the shop owner's note -----------------------------------------------

HOVERS = "The hand only hovers over the mug; it should pour the tea into it."
NOTED = "Tea pours from the teapot into the mug and steam rises from it."


def test_a_clip_made_from_a_note_is_remade_from_the_notes_prompt_keeping_the_owners_fix(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    clip_of_scene_2(fake_model, steps, say)
    said_yes_to_a_fix(2, HOVERS, "clip")
    fake_model.respond(
        "rewrite_clip_prompt",
        {"motion_prompt": NOTED, "motion_prompt_reason": "The note asks for the pour."},
    )
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    fake_model.respond("remake_clip_prompt", rewrite(REMADE))
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": HOVERS}))
    run(fake_model, steps)

    (handoff,) = handoffs("remake_clip_prompt")
    assert (handoff["motion_prompt"], handoff["owner_fix"]) == (NOTED, HOVERS)
    # The owner-note path is as it was: its rewrite still has the conversation.
    (noted,) = handoffs("rewrite_clip_prompt")
    assert noted["note"] == HOVERS and noted["conversation"]
    assert clips() == [1, 2, 3]
    assert (
        ProducedItem.objects.get(version=3, kind="clip").step
        == SceneStep.objects.filter(kind="clip").last()
    )


# --- After the worker stopped -------------------------------------------------------------


def test_a_remake_under_way_when_the_worker_stopped_is_waited_for_not_paid_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    fake_model.respond(
        "check_clip_motion", *[failing(FLOATS)] * 2, CLIP_MOTION_OK, CLIP_MOTION_OK, WorkerStopped()
    )
    fake_model.respond("remake_clip_prompt", rewrite(REMADE))
    with pytest.raises(WorkerStopped):
        steps.run_next()

    # The step is handed out again, as after a restart.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(SceneStep.objects.get(kind="clip", scene__number=2).pk)

    assert paid_for().count("make_broll_clip") == 2
    assert paid_for().count("collect_broll_clip") == 2
    assert paid_for().count("remake_clip_prompt") == 1
    assert paid_for().count("check_clip_motion") == 6
    assert clips() == [1, 2]
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert "the judge passed the new one" in step.result


# --- A remake that can't be made ----------------------------------------------------------


def test_a_remake_that_cant_be_written_keeps_the_clip_made_and_finishes_the_step(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_motion", failing(FLOATS), failing(FLOATS))
    fake_model.respond(
        "remake_clip_prompt", UnusableReply("no prompt came back", input_tokens=10, output_tokens=0)
    )

    step = clip_of_scene_2(fake_model, steps, say)

    # The clip is made and paid for: the step isn't failed for the remake.
    assert step.status == "finished"
    assert clips() == [1]
    assert paid_for().count("make_broll_clip") == 1
    assert (
        f'The clip judge failed the first clip it made ("{FLOATS}"), and it couldn\'t be made '
        "again, so it is kept as it is. Scene 2 is finished."
    ) in step.result
