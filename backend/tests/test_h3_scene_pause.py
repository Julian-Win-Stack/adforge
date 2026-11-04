"""Talking-scene pause and resume through the owner's chat and pause button."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps, handoffs, paid_for, results_of
from .test_pause import pause
from .test_producer_broll import calling, run
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401
from .test_scene_talking_agent import talking_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def parked_talking_scene(
    talking_ready: None,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    def pause_before_drawing() -> Turn:
        assert pause(api, session_id) == {"paused": True}
        return turn(calls=[("draw_starting_picture", PICTURE_FORM)])

    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Show the mug.", "quotes": ["Go on"]},
        ),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("look_at_library", {"item": 1, "video": None, "moment": None})]),
        pause_before_drawing,
    )
    fake_model.respond("produce", turn(says="Paused."), turn(says="The scene is parked."))
    steps.run_held()


def test_a_talking_scene_parks_before_the_next_tool_when_the_owner_presses_pause(
    parked_talking_scene: None,
    api: APIClient,
    session_id: str,
) -> None:
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is True
    assert results_of("draw_starting_picture")[-1].startswith("Refused: paused")
    assert "draw_scene_picture" not in paid_for()
    assert "parked for the owner's pause" in str(handoffs("produce"))


def test_the_owners_resume_continues_the_talking_scenes_same_chat(
    parked_talking_scene: None,
    picture_checked: None,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 1,
                                "instruction": "Continue with the handle visible.",
                                "quotes": ["Continue showing the handle"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="I passed it on."),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "A closer handle photo is needed."})]),
    )
    say("Continue showing the handle")
    run(fake_model, steps)

    resumed = str(handoffs("scene_agent")[-2])
    assert "Library item 1" in resumed
    assert "Show the mug." in resumed
    assert "Continue with the handle visible." in resumed
    assert "Continue showing the handle" in resumed
    assert "Starting picture" in results_of("draw_starting_picture")[-1]
    assert paid_for().count("draw_scene_picture") == 1
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False
