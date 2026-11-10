"""The picture check names every wrong part it sees, through the scene chat (ticket #523)."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ToolUse
from jobs.models import ProducedItem

from .conftest import HeldSteps
from .test_clip_judge import product
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import END_PICTURE_PASSES, PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)

TWO_FAULTS = {
    "passes": False,
    "says": "The logo on the front is faded. The back panel has three extra dots in its "
    "top corner.",
}


def make_both_pictures(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    starting_answer: dict[str, Any],
    end_answer: dict[str, Any],
) -> None:
    """A starting picture, a clip the judge fails, then an end picture made from it."""
    fake_model.respond("check_scene_picture", starting_answer, end_answer)
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)


def told_to_agent(fake_model: FakeModel, tool: str) -> str:
    """What the scene agent's last turn was handed back from its newest `tool` call."""
    request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
    return [
        item.result
        for item in request.handoff.conversation
        if isinstance(item, ToolUse) and item.tool == tool
    ][-1]


def test_both_picture_checks_ask_for_every_wrong_part(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    make_both_pictures(fake_model, steps, say, PICTURE_PASSES, END_PICTURE_PASSES)

    asked = [r for r in fake_model.asked if r.purpose == "check_scene_picture"]
    assert len(asked) == 2
    for request in asked:
        assert "every wrong part" in request.instructions
        assert "in one line" not in request.instructions
        answer = request.output.model_json_schema()["$defs"]["Answer"]
        says = answer["properties"]["says"]["description"]
        assert "every wrong part" in says
        assert "where" in says
        # A passing answer stays short.
        assert "passes" in says and "short" in says


def test_two_faults_reach_the_scene_agent_unchanged(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    make_both_pictures(
        fake_model,
        steps,
        say,
        {**PICTURE_PASSES, "product_same": TWO_FAULTS},
        {**END_PICTURE_PASSES, "product_same": TWO_FAULTS},
    )

    for tool in ("draw_starting_picture", "make_end_picture"):
        assert json.dumps(TWO_FAULTS) in told_to_agent(fake_model, tool)
    end = ProducedItem.objects.get(kind="end_picture")
    assert end.ruling == {
        "status": "failed",
        "answers": {**END_PICTURE_PASSES, "product_same": TWO_FAULTS},
    }


@pytest.mark.parametrize(
    "failing", [None, "product_same", "physics", "product_ready", "only_seen_parts"]
)
def test_the_verdict_is_unchanged(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    failing: str | None,
) -> None:
    answers = dict(PICTURE_PASSES)
    if failing:
        answers[failing] = TWO_FAULTS
    fake_model.respond("check_scene_picture", answers)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.ruling is not None
    assert made.ruling["status"] == ("failed" if failing else "passed")
