"""Current producer clip doors refuse obsolete movement-note arguments; scene notes use
checked owner quotes and the scene's persistent agent chat."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.models import SystemNote
from gateway.fake import FakeModel, turn
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


def test_the_obsolete_note_argument_is_refused_without_new_paid_work(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    paid = paid_for()

    calling(fake_model, say, ("make_clip", {"scene": 2, "note": None}))

    assert paid_for() == paid
    assert "note: Extra inputs are not permitted" in results_of("make_clip")[-1]


def test_a_note_while_the_scene_is_being_made_is_passed_to_its_agent(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    from .conftest import handoffs
    from .scene_script import script_scene_agents
    from .test_producer_broll import run

    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {"scenes": [{"scene": 2, "instruction": NEW_MOTION, "quotes": [HOVERS]}]},
                )
            ]
        ),
        turn(says="I passed your note on."),
    )
    say(HOVERS)
    assert not results_of("pass_note")[-1].startswith("Refused:")
    assert len(steps.held) == 1
    script_scene_agents(fake_model)
    run(fake_model, steps)
    assert HOVERS in str(handoffs("scene_agent")[0])
    assert NEW_MOTION in str(handoffs("scene_agent")[0])


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
    assert "note: Extra inputs are not permitted" in results_of("make_clip")[-1]


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
    assert "note: Extra inputs are not permitted" in results_of("make_clip")[-1]


# --- When the rewrite fails, or the worker stops ----------------------------------------------


# --- Pure logic -------------------------------------------------------------------------------
