"""A B-roll scene's clip made again from a note, driven through the chat (route B step 1,
#132): when a clip's movement is wrong, only the clip is made again, from the same starting
picture and the same audio, with its video prompt rewritten by a backend model from the note.
Nothing else is made or paid for. The producer's model and the scene models are faked at the
gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from agents.producer import MakeClip, _clip_and_transcript, _dollars, normalised_note
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import Fix, ProducedItem, Scene, SceneStep
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_NOTE_RULES,
    SAME_SIDE,
    clip_from_note_instructions,
)

from .conftest import (
    HeldSteps,
    WorkerStopped,
    handoffs,
    paid_for,
    results_of,
    said_yes_to_a_fix,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    LINE_2,
    MOTION,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    run,
    unlabelled,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

HOVERS = "The hand only hovers over the mug; it should pour the tea into it."
NEW_MOTION = "Tea pours from the teapot into the mug and steam rises from it."
REWRITE: dict[str, Any] = {
    "motion_prompt": NEW_MOTION,
    "motion_prompt_reason": "The note asks for the pour, so the tea now pours into the mug.",
}


def clips() -> list[tuple[int, int]]:
    """Each clip made, as (scene, version)."""
    return [
        (clip.scene.number, clip.version)
        for clip in ProducedItem.objects.filter(kind="clip").order_by("id")
        if clip.scene is not None
    ]


def clip_with_note(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    note: str | None,
    *rewrites: dict[str, Any],
) -> None:
    """Have the producer make scene 2's clip with `note`, the rewrite answering `rewrites`:
    once its clip is made, for a fix the shop owner said yes to."""
    noted(fake_model, say, note)
    fake_model.respond("rewrite_clip_prompt", *rewrites)
    run(fake_model, steps)


def noted(fake_model: FakeModel, say: Callable[..., None], note: str | None) -> None:
    """Have the producer call make_clip for scene 2 with `note`: once its clip is made, for a
    fix the shop owner said yes to (#135)."""
    if note and ProducedItem.objects.filter(kind="clip", scene__number=2).exists():
        said_yes_to_a_fix(2, note, "clip")
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": note}))


@pytest.fixture
def clipped(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    """A chat whose scene 2, showing the mug, has its first clip."""
    made_ready(fake_model, steps, say, (2,))
    clip_of_scene_2(fake_model, steps, say)
    assert clips() == [(2, 1)]


# --- The clip made again from a note ----------------------------------------------------------


def test_the_rewrite_is_handed_the_old_prompts_the_scene_the_note_and_the_conversation(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    (handoff,) = handoffs("rewrite_clip_prompt")
    assert {key: handoff[key] for key in handoff if key != "conversation"} == {
        "scene": 2,
        "line": LINE_2,
        "shows": SHOWS,
        "broll_kind": "showcase",
        "person_shown": "no face",
        "usage": "",
        "result": "",
        "picture_prompt": BROLL_CHOICE["prompt"],
        "motion_prompt": MOTION,
        "note": HOVERS,
    }
    said = [message["text"] for message in handoff["conversation"]]
    assert "Make the person and check the script" in said
    assert (
        ModelCall.objects.get(purpose="rewrite_clip_prompt").model
        == (MODEL_FOR_PURPOSE["choose_broll_picture"])
    )


def test_the_rewrite_is_told_the_broll_rules_and_that_only_the_movement_changes(
    fake_model: FakeModel,
    clipped: None,
    steps: HeldSteps,
    say: Callable[..., None],
    instructed: dict[str, list[str]],  # noqa: F811
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    assert instructed["rewrite_clip_prompt"] == [
        f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES['showcase']}\n{CLIP_FROM_NOTE_RULES}"
    ]


def test_the_new_prompt_is_sent_to_the_video_model_with_the_rules_code_adds(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    # Wrapped by code exactly as the first clip's prompt was: its length, the still camera and
    # SAME_SIDE first, so the rewrite can't drop them.
    assert clips_asked("prompt") == [
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {MOTION}",
        f"A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} {NEW_MOTION}",
    ]


def test_only_the_clip_is_made_again_and_paid_for(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    picture_step = SceneStep.objects.get(kind="starting_picture", scene__number=2)
    first = ProducedItem.objects.get(kind="clip")
    paid = len(paid_for())

    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    assert paid_for()[paid:] == ["rewrite_clip_prompt", "make_broll_clip", "collect_broll_clip"]
    assert clips() == [(2, 1), (2, 2)]
    second = ProducedItem.objects.get(kind="clip", version=2)
    assert (second.picture, second.made_from) == (first.picture, first.made_from)
    step = second.step
    assert step is not None
    assert (step.note, step.motion_prompt, step.picture_step) == (HOVERS, NEW_MOTION, picture_step)
    # The first clip and the picture step are as they were.
    assert ProducedItem.objects.get(pk=first.pk).file == first.file
    picture_step.refresh_from_db()
    assert picture_step.motion_prompt == MOTION
    assert ProducedItem.objects.filter(kind="starting_picture").count() == 1
    assert ProducedItem.objects.filter(kind="line_audio").count() == 1
    assert Scene.objects.get(number=2).status == "finished"


def test_the_producer_is_told_the_clip_was_made_from_the_note(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    assert SceneStep.objects.filter(kind="clip").last().result == (  # type: ignore[union-attr]
        "Background step finished: scene 2's clip is ready (version 2, 5 seconds), made from "
        "starting picture version 1 and audio version 1, with its movement rewritten from the "
        f'note "{HOVERS}". Scene 2 is finished. Scene 2\'s fix has cost '
        f"{_dollars(Fix.objects.get().cost_usd())} so far. Tell the shop owner. The ad will be "
        "assembled again, and shown to the shop owner, once scenes 1 and 3 are finished."
    )


def test_a_note_with_messy_spacing_is_kept_tidy(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, f"  {HOVERS.replace(' ', '   ')}\n", REWRITE)

    assert SceneStep.objects.filter(kind="clip").last().note == HOVERS  # type: ignore[union-attr]
    assert handoffs("rewrite_clip_prompt")[0]["note"] == HOVERS


# --- Already made, busy and refused -----------------------------------------------------------


def test_the_same_note_again_is_already_made_and_charges_nothing(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)
    paid = paid_for()

    # Spaced differently, it is still the same note.
    noted(fake_model, say, f" {HOVERS} ")

    assert paid_for() == paid
    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio with this note "
        "(version 2), so nothing was made or paid for again."
    )


def test_a_different_note_makes_a_new_clip(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)

    clip_with_note(
        fake_model,
        steps,
        say,
        "The tea should pour from the left.",
        {**REWRITE, "motion_prompt": "Tea pours from the left into the mug."},
    )

    assert clips() == [(2, 1), (2, 2), (2, 3)]
    assert [handoff["note"] for handoff in handoffs("rewrite_clip_prompt")] == [
        HOVERS,
        "The tea should pour from the left.",
    ]
    # Each rewrite starts from the picture's own prompt, not the last note's.
    assert [handoff["motion_prompt"] for handoff in handoffs("rewrite_clip_prompt")] == [
        MOTION,
        MOTION,
    ]


def test_going_back_to_an_earlier_note_brings_its_clip_back_without_paying_again(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)
    clip_with_note(
        fake_model,
        steps,
        say,
        "The tea should pour from the left.",
        {**REWRITE, "motion_prompt": "Tea pours from the left into the mug."},
    )
    paid = paid_for()

    noted(fake_model, say, HOVERS)

    assert paid_for() == paid
    assert steps.held == []
    assert clips() == [(2, 1), (2, 2), (2, 3), (2, 4)]
    second, back = (ProducedItem.objects.get(kind="clip", version=v) for v in (2, 4))
    assert (back.file, back.step, back.seconds, back.made_from, back.picture) == (
        second.file,
        second.step,
        second.seconds,
        second.made_from,
        second.picture,
    )
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio with this note "
        "(version 2), so nothing was made or paid for again. It is the scene's clip again, as "
        "version 4. Making it cost "
    )
    # The ad is assembled from the note's clip, not the last one made.
    assert _clip_and_transcript(Scene.objects.get(number=2))[0] == back


def test_going_back_to_an_earlier_note_while_a_clip_is_being_made_is_refused_as_busy(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, REWRITE)
    noted(fake_model, say, "The tea should pour faster.")

    noted(fake_model, say, HOVERS)

    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's clip is already being made. You'll be told when it's ready. "
        "Nothing was done."
    )
    assert clips() == [(2, 1), (2, 2)]


def test_no_note_after_a_clip_was_made_is_already_made_as_before(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    paid = paid_for()

    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))

    assert paid_for() == paid
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio (version 1), "
        "so nothing was made or paid for again."
    )


def test_a_note_while_the_clip_is_being_made_is_refused_as_busy(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))

    noted(fake_model, say, HOVERS)

    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's clip is already being made. You'll be told when it's ready. "
        "Nothing was done."
    )
    assert SceneStep.objects.filter(kind="clip").count() == 1


