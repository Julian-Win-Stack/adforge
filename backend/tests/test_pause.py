"""Pausing cancels queued scene agents, keeps results from paid work already in flight,
and parks before new work. The producer hears which scenes were cancelled or kept; until
the owner writes, every tool that spends answers "paused". Driven through the chat."""

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.db.models.signals import pre_save
from django.utils import timezone
from rest_framework.test import APIClient

from agents import pause as pause_module
from agents import tasks
from agents.loop import SYSTEM_NOTE
from agents.models import ToolCall
from agents.pause import PAUSED_NOTICE
from chat.models import Session
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import AgentFix, Job, ProducedItem, SceneStep

from .conftest import (
    HeldSteps,
    ScriptedProducer,
    chat,
    given_to_the_producer,
    paid_for,
    producer_turns,
    results_of,
    scripted_turns,
)
from .test_producer_restarts import WorkerStopped, the_worker_stops
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = [pytest.mark.django_db(transaction=True)]


def pause(api: APIClient, session_id: str) -> dict[str, Any]:
    pressed = api.post(f"/api/sessions/{session_id}/pause/", {}, format="json")
    assert pressed.status_code == 200, pressed.json()
    answer: dict[str, Any] = pressed.json()
    return answer


def scene_call(scene: int, why: str) -> tuple[str, dict[str, Any]]:
    return (
        "start_scene_agent",
        {"scene": scene, "instruction": why, "quotes": ["Make"], "why": why},
    )


def step_of(scene: int) -> SceneStep:
    return SceneStep.objects.filter(scene__number=scene).latest("id")


def taken_by_the_worker(scene: int) -> None:
    """The worker took scene `scene`'s step and is working on it, as run_scene_step marks it."""
    now = timezone.now()
    SceneStep.objects.filter(pk=step_of(scene).pk).update(taken_at=now, seen_at=now)


def the_worker_carries_on(steps: HeldSteps) -> None:
    """The worker that took each step works on it to the end. Its held task stands for that
    same copy, so the claim marked by taken_by_the_worker is handed back to it first: any
    other copy of a step taken and alive quits (tests/test_one_copy_each.py)."""
    SceneStep.objects.filter(pk__in=steps.held).update(taken_at=None)
    steps.run_held()


def picture_paid_but_not_kept(fake_model: FakeModel) -> None:
    """A taken scene's real drawing checkpoint was paid before its worker stopped."""
    fake_model.respond("scene_agent", turn(calls=[("draw_starting_picture", PICTURE_FORM)]))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    not_kept = the_worker_stops(
        pre_save, ProducedItem, when=lambda item: item.kind == "starting_picture"
    )
    with not_kept, pytest.raises(WorkerStopped):
        tasks.run_scene_step(step_of(1).pk)
    # Its heartbeat stopped with its worker, so the copy handed out again may take it.
    gone = timezone.now() - timedelta(seconds=settings.STEP_DEAD_AFTER_SECONDS + 1)
    SceneStep.objects.filter(pk=step_of(1).pk).update(seen_at=gone)
    assert paid_for().count("draw_scene_picture") == 1
    assert not ProducedItem.objects.filter(kind="starting_picture").exists()


def notes_given(number: int) -> list[str]:
    return [
        each["text"]
        for each in given_to_the_producer(number)
        if each["kind"] == "step_finished" and each["text"].startswith(SYSTEM_NOTE)
    ]


