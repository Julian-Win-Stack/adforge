"""Notes enter a scene's lasting conversation through the producer's real tools."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps, handoffs, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_note_during_a_turn_is_read_before_the_next_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
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
                                    "scene": 2,
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
        ("start_scene_agent", {"scene": 2, "instruction": "Show the cup.", "quotes": ["Go on"]}),
    )
    fake_model.respond(
        "scene_agent", note_during_turn, turn(calls=[("give_up", {"reason": "Need another view."})])
    )
    steps.run_held()

    given = str(handoffs("scene_agent")[-1])
    assert "Keep the handle visible." in given
    assert "Show its handle" in given
    assert "new note" in str(results_of("give_up")).lower()
    assert "Need another view" in str(handoffs("produce"))


def test_an_idle_note_preserves_the_earlier_scene_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the cup.", "quotes": ["Go on"]}),
    )
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need a handle view."})]))
    run(fake_model, steps)
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Try a closer view.", "quotes": ["Go on"]}]},
        ),
    )
    assert len(steps.held) == 1
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "Still need a handle view."})])
    )
    run(fake_model, steps)
    given = str(handoffs("scene_agent")[-1])
    assert "Need a handle view." in given
    assert "Try a closer view." in given


def test_one_note_reaches_two_scenes_with_only_their_own_quotes(
    fake_model: FakeModel,
    page_read: str,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from copy import deepcopy

    from .conftest import FACTS_OK, NO_CHOICES, broll
    from .test_producer_broll import BROLL_PLAN

    plan = deepcopy(BROLL_PLAN)
    plan["plan"]["scenes"][2] = broll(
        {
            **plan["plan"]["scenes"][2],
            "shows": "The mug stands on a shelf beside folded towels.",
        }
    )
    fake_model.respond("plan_ad", plan)
    calling(fake_model, say, ("plan_ad", {}))
    fake_model.respond("fact_check", FACTS_OK)
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    calling(fake_model, say, ("make_line_audio", {"scene": 2}), ("make_line_audio", {"scene": 3}))
    run(fake_model, steps)
    calling(
        fake_model,
        say,
        ("transcribe_line_audio", {"scene": 2}),
        ("transcribe_line_audio", {"scene": 3}),
    )
    run(fake_model, steps)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    fake_model.respond("produce", turn(says="I have the note."))
    say(
        "Use the desk for both scenes. For scene two, show its handle. "
        "For scene three, show the rim."
    )
    fake_model.respond("produce", turn(says="Thanks."))
    say("thanks")
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": "Show the handle on the desk.",
                        "quotes": [
                            "Use the desk for both scenes",
                            "For scene two, show its handle",
                        ],
                    },
                    {
                        "scene": 3,
                        "instruction": "Show the rim on the desk.",
                        "quotes": ["Use the desk for both scenes", "For scene three, show the rim"],
                    },
                ]
            },
        ),
    )
    assert len(steps.held) == 2, results_of("pass_note")
    fake_model.respond(
        "scene_agent",
        turn(calls=[("give_up", {"reason": "Need another view."})]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    run(fake_model, steps)

    for handoff in handoffs("scene_agent"):
        given = str(handoff)
        assert "Use the desk for both scenes" in given
        assert "thanks" not in given
        if "You own scene 2." in given:
            assert "For scene two, show its handle" in given
            assert "For scene three, show the rim" not in given
        else:
            assert "For scene three, show the rim" in given
            assert "For scene two, show its handle" not in given
