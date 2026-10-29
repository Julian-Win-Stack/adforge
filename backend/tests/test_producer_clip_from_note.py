"""A B-roll scene's clip made again from a note (route B step 1, #132): when a clip's movement
is wrong, only the clip is made again, from the same sent list and the same audio (a B-roll
scene has no starting picture, spec #210 point 19), with its video prompt rewritten by a
backend model from the note. Nothing else is made or paid for. Once the scene has a clip, the
new clip is a fix plan's, said yes to and run by code, with the plan's change as the clip's
note (fix redesign, decision 7). The scene's first clip is made through the chat; the
producer's model and the scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from agents.models import SystemNote
from agents.money import dollars
from agents.producer import _clip_and_transcript
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import work
from jobs.models import FixPlanScene, ProducedItem, Scene, SceneStep
from jobs.scenes import (
    BROLL_KIND_RULES,
    BROLL_SHARED_RULES,
    CLIP_FROM_REFERENCES_RULES,
)

from .conftest import (
    SENT_LIST_JUDGED,
    HeldSteps,
    WorkerStopped,
    handoffs,
    paid_for,
    results_of,
)
from .fixing import fixed
from .sent_lists import holding_the_fixture
from .test_producer_broll import (
    LINE_2,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    clips_asked,
    instructed,  # noqa: F401 (a fixture)
    made_ready,
    sent_prompt,
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
        "motion_prompt": sent_prompt(),
        "note": HOVERS,
    }
    said = [message["text"] for message in handoff["conversation"]]
    assert "Make the person and check the script" in said
    assert (
        ModelCall.objects.get(purpose="rewrite_clip_prompt").model
        == (MODEL_FOR_PURPOSE["choose_broll_picture"])
    )


def test_a_rewrite_of_a_clip_made_from_references_is_shown_the_pictures_it_was_sent(
    fake_model: FakeModel,
    clipped: None,
    steps: HeldSteps,
    instructed: dict[str, list[str]],  # noqa: F811
) -> None:
    sent = holding_the_fixture(ProducedItem.objects.get(kind="clip"))

    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    rewrite = ModelCall.objects.get(purpose="rewrite_clip_prompt")
    # The sent pictures, in their order with their labels, in place of the starting picture.
    assert [image["label"] for image in rewrite.images] == [
        f"Image {each.image}: {each.label}" for each in sent.pictures
    ]
    (handoff,) = handoffs("rewrite_clip_prompt")
    assert "picture_prompt" not in handoff
    assert handoff["motion_prompt"] == sent.prompt
    assert handoff["note"] == HOVERS
    assert instructed["rewrite_clip_prompt"] == [
        f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES['showcase']}\n{CLIP_FROM_REFERENCES_RULES}"
    ]


def test_the_new_prompt_is_sent_to_the_video_model_with_the_rules_code_adds(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    # The rewrite is the whole prompt, sent with the first clip's pictures as they were.
    assert clips_asked("prompt") == [sent_prompt(), NEW_MOTION]
    first, again = clips_asked("example_pictures")
    assert again == first


def test_only_the_clip_is_made_again_and_paid_for(
    fake_model: FakeModel, clipped: None, steps: HeldSteps
) -> None:
    first = ProducedItem.objects.get(kind="clip")
    paid = len(paid_for())

    clip_with_note(fake_model, steps, HOVERS, REWRITE)

    assert paid_for()[paid:] == [
        "rewrite_clip_prompt",
        "make_broll_clip",
        "collect_broll_clip",
        *SENT_LIST_JUDGED,
    ]
    assert clips() == [(2, 1), (2, 2)]
    second = ProducedItem.objects.get(kind="clip", version=2)
    assert (second.picture, second.made_from) == (None, first.made_from)
    step = second.step
    assert step is not None
    assert (step.note, step.motion_prompt) == (HOVERS, NEW_MOTION)
    # The first clip is as it was, and no picture was drawn.
    assert ProducedItem.objects.get(pk=first.pk).file == first.file
    assert not ProducedItem.objects.filter(kind="starting_picture").exists()
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
    # movement, not the first clip's (#180).
    assert [handoff["motion_prompt"] for handoff in handoffs("rewrite_clip_prompt")] == [
        sent_prompt(),
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
    # It has no picture to bring back: a B-roll clip has none (spec #210 point 19).
    assert back.picture is None
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
        "Scene 2's clip was already made from this plan and audio (version 1), "
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
        "can't be rewritten from a note. Its line can change instead. Nothing was done."
    )


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
    assert clips_asked("prompt")[-1] == NEW_MOTION


# --- Pure logic -------------------------------------------------------------------------------
