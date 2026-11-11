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


def test_one_refused_scene_does_not_cancel_other_scene_notes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import Scene, SceneAgentRun

    Scene.objects.filter(number=3).update(line=" ".join(["word"] * 50))
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {"scene": number, "instruction": instruction, "quotes": [instruction]}
                            for number, instruction in [
                                (1, "Show the handle."),
                                (2, "Show the rim."),
                                (3, "Show the lid."),
                            ]
                        ]
                    },
                )
            ]
        ),
        turn(says="The first two notes started; scene three needs a shorter line."),
    )
    say("Scene 1: Show the handle. Scene 2: Show the rim. Scene 3: Show the lid.")

    assert len(steps.held) == 2, results_of("pass_note")
    assert set(
        SceneAgentRun.objects.filter(finished_at=None).values_list(
            "agent_fix__step__scene__number", flat=True
        )
    ) == {1, 2}
    answer = results_of("pass_note")[-1]
    assert "Scene 1's note opens a new run" in answer
    assert "Scene 2's note opens a new run" in answer
    assert "Scene 3" in answer
    assert "14 seconds" in answer


def test_a_bad_quote_names_its_scene_and_leaves_other_notes_running(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
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
                                "instruction": "Show the handle.",
                                "quotes": ["Show the handle."],
                            },
                            {
                                "scene": 2,
                                "instruction": "Show the rim.",
                                "quotes": ["Show the rim.", "A purple elephant"],
                            },
                            {
                                "scene": 3,
                                "instruction": "Show the lid.",
                                "quotes": ["Show the lid."],
                            },
                        ]
                    },
                )
            ]
        ),
        turn(says="The handle and lid notes started; the rim quote needs correction."),
    )
    say("Scene 1: Show the handle. Scene 2: Show the rim. Scene 3: Show the lid.")

    assert len(steps.held) == 2, results_of("pass_note")
    answer = results_of("pass_note")[-1]
    assert "Scene 1's note opens a new run" in answer
    assert "Scene 3's note opens a new run" in answer
    refusal = next(line for line in answer.splitlines() if "Scene 2" in line)
    assert "A purple elephant" in refusal
    assert "wasn't found" in refusal


def test_restarting_a_multi_scene_note_does_not_open_successful_runs_twice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    from django.db.models.signals import pre_save

    from agents.models import ToolCall
    from agents.tasks import restart_dead_producers

    from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": number,
                                "instruction": "Show the handle.",
                                "quotes": ["Show the handle."],
                            }
                            for number in (1, 2, 3)
                        ]
                    },
                )
            ]
        ),
        turn(says="All three notes were handed on."),
    )
    before_checkpoint = the_worker_stops(
        pre_save,
        ToolCall,
        when=lambda call: call.tool == "pass_note" and call.finished_at is not None,
    )
    with before_checkpoint, pytest.raises(WorkerStopped):
        say("Show the handle.")
    assert len(steps.held) == 3
    the_producer_died(session_id)
    restart_dead_producers()

    assert len(steps.held) == 3
    answer = results_of("pass_note")[-1]
    for number in (1, 2, 3):
        assert f"Scene {number}'s note was handed on already." in answer
