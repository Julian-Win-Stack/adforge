"""Owner notes requiring an overlay or exceeding the video model's abilities."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Scene

from .conftest import HeldSteps, chat, handoffs, paid_for
from .test_producer_broll import checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_overlay_note_edits_a_busy_scene_without_drawing_or_filming(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:  # noqa: F811
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {"scene": 3, "instruction": "Make this scene.", "quotes": ["Go on"]},
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Go on")
    before = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("edit_scene", {"scene": 3, "overlay": "Free shipping"})]),
        turn(says="Added Free shipping on screen."),
    )
    say("Scene 3: put 'Free shipping' on screen")
    assert Scene.objects.get(number=3).overlay == "Free shipping"
    assert paid_for() == before


def test_hard_limit_ends_before_paid_work_and_producer_explains_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    reason = "I can't make tiny print readable because the video model cannot preserve its letters."
    reply = reason + " I can put those words in the text overlay instead."
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "cannot_film",
                    {"reason": reason, "alternative": "Put the words in the text overlay."},
                )
            ]
        ),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": "Make the tiny label readable.",
                                "quotes": ["Make the tiny label readable"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Make the tiny label readable")
    before = paid_for()
    fake_model.respond("produce", turn(says=reply))
    steps.run_held()
    assert [purpose for purpose in paid_for()[len(before) :] if purpose != "scene_agent"] == []
    assert reason in str(handoffs("produce")[-1])
    shown = str(chat(api, session_id))
    assert shown.count(reason) == 1
    assert "text overlay instead" in shown


def test_overlay_note_handed_back_by_scene_agent_is_completed_by_producer(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "needs_producer",
                    {
                        "reason": "On-screen words use the overlay.",
                        "alternative": "Set Free shipping as this scene's overlay with edit_scene.",
                    },
                )
            ]
        ),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 3,
                                "instruction": "Put Free shipping on screen.",
                                "quotes": ["Scene 3: put 'Free shipping' on screen"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Scene 3: put 'Free shipping' on screen")
    before = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("edit_scene", {"scene": 3, "overlay": "Free shipping"})]),
        turn(says="Added Free shipping on screen."),
    )
    steps.run_held()
    assert Scene.objects.get(number=3).overlay == "Free shipping"
    assert [purpose for purpose in paid_for()[len(before) :] if purpose != "scene_agent"] == []
    assert "Needs producer:" in str(handoffs("produce")[-1])
