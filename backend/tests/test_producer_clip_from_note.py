"""Current producer clip doors refuse obsolete movement-note arguments; scene notes use
checked owner quotes and the scene's persistent agent chat."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.models import SystemNote
from gateway.fake import FakeModel
from jobs.models import ProducedItem

from .conftest import (
    HeldSteps,
)
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    made_ready,
    unlabelled,  # noqa: F401 (a fixture)
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("legacy_path")]

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


# --- When the rewrite fails, or the worker stops ----------------------------------------------


# --- Pure logic -------------------------------------------------------------------------------
