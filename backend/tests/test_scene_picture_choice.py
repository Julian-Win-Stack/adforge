"""Compare and choose starting pictures through a scene-agent run."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import ToolUse, Turn, UnusableReply

from .conftest import HeldSteps, handoffs, paid_for, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("edit", [False, True])
def test_new_picture_is_compared_with_the_picture_it_replaces_before_drawing_again(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    edit: bool,
) -> None:
    second = (
        (
            "edit_starting_picture",
            {
                "keep": ["the room"],
                "change": "Turn the handle toward the camera",
                "anything_else": "not needed here",
                "picks": PICTURE_FORM["picks"],
            },
        )
        if edit
        else ("draw_starting_picture", {**PICTURE_FORM, "pose": "Handle faces camera"})
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[second]),
        turn(
            says="The handle still points away; the intended change is absent. I will draw again.",
            calls=[("draw_starting_picture", {**PICTURE_FORM, "pose": "Handle toward camera"})],
        ),
        turn(calls=[("give_up", {"reason": "Need a clearer view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][2]
    draws = [
        each
        for each in request.handoff.conversation
        if isinstance(each, ToolUse)
        and each.tool in {"draw_starting_picture", "edit_starting_picture"}
    ]
    first, second_result = draws
    assert len(second_result.pictures) == 2
    assert second_result.pictures[0].key == first.pictures[0].key
    assert second_result.pictures[0].label.startswith("Previous starting picture ")
    assert second_result.pictures[1].label.startswith("Starting picture ")
    assert "Say whether" in second_result.result
    assert paid_for().count("draw_scene_picture") == 3
    assert "make_broll_clip" not in paid_for()


@pytest.mark.parametrize("unchecked", [False, True])
def test_agent_can_film_an_earlier_eligible_picture_and_the_card_shows_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    unchecked: bool,
) -> None:
    if unchecked:
        fake_model.respond(
            "check_scene_picture", UnusableReply("Unavailable", input_tokens=0, output_tokens=0)
        )
    selected: list[str] = []

    def choose_earlier() -> Turn:
        request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
        first = next(
            each
            for each in request.handoff.conversation
            if isinstance(each, ToolUse) and each.tool == "draw_starting_picture"
        )
        selected.append(first.pictures[0].key)
        return turn(
            says="The new picture did not improve the handle; I will film the earlier one.",
            calls=[("film", {**FILM, "picture": int(first.pictures[0].label.split()[-1])})],
        )

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "pose": "Handle faces camera"})]),
        choose_earlier,
        turn(calls=[("give_up", {"reason": "The clip should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert handoffs("make_broll_clip")[0]["starting_picture"] == selected[0]
    card = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert card["picture"].endswith(selected[0])
    assert card["versions"][0]["picture"] == card["picture"]
    assert paid_for().count("draw_scene_picture") == 2
    assert paid_for().count("make_broll_clip") == 1


@pytest.mark.parametrize("draws", [1, 6])
def test_naming_a_failed_picture_does_not_bypass_the_closest_picture_rule(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    draws: int,
) -> None:
    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The mug floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: failed)

    def choose_failed() -> Turn:
        request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
        first = next(
            each
            for each in request.handoff.conversation
            if isinstance(each, ToolUse) and each.tool == "draw_starting_picture"
        )
        return turn(
            calls=[("film", {**FILM, "picture": int(first.result.split(":")[0].split()[-1])})]
        )

    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(draws)],
        choose_failed,
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "selected starting picture has passed" in str(results_of("film"))
    assert "make_broll_clip" not in paid_for()


@pytest.mark.parametrize("edit", [False, True])
def test_redraw_after_filming_an_earlier_picture_compares_and_edits_that_picture(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    edit: bool,
) -> None:
    from .test_clip_judge import product

    fake_model.respond("check_clip_product", product("fail", "The mug's base changes shape."))
    selected: list[str] = []

    def choose_earlier() -> Turn:
        request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
        first = next(
            each
            for each in request.handoff.conversation
            if isinstance(each, ToolUse) and each.tool == "draw_starting_picture"
        )
        selected.append(first.pictures[0].key)
        return turn(
            calls=[("film", {**FILM, "picture": int(first.result.split(":")[0].split()[-1])})]
        )

    repair = (
        (
            "edit_starting_picture",
            {
                "keep": ["the room"],
                "change": "Rest the mug on the desk",
                "anything_else": "not needed here",
                "picks": PICTURE_FORM["picks"],
            },
        )
        if edit
        else ("draw_starting_picture", {**PICTURE_FORM, "pose": "Mug rests on desk"})
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "pose": "Handle faces camera"})]),
        choose_earlier,
        turn(says="The filmed picture needs support under the mug.", calls=[repair]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
    repair_result = next(
        each
        for each in reversed(request.handoff.conversation)
        if isinstance(each, ToolUse) and each.tool == repair[0]
    )
    assert repair_result.pictures[0].key == selected[0]
    assert len(repair_result.pictures) == 2
    assert repair_result.pictures[0].label.startswith("Previous starting picture ")
    assert repair_result.pictures[1].label.startswith("Starting picture ")
    assert repair_result.pictures[1].key != selected[0]
    if edit:
        assert handoffs("draw_scene_picture")[-1]["pictures"][0] == selected[0]
    assert paid_for().count("draw_scene_picture") == 3
    assert paid_for().count("make_broll_clip") == 1
