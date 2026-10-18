"""A B-roll scene's clip made again from a note (route B step 1, #132): when a clip's movement
is wrong, only the clip is made again, from the same starting picture and the same audio,
with its video prompt rewritten by a backend model from the note. Nothing else is made or
paid for. Once the scene has a clip, the new clip is a fix plan's, said yes to and run by
code, with the plan's change as the clip's note (fix redesign, decision 7). The scene's first
clip is made through the chat; the producer's model and the scene models are faked at the
gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from agents.models import SystemNote
from agents.money import dollars
from agents.producer import MakeClip, _clip_and_transcript, normalised_note
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import work
from jobs.models import FixPlanScene, ProducedItem, Scene, SceneStep
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_NOTE_RULES,
    SAME_SIDE,
    broll_prompt_instructions,
    clip_from_judge_instructions,
    clip_from_note_instructions,
)

from .conftest import (
    JUDGED_CLIP,
    HeldSteps,
    WorkerStopped,
    handoffs,
    paid_for,
    results_of,
)
from .fixing import fixed
from .test_producer_broll import (
    BROLL_CHOICE,
    IMAGE_1,
    LINE_2,
    MOTION,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
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
    fake_model: FakeModel, steps: HeldSteps, note: str, *rewrites: dict[str, Any]
) -> None:
    """Make scene 2's clip again with `note`, the rewrite answering `rewrites`: through a
    plan for a new clip with `note` as its change, said yes to and run by code."""
    noted(note)
    fake_model.respond("rewrite_clip_prompt", *rewrites)
    steps.run_held()


def noted(note: str) -> None:
    """The shop owner said yes to a plan giving scene 2 a new clip for `note`: its clip step
    is queued, unless the scene is busy."""
    fixed(2, "new_clip", change=note)


def notes(kind: str) -> list[str]:
    return list(SystemNote.objects.filter(kind=kind).order_by("id").values_list("text", flat=True))


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
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

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
    instructed: dict[str, list[str]],  # noqa: F811
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    assert instructed["rewrite_clip_prompt"] == [
        f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES['showcase']}\n{CLIP_FROM_NOTE_RULES}"
    ]


def test_the_new_prompt_is_sent_to_the_video_model_with_the_rules_code_adds(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    # Wrapped by code exactly as the first clip's prompt was: its length, the still camera and
    # SAME_SIDE first, so the rewrite can't drop them.
    assert clips_asked("prompt") == [
        f"{IMAGE_1}A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} "
        f"{MOTION}",
        f"{IMAGE_1}A 5-second video at real-time speed. The camera stays still. {SAME_SIDE} "
        f"{NEW_MOTION}",
    ]
    # In reference mode, its starting picture as Image 1, as every B-roll clip.
    picture = ProducedItem.objects.get(kind="starting_picture").file
    assert clips_asked("example_pictures")[-1] == [picture]


def test_only_the_clip_is_made_again_and_paid_for(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    picture_step = SceneStep.objects.get(kind="starting_picture", scene__number=2)
    first = ProducedItem.objects.get(kind="clip")
    paid = len(paid_for())

    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    assert paid_for()[paid:] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]
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


def test_the_producer_is_told_by_a_note_that_the_clip_was_made(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    row = FixPlanScene.objects.get()
    assert notes("scene_done") == [
        f"Scene 2's new clip is done. It cost {dollars(row.cost_usd())}."
    ]
    # Scenes 1 and 3 have no clip yet, so the new ad can't be assembled, and it is told why.
    assert notes("error") == [
        "The new ad couldn't be assembled: scenes 1 and 3 aren't finished: a scene is "
        "finished once its clip is made. Finish them, then assemble the ad."
    ]


def test_a_note_with_messy_spacing_is_kept_tidy(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, f"  {HOVERS.replace(' ', '   ')}\n", REWRITE)

    assert SceneStep.objects.filter(kind="clip").last().note == HOVERS  # type: ignore[union-attr]
    assert handoffs("rewrite_clip_prompt")[0]["note"] == HOVERS


# --- Already made, busy and refused -----------------------------------------------------------


def test_a_different_note_makes_a_new_clip(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    clip_with_note(
        fake_model,
        steps,
        "The tea should pour from the left.",
        {**REWRITE, "motion_prompt": "Tea pours from the left into the mug."},
    )

    assert clips() == [(2, 1), (2, 2), (2, 3)]
    assert [handoff["note"] for handoff in handoffs("rewrite_clip_prompt")] == [
        HOVERS,
        "The tea should pour from the left.",
    ]
    # Each rewrite improves the clip in the ad: the second starts from the first note's
    # movement, not the picture's own prompt (#180).
    assert [handoff["motion_prompt"] for handoff in handoffs("rewrite_clip_prompt")] == [
        MOTION,
        REWRITE["motion_prompt"],
    ]


def test_going_back_to_an_earlier_note_brings_its_clip_back_without_paying_again(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)
    clip_with_note(
        fake_model,
        steps,
        "The tea should pour from the left.",
        {**REWRITE, "motion_prompt": "Tea pours from the left into the mug."},
    )
    paid = paid_for()

    fixed(2, "go_back", change="The pour was better.", go_back_to=2)

    assert paid_for() == paid
    assert steps.held == []
    assert clips() == [(2, 1), (2, 2), (2, 3), (2, 4)]
    second, back = (ProducedItem.objects.get(kind="clip", version=v) for v in (2, 4))
    assert (back.file, back.step, back.seconds, back.made_from, back.ruling) == (
        second.file,
        second.step,
        second.seconds,
        second.made_from,
        second.ruling,
    )
    # Its picture is brought back with it, as the version's newest picture.
    assert back.picture is not None and second.picture is not None
    assert back.picture.file == second.picture.file
    assert work.versions(Scene.objects.get(number=2))[-1].label == "v4 back to v2"
    # The ad is assembled from the note's clip, not the last one made.
    assert _clip_and_transcript(Scene.objects.get(number=2))[0] == back


def test_going_back_to_an_earlier_note_while_a_clip_is_being_made_waits_for_it(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)
    noted("The tea should pour faster.")

    fixed(2, "go_back", change="The pour was better.", go_back_to=2)

    assert clips() == [(2, 1), (2, 2)]
    fake_model.respond("rewrite_clip_prompt", {**REWRITE, "motion_prompt": "Tea pours fast."})
    steps.run_held()
    # Once the faster clip was made, code went back on top of it.
    assert clips() == [(2, 1), (2, 2), (2, 3), (2, 4)]
    second, back = (ProducedItem.objects.get(kind="clip", version=v) for v in (2, 4))
    assert back.file == second.file
    assert _clip_and_transcript(Scene.objects.get(number=2))[0] == back


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

    calling(fake_model, say, ("make_clip", {"scene": 2, "note": HOVERS}))

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


def test_a_scene_planned_before_its_broll_labels_with_a_note_is_refused(
    fake_model: FakeModel,
    unlabelled: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    paid = paid_for()

    calling(fake_model, say, ("make_clip", {"scene": 2, "note": HOVERS}))

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
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, {"motion_prompt": " ", "nonsense": 1})

    step = SceneStep.objects.filter(kind="clip").last()
    assert step is not None
    assert step.status == "failed"
    assert step.reason.startswith("a model's answer couldn't be used")
    assert notes("error")[0].startswith(
        "Scene 2's new clip couldn't be made: a model's answer couldn't be used"
    )
    assert paid_for().count("make_broll_clip") == 1
    assert clips() == [(2, 1)]


def test_a_rewrite_answered_before_the_worker_stopped_isnt_paid_for_again(
    fake_model: FakeModel,
    clipped: None,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noted(HOVERS)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    (step_id,) = steps.held

    # The worker stops once the clip is asked for, as it waits for it.
    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    with monkeypatch.context() as stopping:
        stopping.setattr(work, "collect_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
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
        "note asks, starting from what the picture shows, and keep what was right. When the "
        'note names a movement to film, such as a step before the one "shows" names, like '
        "putting the product on the tool used with it, film every movement the note names, in "
        "the order it says: here the note wins over the rules above that say to film one "
        "movement only, never to film or draw putting the product on, and to follow a note "
        'only for what "shows" describes. Every other rule of this scene\'s kind still holds, '
        "such as nobody touching the product in a scene that shows the problem. Follow the "
        "note unless it asks for something that "
        "can't start from this picture, or something else \"shows\" doesn't describe, and then "
        "say so in the reason. Give a one-sentence reason for the video prompt, written for "
        "the shop owner."
    )


def test_a_note_naming_a_movement_beats_the_one_movement_rule_only_in_a_note_rewrite() -> None:
    # Julian's grade of the real-clip run (Oct 18): "show the gel going onto the brush first"
    # was dropped because the one-movement rule won. The note wins only in a rewrite from a
    # note: the proven rules a scene's prompts are first written with stay as they are.
    for kind in BROLL_KIND_RULES:
        assert "wins over the rules above" not in broll_prompt_instructions(kind)


@pytest.mark.parametrize("again", [False, True])
def test_a_judge_remake_keeps_the_movements_the_owners_note_names(again: bool) -> None:
    # The review of the build (Oct 18): a judge remake of a note clip was told the same
    # one-movement rule, so it could drop the gel the owner asked for again.
    told = clip_from_judge_instructions("does a job", again=again)

    assert (
        'When "owner_fix" names movements to film, film them in its order: there it wins over '
        'the rules above on one movement, putting the product on, and what "shows" '
        "describes."
    ) in told
