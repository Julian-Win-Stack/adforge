"""Overlay ownership is shown beside the plan in each agent's real chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Said, ToolUse

from .conftest import FACTS_OK, NO_CHOICES, READABLE, HeldSteps
from .test_producer_broll import BROLL_PLAN, calling, run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("overlay", ["Hand-thrown", None])
def test_both_agents_are_told_code_draws_the_planned_overlay(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    overlay: str | None,
) -> None:
    plan = {
        **BROLL_PLAN,
        "plan": {
            **BROLL_PLAN["plan"],
            "scenes": [
                {**scene, "overlay": overlay if number == 2 else None}
                for number, scene in enumerate(BROLL_PLAN["plan"]["scenes"], 1)
            ],
        },
    }
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="The plan is ready."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", plan)
    say(product_page_url)
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Go on")
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Review the plan."})]))
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)

    producer = [request for request in fake_model.turns_taken if request.purpose == "produce"][-1]
    scene = next(request for request in fake_model.turns_taken if request.purpose == "scene_agent")
    producer_words = "\n".join(
        each.result if isinstance(each, ToolUse) else each.text
        for each in producer.handoff.conversation
    )
    first = scene.handoff.conversation[0]
    assert isinstance(first, Said)
    scene_words = first.text
    if overlay:
        assert (
            'Overlays (code draws these on the finished ad): scene 2 "Hand-thrown".'
            in producer_words
        )
        assert 'overlay: "Hand-thrown" (code draws this on the finished ad)' in scene_words
    else:
        assert "Overlays: none." in producer_words
        assert "overlay:" not in scene_words
    descriptions = [
        tool.arguments.model_json_schema()["properties"]["overlay"]["description"]
        for tool in producer.tools
        if tool.name in {"edit_scene", "add_scene"}
    ]
    assert len(descriptions) == 2
    assert all("Code draws these words on the finished ad" in text for text in descriptions)
