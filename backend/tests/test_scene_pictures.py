"""Starting pictures through the chat, with only external models scripted."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem

from .conftest import HeldSteps, handoffs, paid_for
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PICTURE_FORM: dict[str, Any] = dict(
    everything_shown=[],
    camera="Phone at chest height",
    person_and_room="Hands at a desk",
    pose="One hand holds the mug",
    inside="not needed here",
    size="A quarter of the frame",
    moment="Pouring halfway through",
    light="Window on the left",
    bodies="Two hands",
    anything_else="not needed here",
    picks=[{"item": 1, "take": "Copy the shape and glaze."}],
)
PICTURE_PASSES = {
    "product_same": {"passes": True, "says": "The product matches."},
    "physics": {"passes": True, "says": "Everything is possible."},
}


@pytest.fixture
def picture_checked(fake_model: FakeModel) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)


def test_drawn_picture_and_check_share_the_selected_inputs(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.sent is not None
    draw = handoffs("draw_scene_picture")[0]
    check = handoffs("check_scene_picture")[0]
    assert draw["pictures"] == [p["file"] for p in made.sent["photos"]]
    assert check["photos"] == [p["line"] for p in made.sent["photos"]]
    assert "Copy the shape and glaze." in draw["prompt"]
    assert made.ruling == {"status": "passed", "answers": PICTURE_PASSES}
    assert paid_for().count("draw_scene_picture") == 1
    assert ("draw_scene_picture", "1152x2048") in fake_model.edits_asked
    assert set(check) == {"product_name", "product_colour", "page_text", "owner_chat", "photos"}


def test_failed_picture_is_redrawn_with_answers_in_the_same_scene_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    failure = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The rim is square."}}
    fake_model.respond("check_scene_picture", failure, PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "pose": "Round rim visible"})]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    requests = handoffs("scene_agent")
    assert "The rim is square." in str(requests[1])
    assert "The rim is square." in str(requests[2])
    assert paid_for().count("draw_scene_picture") == 2
    assert list(
        ProducedItem.objects.filter(kind="starting_picture", scene__number=2)
        .order_by("version")
        .values_list("ruling__status", flat=True)
    ) == ["failed", "passed"]


def test_unavailable_check_keeps_picture_unchecked_and_tells_owner(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.types import UnusableReply

    from .conftest import recorded

    fake_model.respond(
        "check_scene_picture", UnusableReply("No answer", input_tokens=0, output_tokens=0)
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.ruling == {"status": "unchecked", "answers": None}
    assert any("look at it yourself" in text for _, text in recorded(api, session_id))
