"""The scene agent through the owner's chat, outside models scripted at the gateway."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.models import SceneStep

from .conftest import HeldSteps, handoffs, recorded
from .test_producer_broll import calling, checked, run  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scene_agent_ready(checked: None) -> None:  # noqa: F811
    """H3 scenes need only a checked plan and the presenter's portrait."""


@pytest.mark.parametrize("scene", [1, 2], ids=["talking", "broll"])
def test_every_scene_gets_the_starting_picture_rules_and_form_notes(
    fake_model: FakeModel,
    scene_agent_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    scene: int,
) -> None:
    from agents.scene_agent.instructions import CLOSE_VIEW_RULE, NO_REVEAL_RULE
    from agents.scene_forms import WHOLE_PRODUCT_RULE

    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "No view fits."})]))
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Show it.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)

    [request] = [each for each in fake_model.turns_taken if each.purpose == "scene_agent"]
    for rule in (WHOLE_PRODUCT_RULE, NO_REVEAL_RULE, CLOSE_VIEW_RULE):
        assert rule in request.instructions
    tools = {each.name: each for each in request.tools}
    draw = tools["draw_starting_picture"].arguments.model_json_schema()
    assert WHOLE_PRODUCT_RULE in draw["properties"]["size"]["description"]
    # Edits inherit the same instructions without imposing a whole-product crop on ends.
    end = tools["make_end_picture"].arguments.model_json_schema()
    assert WHOLE_PRODUCT_RULE not in str(end)


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
    assert any("couldn't be filmed as planned" in text for _, text in recorded(api, session_id))
    assert not any("missing view" in text.lower() for _, text in recorded(api, session_id))
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
