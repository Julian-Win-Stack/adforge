"""Scene agents decide with the available library and cannot ask the owner."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_scene_agent_has_no_owner_question_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    offered: list[str] = []

    def decides(request: Any) -> Turn:
        offered.extend(tool.name for tool in request.tools)
        return turn(calls=[("give_up", {"reason": "The available view cannot show the base."})])

    fake_model.answer_unscripted("scene_agent", decides)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its base.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert offered
    assert "ask_owner" not in offered
