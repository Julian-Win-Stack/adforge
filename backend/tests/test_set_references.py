"""Owner exclusions remain a producer tool; scene agents choose every drawing photo."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, handoffs, paid_for, results_of
from .scene_script import make_scene_clip
from .test_producer_broll import calling, checked, made_ready, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import PICTURE_FORM

pytestmark = pytest.mark.django_db(transaction=True)


def set_references(fake_model: FakeModel, say: Callable[..., None], **asked: Any) -> str:
    calling(fake_model, say, ("set_references", {"scene": 2, **asked}))
    return results_of("set_references")[-1]


@pytest.mark.parametrize(
    "selection",
    [
        {"pictures": [{"number": 1, "for": "state"}]},
        {"pictures": []},
    ],
)
def test_photo_selections_are_refused_without_applying_exclusions(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    selection: dict[str, Any],
) -> None:
    answer = set_references(fake_model, say, exclude=[1], **selection)
    assert answer.startswith("Refused: the scene agent picks its photos")
    make_scene_clip(fake_model, steps, say)
    assert paid_for().count("make_broll_clip") == 1
    assert "passed" in str(handoffs("produce"))


def test_exclusions_are_a_set_and_null_keeps_the_owners_list(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    answer = set_references(fake_model, say, exclude=[2, 1, 2])
    assert "owner exclusions are 1 and 2" in answer
    assert "video model receives only the scene's starting picture" in answer
    assert "front or look" not in answer
    assert "other sides" not in answer
    kept = set_references(fake_model, say, exclude=None)
    assert "owner exclusions are 1 and 2" in kept
    assert "Nothing changed" in kept
    assert "owner exclusions are none" in set_references(fake_model, say, exclude=[])


def test_an_unknown_exclusion_is_refused_before_any_scene_work(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    before = paid_for()
    answer = set_references(fake_model, say, exclude=[99999])
    assert answer.startswith("Refused:") and "99999" in answer
    assert not steps.held and paid_for() == before


def test_an_owner_excluded_photo_is_refused_by_the_scene_agents_draw(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    assert "owner exclusions are 1" in set_references(fake_model, say, exclude=[1])
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Need a different view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the action.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert "excluded photo cannot be picked" in str(results_of("draw_starting_picture"))
    assert "draw_scene_picture" not in paid_for()


def test_exclusions_are_refused_while_the_scene_agent_is_working(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    answer = set_references(fake_model, say, exclude=[1])
    assert answer.startswith("Refused:") and "still being made" in answer
    assert len(steps.held) == 1


def test_a_talking_scene_refuses_reference_exclusions(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    calling(fake_model, say, ("set_references", {"scene": 1, "exclude": [1]}))
    assert "said to camera" in results_of("set_references")[-1]