@pytest.fixture
def three_scenes_started(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """Every scene agent started; none taken by the worker yet."""
    fake_model.respond(
        "produce",
        turn(
            calls=[
                scene_call(1, "Scene 1 opens the ad."),
                scene_call(2, "Scene 2 shows the mug in use."),
                scene_call(3, "Scene 3 closes on the price."),
            ]
        ),
        turn(says="I've started every scene's agent."),
    )
    say("Make every scene")


def test_pause_cancels_the_queued_steps_keeps_the_running_one_and_starts_nothing_until_the_owner_writes(  # noqa: E501
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    picture_paid_but_not_kept(fake_model)
    fake_model.respond(
        "produce",
        # It tries to start one again: the tool answers paused.
        turn(calls=[scene_call(2, "Scene 2 still needs its picture.")]),
        turn(says="Paused. Scene 1's picture is finishing. What should change?"),
    )

    assert pause(api, session_id)["paused"] is True

    # The queued two are cancelled, the taken one runs on.
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["running", "cancelled", "cancelled"]
    # The producer is told which were cancelled, what each was for, and which runs on.
    told = notes_given(producer_turns() - 1)[-1]
    cancelled, running = told.split("Still running")
    assert "scene 2's scene agent" in cancelled
    assert "Scene 2 shows the mug in use." in cancelled
    assert "scene 3's scene agent" in cancelled
    assert "Scene 3 closes on the price." in cancelled
    assert "scene 1's scene agent" not in cancelled
    assert "scene 1's scene agent" in running
    # The note is all it is told of them: no cancelled step comes as a step result of its own.
    assert [
        each["text"]
        for each in given_to_the_producer(producer_turns() - 1)
        if each["kind"] == "step_finished" and not each["text"].startswith(SYSTEM_NOTE)
    ] == []
    # Every scene tool answers paused, and starts nothing.
    assert (
        ToolCall.objects.filter(tool="start_scene_agent")
        .latest("id")
        .result.startswith("Refused: paused")
    )
    assert SceneStep.objects.count() == 3
    assert chat(api, session_id)[-1] == (
        "agent",
        "Paused. Scene 1's picture is finishing. What should change?",
    )

    # The running step finishes and is kept; the cancelled ones are never worked on.
    fake_model.respond(
        "produce",
        turn(calls=[scene_call(3, "Scene 3 still needs its picture.")]),
        turn(says="Scene 1's picture is ready."),
    )
    steps.run_held()
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["finished", "cancelled", "cancelled"]
    assert paid_for().count("draw_scene_picture") == 1
    assert AgentFix.objects.get(step=step_of(1)).paused_at is not None
    assert ProducedItem.objects.filter(kind="starting_picture", scene__number=1).count() == 1
    assert (
        ToolCall.objects.filter(tool="start_scene_agent")
        .latest("id")
        .result.startswith("Refused: paused")
    )

    # The owner's message clears the pause, and the producer calls again what is needed.
    fake_model.respond(
        "produce",
        turn(calls=[scene_call(2, "The owner wants scene 2 after all.")]),
        turn(says="Making scene 2's picture again."),
    )
    say("Go on, but only scene 2")

    assert step_of(2).status == "running"
    assert (
        ToolCall.objects.filter(tool="start_scene_agent")
        .latest("id")
        .result.startswith("Started scene 2's agent")
    )
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False


def test_a_pause_pressed_while_the_producer_decides_reaches_it_before_any_of_its_calls_run(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        meanwhile(
            lambda: pause(api, session_id),
            turn(calls=[("create_music", {"mood": "calm"}), scene_call(1, "Scene 1 first.")]),
        ),
        turn(says="Paused. Nothing is running. What should change?"),
    )

    say("Make the music and scene 1's picture")

    # Neither ran: the producer read the pause's note first.
    assert [result.split(".")[0] for result in results_of("create_music")] == ["Refused: paused"]
    assert [result.split(".")[0] for result in results_of("start_scene_agent")] == [
        "Refused: paused"
    ]
    held = ToolCall.objects.filter(tool__in=["create_music", "start_scene_agent"])
    assert [call.answer for call in held] == ["cant", "cant"]
    assert "make_music" not in paid_for()
    assert not SceneStep.objects.exists()
    assert "The shop owner pressed pause" in notes_given(producer_turns())[-1]


def test_a_pause_pressed_while_the_producer_decides_holds_even_a_free_call_until_it_reads_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        meanwhile(
            lambda: pause(api, session_id),
            turn(calls=[("cost_so_far", {"why": "The shop owner asked."})]),
        ),
        turn(says="Paused. What should change?"),
    )

    say("What has it cost so far?")

    # Refused unrun, as a can't, and ended: never run again later.
    [held] = ToolCall.objects.filter(tool="cost_so_far")
    assert held.result.startswith("Refused: paused")
    assert (held.answer, held.finished_at is not None) == ("cant", True)


def test_a_message_sent_right_after_the_pause_lets_the_turns_calls_run(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def paused_then_written() -> None:
        pause(api, session_id)
        sent = api.post(
            f"/api/sessions/{session_id}/messages/", {"text": "Go on after all"}, format="json"
        )
        assert sent.status_code == 201, sent.json()

    fake_model.respond(
        "produce",
        meanwhile(paused_then_written, turn(calls=[scene_call(1, "Scene 1 opens the ad.")])),
        turn(says="Making scene 1's picture."),
        turn(says="Going on."),
    )

    say("Make scene 1's picture")

    # The pause ended before the call ran, so it started its step.
    assert results_of("start_scene_agent")[0].startswith("Started scene 1's agent")
    assert step_of(1).status == "running"


def test_a_pause_pressed_during_the_producers_last_turn_is_read_on_one_more(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        meanwhile(lambda: pause(api, session_id), turn(says="Nothing is left to make.")),
        turn(says="Paused. Nothing is running."),
    )

    say("Anything left?")

    assert fake_model.left("produce") == 0
    assert "The shop owner pressed pause" in notes_given(producer_turns())[-1]


def test_a_step_the_chat_doesnt_follow_is_cancelled_with_no_notice(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="The old line's audio is queued."))
    say("Pause the work left from my earlier ad")
    # An authentic queued checkpoint from an earlier ad; its retired tool never runs.
    job = Job.objects.get()
    assert job.session is not None
    call = ToolCall.objects.create(
        session=job.session,
        job=job,
        agent="producer",
        tool="make_line_audio",
        call_id="historical-audio-before-pause",
        arguments={"scene": 1},
        result="Started scene 1's audio. It isn't made yet.",
        answer="made",
        finished_at=timezone.now(),
    )
    scene = job.scenes.get(number=1)
    SceneStep.objects.create(
        scene=scene,
        tool_call=call,
        kind=SceneStep.Kind.LINE_AUDIO,
        **SceneStep.snapshot(scene),
    )
    fake_model.respond("produce", turn(says="Paused."))

    pause(api, session_id)

    assert step_of(1).status == "cancelled"
    # A line's audio is the producer's to judge: the shop owner isn't told of it.
    assert not [text for _, text in chat(api, session_id) if "cancelled by the pause" in text]


def test_steps_the_pause_cancelled_stay_cancelled_as_the_worker_starts_again(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    steps: HeldSteps,
    session_id: str,
) -> None:
    taken_by_the_worker(1)
    fake_model.respond("produce", turn(says="Paused. Scene 1's picture is finishing."))
    pause(api, session_id)
    turns = producer_turns()

    fake_model.respond("produce", turn(says="Scene 1's picture stopped partway."))
    tasks.carry_on_after_the_worker_starts()

    # The taken H3 parent resumes, parks before unpaid work, and is told once.
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["running", "cancelled", "cancelled"]
    the_worker_carries_on(steps)
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["finished", "cancelled", "cancelled"]
    assert AgentFix.objects.get(step=step_of(1)).paused_at is not None
    assert producer_turns() == turns + 1
    told = [each["text"] for each in given_to_the_producer(producer_turns())[-1:]]
    assert "scene 1's agent parked for the owner's pause" in told[0]
    # The chat says once that each cancelled picture wasn't made, and never that it failed.
    texts = [text for _, text in chat(api, session_id)]
    assert sum("Scene 2's clip" in text for text in texts) == 1
    assert sum("Scene 3's clip" in text for text in texts) == 1


def test_the_chat_shows_the_pause_until_the_owner_writes_and_a_second_press_does_nothing_more(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="Paused. Nothing is running."))
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False

    pause(api, session_id)
    shown = chat(api, session_id)
    turns = producer_turns()
    # The chat says so once.
    assert shown.count(("notice", PAUSED_NOTICE)) == 1
    assert pause(api, session_id) == {"paused": True}

    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is True
    # No second note, notice or producer turn.
    assert chat(api, session_id) == shown
    assert producer_turns() == turns
    # Each cancelled picture's card is told it wasn't made.
    ended = [
        (message["level"], message["step"])
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["step"] is not None and message["step"]["state"] == "not_made"
    ]
    assert ended == [
        ("info", {"scene": scene, "kind": "clip", "state": "not_made"}) for scene in (1, 2, 3)
    ]

    fake_model.respond("produce", turn(says="What should change?"))
    say("Hold on, let me think")
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False


