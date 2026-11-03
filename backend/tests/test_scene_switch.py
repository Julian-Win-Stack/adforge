"""Producer doors select scene agents for B-roll and keep talking-scene tools."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel

from .conftest import HeldSteps, results_of
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_broll_clip_and_picture_tools_name_the_scene_agent_instead(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        ("make_clip", {"scene": 2}),
        ("make_starting_picture", {"scene": 2, "note": None}),
    )

    assert not steps.held
    assert "start_scene_agent" in results_of("make_clip")[-1]
    assert "agent draws its starting picture" in results_of("make_starting_picture")[-1]