def test_a_talking_scene_with_a_note_is_refused_and_nothing_is_paid(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (1,))
    paid = paid_for()

    calling(fake_model, say, ("make_clip", {"scene": 1, "note": "Wave at the camera."}))

    assert paid_for() == paid
    assert not SceneStep.objects.filter(kind="clip").exists()
    assert results_of("make_clip")[-1] == (
        "Refused: scene 1 shows the person talking, and a talking clip's movement can't be "
        "changed from a note: the video model ignores what it's asked about movement in a "
        "talking clip. Its starting picture or its line can change instead. Nothing was done."
    )


def test_a_talking_scene_with_no_note_gets_its_clip_as_before(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (1,))

    calling(fake_model, say, ("make_clip", {"scene": 1, "note": None}))
    run(fake_model, steps)

    assert clips() == [(1, 1)]
    assert "rewrite_clip_prompt" not in paid_for()


def test_a_scene_planned_before_its_broll_labels_with_a_note_is_refused(
    fake_model: FakeModel,
    unlabelled: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    paid = paid_for()

    noted(fake_model, say, HOVERS)

    assert paid_for() == paid
    assert not SceneStep.objects.filter(kind="clip").exists()
    assert results_of("make_clip")[-1] == (
        "Refused: scene 2 was planned before scenes had their B-roll labels, so its movement "
        "can't be rewritten from a note. Make its starting picture again with the note "
        "instead. Nothing was done."
    )


def test_the_tool_takes_a_note() -> None:
    assert list(MakeClip.model_json_schema()["properties"]) == ["scene", "note"]


# --- When the rewrite fails, or the worker stops ----------------------------------------------


def test_an_unusable_rewrite_fails_the_step_before_any_video_is_paid_for(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_with_note(fake_model, steps, say, HOVERS, {"motion_prompt": " ", "nonsense": 1})

    step = SceneStep.objects.filter(kind="clip").last()
    assert step is not None
    assert step.status == "failed"
    assert step.reason.startswith("a model's answer couldn't be used")
    assert step.result.startswith("Background step failed: scene 2's clip couldn't be made: ")
    assert paid_for().count("make_broll_clip") == 1
    assert clips() == [(2, 1)]


def test_a_rewrite_answered_before_the_worker_stopped_isnt_paid_for_again(
    fake_model: FakeModel,
    clipped: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noted(fake_model, say, HOVERS)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    (step_id,) = steps.held

    # The worker stops once the clip is asked for, as it waits for it.
    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    with monkeypatch.context() as stopping:
        stopping.setattr(work, "collect_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert ModelCall.objects.filter(purpose="rewrite_clip_prompt").count() == 1
    assert paid_for().count("make_broll_clip") == 2
    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert clips_asked("prompt")[-1].endswith(f"{SAME_SIDE} {NEW_MOTION}")


# --- Pure logic -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("note", "kept"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Pour the tea.", "Pour the tea."),
        ("  Pour   the\ttea.\n", "Pour the tea."),
    ],
)
def test_a_note_is_normalised_as_the_picture_tool_does(note: str | None, kept: str) -> None:
    assert normalised_note(note) == kept


@pytest.mark.parametrize("kind", list(BROLL_KIND_RULES))
def test_the_rewrite_is_told_the_shared_rules_its_kinds_rules_and_the_clip_only_block(
    kind: str,
) -> None:
    told = clip_from_note_instructions(kind)

    assert told.startswith(BROLL_SHARED_RULES)
    assert BROLL_KIND_RULES[kind] in told
    assert told.endswith(CLIP_FROM_NOTE_RULES)
    for other in set(BROLL_KIND_RULES) - {kind}:
        assert BROLL_KIND_RULES[other] not in told


def test_the_clip_only_block_says_the_picture_is_fixed_and_only_the_movement_changes() -> None:
    assert CLIP_FROM_NOTE_RULES == (
        "The scene's starting picture is already made, and it stays as it is: you are shown "
        'it, and "picture_prompt" is the prompt it was drawn from. Pick no photo and write no '
        'picture prompt. Write only a new video prompt: "motion_prompt" is the one the '
        'scene\'s first clip from this picture was made from, before any note, and "note" '
        "says what the shop owner wants changed about how it moves. Change the movement as the "
        "note asks, starting from what the picture shows, and keep what was right. Follow the "
        "note unless it asks for something that can't start from this picture, or something "
        '"shows" doesn\'t describe, and then say so in the reason. Give a one-sentence reason '
        "for the video prompt, written for the shop owner."
    )
