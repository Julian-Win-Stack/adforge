"""Talking scenes use the same lasting chat and picture checks as B-roll."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import FACTS_OK, NO_CHOICES, HeldSteps, facts_ok, handoffs, results_of
from .test_h3_planning import h3_plan
from .test_producer_broll import calling, run
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def talking_ready(
    page_read: str,
    fake_model: FakeModel,
    say: Callable[..., None],
) -> None:
    fake_model.respond("plan_ad", h3_plan())
    calling(fake_model, say, ("plan_ad", {}))
    fake_model.respond("fact_check", FACTS_OK)
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))


def test_both_kinds_draw_from_their_shared_picture_through_the_chat(
    talking_ready: None,
    fake_model: FakeModel,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    for scene in (1, 2):
        form = (
            {
                **PICTURE_FORM,
                "person_and_room": "The woman from the shared picture, her face in frame",
                "pose": "She holds the mug beside her face with its handle visible",
                "bodies": "One woman, with her face and both hands visible",
            }
            if scene == 1
            else PICTURE_FORM
        )
        fake_model.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", form)]),
            turn(calls=[("give_up", {"reason": "Need another product view."})]),
        )
        calling(
            fake_model,
            say,
            ("start_scene_agent", {"scene": scene, "instruction": "Show it.", "quotes": ["Go on"]}),
        )
        run(fake_model, steps)
    agents = handoffs("scene_agent")
    assert "Shared person and room" in str(agents[0])
    assert "Shared room alone" in str(agents[2])
    draws = handoffs("draw_scene_picture")
    assert "person and room" in draws[0]["prompt"]
    assert "her face in frame" in draws[0]["prompt"]
    assert "holds the mug" in draws[0]["prompt"]
    assert "room alone" in draws[1]["prompt"]
    assert len(handoffs("check_scene_picture")) == 2
    assert "Refused" not in str(results_of("start_scene_agent"))


def test_a_busy_talking_scene_reads_the_owners_checked_note_in_its_same_chat(
    talking_ready: None,
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    def note_during_turn() -> Turn:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 1,
                                    "instruction": "Keep the handle visible.",
                                    "quotes": ["Show its handle"],
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed it on."),
            turn(says="The scene finished."),
        )
        say("Show its handle")
        return turn(calls=[("give_up", {"reason": "This old decision must not end the new run."})])

    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 1,
                "instruction": "Show it.",
                "quotes": ["Go on"],
            },
        ),
    )
    fake_model.respond(
        "scene_agent",
        note_during_turn,
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    steps.run_held()
    given = str(handoffs("scene_agent")[-1])
    assert "Keep the handle visible." in given
    assert "Show its handle" in given
    assert "new note" in str(results_of("give_up")).lower()


def test_changing_kind_needs_a_why_and_still_refuses_another_scenes_action(
    talking_ready: None,
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fields = {
        "line": h3_plan()["plan"]["scenes"][2]["line"],
        "shows": "Tea is stirred in the mug with a spoon.",
        "usage": "",
        "result": "",
        "broll_kind": "showcase",
        "person_shown": "no face",
        "kind": "broll",
    }
    why = "A close view inside the mug is needed."
    fake_model.respond("fact_check", facts_ok(3))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", fields)]),
        turn(
            calls=[
                (
                    "change_scene",
                    {
                        **fields,
                        "shows": h3_plan()["plan"]["scenes"][1]["shows"],
                        "kind_why": why,
                    },
                )
            ]
        ),
        turn(calls=[("change_scene", {**fields, "kind_why": why})]),
        turn(calls=[("change_scene", {**fields, "kind_why": why})]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 3,
                "instruction": "Try the hands shot.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    results = results_of("change_scene")
    assert "say why her hands cannot show" in results[0]
    assert "scene 2 already films this action" in results[1]
    assert "Nothing was changed" in results[2]
    assert "The scene row changed" in results[3]
    assert "scene kind: broll" in str(handoffs("scene_agent")[-1])
    assert why in str(handoffs("scene_agent")[-1])
    # A give-up restores the kind as well as the action before the producer sees it.
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need a photo."})]))
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 3,
                        "instruction": "Try once more.",
                        "quotes": ["Go on"],
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    assert "scene kind: talking" in str(handoffs("scene_agent")[-1])


def test_the_opening_scene_cannot_change_to_broll(
    talking_ready: None,
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "change_scene",
                    {
                        "line": h3_plan()["plan"]["scenes"][0]["line"],
                        "shows": "A spoon stirs tea inside the mug.",
                        "usage": "",
                        "result": "",
                        "broll_kind": "showcase",
                        "person_shown": "no face",
                        "kind": "broll",
                        "kind_why": "Its inside needs a close view.",
                    },
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Keep the opening talking."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 1,
                "instruction": "Try a close view.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    assert "first scene" in results_of("change_scene")[-1]
    assert "talking" in results_of("change_scene")[-1]
    assert "scene kind: talking" in str(handoffs("scene_agent")[-1])
