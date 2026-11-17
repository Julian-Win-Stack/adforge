"""Every starting picture shows the whole product, and no planned move reveals a part it
hides (spec #706, ticket #707). Through the chat, faking only outside services. The ladder's
step 1 is covered in test_scene_ladder_told.py."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import TurnRequest

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_ladder_told import DRAW, FAILED_PICTURE, GIVE_UP, START

pytestmark = pytest.mark.django_db(transaction=True)


def flat(text: str) -> str:
    return " ".join(text.split())


def form_notes(request: TurnRequest, tool: str) -> str:
    [spec] = [each for each in request.tools if each.name == tool]
    return flat(" ".join(str(field.description) for field in spec.arguments.model_fields.values()))


@pytest.fixture
def first_turn(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> TurnRequest:
    fake_model.respond("check_scene_picture", FAILED_PICTURE)
    fake_model.respond("scene_agent", turn(calls=[DRAW]), GIVE_UP)
    calling(fake_model, say, START)
    run(fake_model, steps)
    return next(each for each in fake_model.turns_taken if each.purpose == "scene_agent")


def test_the_scene_agent_is_told_the_whole_product_no_reveal_and_close_view_rules(
    first_turn: TurnRequest,
) -> None:
    from jobs.picture_rules import CLOSE_VIEW_RULE, NO_REVEAL_RULE, WHOLE_PRODUCT_RULE

    told = flat(first_turn.instructions)
    for rule in (WHOLE_PRODUCT_RULE, NO_REVEAL_RULE, CLOSE_VIEW_RULE):
        assert flat(rule) in told


def test_the_starting_picture_form_asks_for_the_whole_product_and_the_end_form_does_not(
    first_turn: TurnRequest,
) -> None:
    from jobs.picture_rules import LABEL_RULE, WHOLE_PRODUCT_RULE

    starting = form_notes(first_turn, "draw_starting_picture")
    assert flat(WHOLE_PRODUCT_RULE) in starting
    assert flat(LABEL_RULE) in starting
    assert flat(WHOLE_PRODUCT_RULE) not in form_notes(first_turn, "make_end_picture")


def test_the_picture_check_is_not_told_the_whole_product_rule() -> None:
    from agents.scene_agent.picture_check import instructions
    from jobs.picture_rules import WHOLE_PRODUCT_RULE

    for starting in (True, False):
        assert flat(WHOLE_PRODUCT_RULE) not in flat(instructions(starting=starting))
