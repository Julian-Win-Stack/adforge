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

from agents.models import SystemNote
from gateway.fake import FakeModel
from jobs.models import ProducedItem, SceneStep

from .conftest import (
    HeldSteps,
    paid_for,
    results_of,
)
from .test_producer_broll import (
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
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


# --- Already made, busy and refused -----------------------------------------------------------


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


# --- Pure logic -------------------------------------------------------------------------------
