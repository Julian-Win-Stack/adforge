"""Give-ups inform the owner while the producer handles recovery."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, chat, handoffs
from .scene_script import make_scene_clip
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_give_up_without_a_clip_tells_the_producer_to_recover(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "No photo shows the working part."})])
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    shown = str(chat(api, session_id))
    assert "couldn't be filmed as planned" in shown
    assert "No photo shows the working part" not in shown
    # Scene 2 is the ad's only B-roll scene, so it can't be said to camera (ticket #403).
    assert "talks about it" not in shown.lower()
    assert "send a photo" not in shown.lower()
    assert "pass_note" in str(handoffs("produce")[-1])


def test_a_give_up_keeps_an_existing_clip_without_asking(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    make_scene_clip(fake_model, steps, say)
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "A needed side is unseen."})])
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show another side.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)
    shown = str(chat(api, session_id))
    assert "Its last clip stays in the ad" in shown
    assert "A needed side is unseen" not in shown
    assert "Wait for the owner's choice" not in str(handoffs("produce")[-1])
