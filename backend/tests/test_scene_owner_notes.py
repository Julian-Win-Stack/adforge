"""Notes enter a scene's lasting conversation through the producer's real tools."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps, handoffs, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


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
                    ("edit_scene", {"scene": 2, "overlay": "A change while busy"}),
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
                    ),
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
    assert "pass_note" in str(results_of("edit_scene"))
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


def test_a_note_after_the_agent_ends_waits_for_the_old_step_to_finish(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from typing import Any

    from django.db.models.signals import post_save

    from agents.models import ToolCall

    sent = False

    def owner_note_after_give_up(sender: type[ToolCall], instance: ToolCall, **kwargs: Any) -> None:
        nonlocal sent
        if sent or instance.tool != "give_up" or instance.finished_at is None:
            return
        sent = True
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
                                    "instruction": "Keep trying from the new note.",
                                    "quotes": ["Keep trying"],
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed it on."),
            turn(says="The latest run finished."),
        )
        say("Keep trying")

    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the cup.", "quotes": ["Go on"]}),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("give_up", {"reason": "Need a handle view."})]),
        turn(calls=[("give_up", {"reason": "Still need a handle view."})]),
    )
    post_save.connect(owner_note_after_give_up, sender=ToolCall)
    try:
        steps.run_held()
    finally:
        post_save.disconnect(owner_note_after_give_up, sender=ToolCall)

    assert len(handoffs("scene_agent")) == 2
    assert "Keep trying from the new note." in str(handoffs("scene_agent")[-1])
    assert "Still need a handle view." in str(handoffs("produce")[-1])


def test_a_note_keeps_scene_work_completed_while_it_waited_for_the_job_lock(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from typing import Any

    from django.db import connection

    from agents import tasks

    from .conftest import WorkerStopped, facts_ok
    from .test_scene_agent_changes import ACTION, change

    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Try the new action.",
                "quotes": ["Go on"],
            },
        ),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent", turn(calls=[change()]), turn(calls=[change()]), WorkerStopped()
    )
    interleaved = False

    def work_before_lock(
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: Any,
    ) -> Any:
        nonlocal interleaved
        if not interleaved and '"jobs_job"' in sql and "FOR UPDATE" in sql:
            interleaved = True
            # Schedule the worker at the database boundary: it changes the real scene
            # after note validation, before the producer acquires the ad's lock.
            with pytest.raises(WorkerStopped):
                steps.run_next()
        return execute(sql, params, many, context)

    with connection.execute_wrapper(work_before_lock):
        calling(
            fake_model,
            say,
            (
                "pass_note",
                {
                    "scenes": [
                        {
                            "scene": 2,
                            "instruction": "Keep the new action.",
                            "quotes": ["Go on"],
                        }
                    ]
                },
            ),
        )
    assert interleaved
    assert "The scene row changed" in str(results_of("change_scene"))
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "Another view is needed."})])
    )
    fake_model.respond("produce", turn(says="The latest run ended."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()

    # The latest run restores the row it began with, including work the previous run
    # completed while the note waited. The producer sees that row through the chat.
    assert ACTION in handoffs("produce")[-1]["snapshot"]
