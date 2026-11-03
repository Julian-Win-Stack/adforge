"""The scene agent through the owner's chat, outside models scripted at the gateway."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import SceneStep

from .conftest import HeldSteps, handoffs, recorded
from .test_producer_broll import calling, checked, made_ready, run  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scene_agent_ready(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))


def test_scene_agent_sees_the_plan_and_gives_up_through_the_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "A missing view needs an owner photo."})])
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the product being used.", "quotes": ["Go on"]},
        ),
    )
    assert len(steps.held) == 1
    run(fake_model, steps)
    assert "missing view" in str(handoffs("produce")).lower()
    assert handoffs("scene_agent")
    assert any("missing view" in text.lower() for _, text in recorded(api, session_id))
    assert SceneStep.objects.get(kind="scene_agent").status == "finished"


def test_worker_restart_keeps_the_scene_chat_and_does_not_repeat_paid_work(
    fake_model: FakeModel, scene_agent_ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    from agents import tasks

    from .conftest import WorkerStopped

    fake_model.respond(
        "scene_agent",
        turn(calls=[("look_at_library", {"item": 1, "video": None, "moment": None})]),
        WorkerStopped(),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    with pytest.raises(WorkerStopped):
        steps.run_held()
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    fake_model.respond("produce", turn(says="It needs another view."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()
    requests = handoffs("scene_agent")
    assert "Library item 1" in str(requests[-1])
    assert "Need another view" in str(handoffs("produce"))


def test_pause_during_a_scene_turn_parks_it_before_the_next_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    from agents import pause
    from chat.models import Session

    def paused_turn() -> Turn:
        pause.pause(Session.objects.get(pk=session_id), wake=False)
        return turn(calls=[("give_up", {"reason": "This must not be treated as a give-up."})])

    fake_model.respond("scene_agent", paused_turn)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert "parked for the owner's pause" in str(handoffs("produce"))
    assert "This must not be treated" not in str(handoffs("produce"))