def test_while_paused_every_tool_that_costs_money_answers_paused_and_a_free_one_runs(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    steps: HeldSteps,
    session_id: str,
) -> None:
    taken_by_the_worker(1)
    fake_model.respond("produce", turn(says="Paused. Scene 1's picture is finishing."))
    pause(api, session_id)

    # Woken by the running step's result while still paused, having read the note already.
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("create_music", {"mood": "calm"}),
                ("plan_ad", {}),
                ("ask_owner", {"question": "Scene 1's picture is ready. What should change?"}),
            ]
        ),
    )
    the_worker_carries_on(steps)

    assert [result.split(".")[0] for result in results_of("create_music")] == ["Refused: paused"]
    assert [result.split(".")[0] for result in results_of("plan_ad")[-1:]] == ["Refused: paused"]
    assert not results_of("ask_owner")[0].startswith("Refused")
    assert "make_music" not in paid_for()
    assert paid_for().count("plan_ad") == 1


def test_a_step_started_as_the_pause_is_pressed_is_cancelled_by_it_never_paid_for(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scene tool was let run before the pause, and the pause is pressed, on another
    connection, while it starts its step."""
    pressed: list[threading.Thread] = []
    started = SceneStep.snapshot

    def pressed_meanwhile(scene: Any) -> dict[str, Any]:
        if not pressed:

            def pressing() -> None:
                try:
                    pause_module.pause(Session.objects.get(pk=session_id), wake=False)
                finally:
                    connection.close()

            pressed.append(threading.Thread(target=pressing))
            pressed[0].start()
            # Long enough for the pause to commit, unless it waits for the step to start.
            pressed[0].join(timeout=2)
        return started(scene)

    monkeypatch.setattr(SceneStep, "snapshot", staticmethod(pressed_meanwhile))
    fake_model.respond(
        "produce",
        turn(calls=[scene_call(1, "Scene 1 opens the ad.")]),
        turn(says="Started scene 1's picture."),
    )

    say("Make scene 1")
    pressed[0].join(timeout=30)

    # Either refused, or started and then cancelled by the pause: never left to run.
    assert [step.status for step in SceneStep.objects.all()] in ([], ["cancelled"])
    steps.run_held()
    assert "draw_scene_picture" not in paid_for()


def test_the_poll_says_whether_the_pause_holds_and_a_message_sent_while_paused_ends_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
) -> None:
    def polled() -> str:
        return str(api.get(f"/api/sessions/{session_id}/messages/?after=0")["X-Session-Paused"])

    assert polled() == "false"
    fake_model.respond("produce", turn(says="Paused."))
    pause(api, session_id)
    assert polled() == "true"

    fake_model.respond("produce", turn(says="Going on."))
    sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": "Go on"}, format="json")

    assert sent.json()["session"]["paused"] is False
    assert polled() == "false"


def test_pausing_while_another_session_works_holds_and_cancels_but_starts_no_producer(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    session_id: str,
) -> None:
    working = Session.objects.create()
    Session.objects.filter(pk=working.pk).update(producer_running=True)
    turns = producer_turns()

    pause(api, session_id)

    assert producer_turns() == turns
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["cancelled"] * 3
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is True


def test_a_pause_with_nothing_waiting_or_running_tells_the_producer_so(
    api: APIClient, fake_model: FakeModel, checked: None, session_id: str
) -> None:
    fake_model.respond("produce", turn(says="Paused."))

    pause(api, session_id)

    told = notes_given(producer_turns())[-1]
    assert "No step was waiting, so none was cancelled." in told
    assert "Still running: none." in told
    # And how to answer it: one line to the shop owner, then wait for them.
    assert "tell them in one line that you paused" in told


def test_the_pause_note_names_each_step_by_its_call_and_the_running_ones_oldest_first(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    session_id: str,
) -> None:
    taken_by_the_worker(1)
    taken_by_the_worker(2)
    fake_model.respond("produce", turn(says="Paused."))

    pause(api, session_id)

    cancelled, running = notes_given(producer_turns())[-1].split("Still running")
    # The call that started it, with what it was asked.
    assert "start_scene_agent {" in cancelled
    assert '"scene": 3' in cancelled
    assert '"instruction": "Scene 3 closes on the price."' in cancelled
    assert '"quotes": ["Make"]' in cancelled
    assert running.index("scene 1's scene agent") < running.index("scene 2's scene agent")


def test_while_paused_the_tools_that_spend_nothing_still_run(
    api: APIClient,
    fake_model: FakeModel,
    three_scenes_started: None,
    steps: HeldSteps,
    session_id: str,
) -> None:
    taken_by_the_worker(1)
    fake_model.respond("produce", turn(says="Paused. Scene 1's picture is finishing."))
    pause(api, session_id)
    free: list[tuple[str, dict[str, Any]]] = [
        ("approve_owner_part", {"scene": 1}),
        ("use_version", {"scene": 1, "version": 1}),
        ("keep_try", {"scene": 1, "try_number": 0}),
        ("assemble_ad", {}),
        ("cost_so_far", {}),
        ("read_ad", {"scene": None}),
        ("read_earlier_call", {"call_id": "call_none"}),
        # Not a tool it has: refused as such, not as paused.
        ("no_such_tool", {}),
    ]

    # Woken by the running step's result while still paused, having read the note already.
    why = "To see where the ad stands."
    fake_model.respond(
        "produce",
        turn(calls=[(tool, {**arguments, "why": why}) for tool, arguments in free]),
        turn(says="Scene 1's picture is ready."),
    )
    the_worker_carries_on(steps)

    for tool, _ in free:
        [answer] = results_of(tool)
        assert not answer.startswith("Refused: paused"), (tool, answer)


# --- Whole-build check C (spec #247 Testing Decisions, ticket #266) --------------------------


def test_the_scripted_producer_paused_with_two_steps_queued_and_one_running_re_calls_one_after(
    api: APIClient,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
) -> None:
    producer.told(
        "Make every scene",
        [
            scene_call(1, "Scene 1 opens the ad."),
            scene_call(2, "Scene 2 shows the mug in use."),
            scene_call(3, "Scene 3 closes on the price."),
        ],
        "I've started every scene's agent.",
    )
    picture_paid_but_not_kept(fake_model)
    paid = paid_for()

    # The shop owner presses pause: the producer, woken by it, tries a scene tool, which
    # answers paused, and says one line.
    fake_model.respond(
        "produce",
        *scripted_turns(
            scene_call(2, "Scene 2 still needs its picture."),
            "Paused. Scene 1's picture is finishing. What should change?",
        ),
    )
    assert pause(api, session_id)["paused"] is True
    assert fake_model.left("produce") == 0

    # Two queued steps cancelled, the running one kept; the note names each and what it was
    # for; the scene tool answered paused and started nothing.
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["running", "cancelled", "cancelled"]
    cancelled, running = notes_given(producer_turns() - 1)[-1].split("Still running")
    for scene, why in ((2, "Scene 2 shows the mug in use."), (3, "Scene 3 closes on the price.")):
        assert f"scene {scene}'s scene agent" in cancelled and why in cancelled
    assert "scene 1's scene agent" in running
    assert results_of("start_scene_agent")[-1].startswith("Refused: paused")
    assert SceneStep.objects.count() == 3
    assert producer.said()[-1] == "Paused. Scene 1's picture is finishing. What should change?"

    # The running step finishes and is kept; the woken producer's scene tool still answers
    # paused; the cancelled steps are never worked on.
    producer.woken(scene_call(3, "Scene 3 still needs its picture."), "Scene 1 is ready.")
    producer.steps.run_held()
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["finished", "cancelled", "cancelled"]
    assert results_of("start_scene_agent")[-1].startswith("Refused: paused")
    # The paid drawing is kept without paying again, and no clip starts while paused.
    assert paid_for() == paid
    assert paid_for().count("draw_scene_picture") == 1
    assert ProducedItem.objects.filter(kind="starting_picture", scene__number=1).count() == 1
    assert "make_broll_clip" not in paid_for()

    # The shop owner writes: the pause ends, and the producer re-calls the one still needed.
    producer.told(
        "Go on, but only scene 2",
        scene_call(2, "The owner wants scene 2 after all."),
        "Making scene 2's picture again.",
    )
    assert results_of("start_scene_agent")[-1].startswith("Started scene 2's agent")
    assert step_of(2).status == "running"
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False
