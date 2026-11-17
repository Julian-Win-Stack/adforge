"""Every starting picture shows the whole product, no planned move reveals a part it hides,
and the fix ladder never frames a part out (spec #706, ticket #707). Through the chat,
faking only outside services."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import TurnRequest

from .conftest import HeldSteps, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)

START = ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]})
GIVE_UP = turn(calls=[("give_up", {"reason": "Every step is tried."})])
FAILED_PICTURE = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The mug floats."}}


def flat(text: str) -> str:
    return " ".join(text.split())


def step_one_of(told: str) -> str:
    told = flat(told)
    start = told.index("1. From the first failed picture")
    return told[start : told.index("2. ", start)]


def assert_step_one_never_frames_a_part_out(step_one: str) -> None:
    assert "frame it out" not in step_one
    assert "angle it away" in step_one
    assert "cover it with a hand" in step_one
    assert "natural grip" in step_one
    # The needed-mark advice brings the product closer but keeps it whole in the frame.
    closer = step_one[step_one.index("closer to the camera") :]
    assert "whole product" in closer.split(". ")[0]


def form_notes(request: TurnRequest, tool: str) -> dict[str, str]:
    [spec] = [each for each in request.tools if each.name == tool]
    return {
        name: flat(field.description or "") for name, field in spec.arguments.model_fields.items()
    }


@pytest.fixture
def first_picture_failed(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> TurnRequest:
    fake_model.respond("check_scene_picture", FAILED_PICTURE)
    fake_model.respond(
        "scene_agent", turn(calls=[("draw_starting_picture", PICTURE_FORM)]), GIVE_UP
    )
    calling(fake_model, say, START)
    run(fake_model, steps)
    return next(each for each in fake_model.turns_taken if each.purpose == "scene_agent")


def test_the_scene_agent_is_told_the_whole_product_no_reveal_and_close_view_rules(
    first_picture_failed: TurnRequest,
) -> None:
    from jobs.picture_rules import CLOSE_VIEW_RULE, NO_REVEAL_RULE, WHOLE_PRODUCT_RULE

    told = flat(first_picture_failed.instructions)
    for rule in (WHOLE_PRODUCT_RULE, NO_REVEAL_RULE, CLOSE_VIEW_RULE):
        assert flat(rule) in told

    assert "Every starting picture" in WHOLE_PRODUCT_RULE
    assert "whole outline inside the frame" in WHOLE_PRODUCT_RULE
    assert "natural grip" in WHOLE_PRODUCT_RULE
    assert "frame edge never cuts" in WHOLE_PRODUCT_RULE
    assert "uncovers or turns toward a part the starting picture hides" in NO_REVEAL_RULE
    for move in ("turn", "lift", "hand move", "camera move"):
        assert move in NO_REVEAL_RULE, move
    assert "still shows the whole product" in CLOSE_VIEW_RULE
    assert "planned still shot's push in" in CLOSE_VIEW_RULE


def test_the_ladder_never_frames_a_part_out_in_the_prompt_or_the_picture_steps(
    first_picture_failed: TurnRequest,
) -> None:
    assert_step_one_never_frames_a_part_out(step_one_of(first_picture_failed.instructions))
    [failed] = results_of("draw_starting_picture")
    assert_step_one_never_frames_a_part_out(step_one_of(failed))


def test_the_picture_form_asks_for_the_whole_product_but_the_end_picture_form_does_not(
    first_picture_failed: TurnRequest,
) -> None:
    from jobs.picture_rules import WHOLE_PRODUCT_RULE

    notes = form_notes(first_picture_failed, "draw_starting_picture")
    assert flat(WHOLE_PRODUCT_RULE) in notes["size"]
    assert "whole product" in notes["camera"]
    # The label rule stays: close to the lens, and whole inside the frame on top of it.
    assert "held close to the lens" in notes["size"]
    end = form_notes(first_picture_failed, "make_end_picture")
    assert not any("whole product" in note for note in end.values())


def test_the_picture_check_is_not_told_the_whole_product_rule() -> None:
    from agents.scene_agent.picture_check import instructions
    from jobs.picture_rules import WHOLE_PRODUCT_RULE

    for starting in (True, False):
        assert flat(WHOLE_PRODUCT_RULE) not in flat(instructions(starting=starting))
