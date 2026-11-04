"""Producer quotes reach the real scene agent through the chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, handoffs, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_checked_owner_quotes_reach_the_scene_with_their_own_label(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="I will keep that note."))
    say("Keep its handle visible. Use the desk for scene two.")
    fake_model.respond("produce", turn(says="Understood."))
    say("thanks")
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Frame the handle clearly.",
                "quotes": ["Keep its handle visible", "Use the desk for scene two"],
            },
        ),
    )
    assert steps.held, results_of("start_scene_agent")
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    run(fake_model, steps)

    given = str(handoffs("scene_agent"))
    assert "Producer's instruction: Frame the handle clearly." in given
    assert "Owner's words (checked word for word)" in given
    assert "Keep its handle visible" in given
    assert "Use the desk for scene two" in given
    assert "thanks" not in given


def test_a_misquote_refuses_the_start_before_any_work(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Make it blue.",
                "quotes": ["The owner never said these words"],
            },
        ),
    )
    assert not steps.held
    assert "that text isn't in the owner's messages, quote it exactly" in str(
        results_of("start_scene_agent")
    )
