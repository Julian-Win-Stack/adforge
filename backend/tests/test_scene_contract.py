"""The sole B-roll note interface remains the scene's persistent chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel

from .test_producer_broll import checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_producer_offers_one_broll_note_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from gateway.fake import turn

    fake_model.respond("produce", turn(says="Ready for your note."))
    say("Which scene can I change?")
    producer = next(
        request for request in reversed(fake_model.turns_taken) if request.purpose == "produce"
    )
    names = [tool.name for tool in producer.tools]
    assert "pass_note" in names
    assert "fix_scene" not in names
    assert "fixing agent" not in producer.instructions.lower()
