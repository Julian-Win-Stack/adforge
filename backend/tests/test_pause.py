"""The pause button (spec #247 point 14), driven through the chat. Pausing cancels every scene
step still waiting for the worker, lets the ones it took finish and keeps them, and tells the
producer which is which; until the shop owner writes, every scene tool answers "paused"."""

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from agents import tasks
from agents.loop import SYSTEM_NOTE
from agents.models import ToolCall
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import SceneStep

from .conftest import (
    HeldSteps,
    chat,
    given_to_the_producer,
    paid_for,
    producer_turns,
    results_of,
)
from .test_producer_scenes import CHOICE

pytestmark = pytest.mark.django_db(transaction=True)


def pause(api: APIClient, session_id: str) -> dict[str, Any]:
    pressed = api.post(f"/api/sessions/{session_id}/pause/", {}, format="json")
    assert pressed.status_code == 200, pressed.json()
    answer: dict[str, Any] = pressed.json()
    return answer


def picture_call(scene: int, why: str) -> tuple[str, dict[str, Any]]:
    return ("make_starting_picture", {"scene": scene, "note": None, "why": why})


def step_of(scene: int) -> SceneStep:
    return SceneStep.objects.filter(scene__number=scene).latest("id")


def taken_by_the_worker(scene: int) -> None:
    """The worker took scene `scene`'s step and is working on it, as run_scene_step marks it."""
    now = timezone.now()
    SceneStep.objects.filter(pk=step_of(scene).pk).update(taken_at=now, seen_at=now)


def notes_given(number: int) -> list[str]:
    return [
        each["text"]
        for each in given_to_the_producer(number)
        if each["kind"] == "step_finished" and each["text"].startswith(SYSTEM_NOTE)
    ]


@pytest.fixture
def three_pictures_started(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """Every scene's starting picture started; none taken by the worker yet."""
    fake_model.respond(
        "produce",
        turn(
            calls=[
                picture_call(1, "Scene 1 opens the ad."),
                picture_call(2, "Scene 2 shows the mug in use."),
                picture_call(3, "Scene 3 closes on the price."),
            ]
        ),
        turn(says="I've started every scene's picture."),
    )
    say("Make every scene's starting picture")


def test_pause_cancels_the_queued_steps_keeps_the_running_one_and_starts_nothing_until_the_owner_writes(  # noqa: E501
    api: APIClient,
    fake_model: FakeModel,
    three_pictures_started: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    taken_by_the_worker(1)
    fake_model.respond(
        "produce",
        # It tries to start one again: the tool answers paused.
        turn(calls=[picture_call(2, "Scene 2 still needs its picture.")]),
        turn(says="Paused. Scene 1's picture is finishing. What should change?"),
    )

    assert pause(api, session_id)["paused"] is True

    # The queued two are cancelled, the taken one runs on.
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["running", "cancelled", "cancelled"]
    # The producer is told which were cancelled, what each was for, and which runs on.
    told = notes_given(producer_turns() - 1)[-1]
    cancelled, running = told.split("Still running")
    assert "scene 2's starting picture" in cancelled
    assert "Scene 2 shows the mug in use." in cancelled
    assert "scene 3's starting picture" in cancelled
    assert "Scene 3 closes on the price." in cancelled
    assert "scene 1's starting picture" not in cancelled
    assert "scene 1's starting picture" in running
    # The note is all it is told of them: no cancelled step comes as a step result of its own.
    assert [
        each["text"]
        for each in given_to_the_producer(producer_turns() - 1)
        if each["kind"] == "step_finished" and not each["text"].startswith(SYSTEM_NOTE)
    ] == []
    # Every scene tool answers paused, and starts nothing.
    assert (
        ToolCall.objects.filter(tool="make_starting_picture")
        .latest("id")
        .result.startswith("Refused: paused")
    )
    assert SceneStep.objects.count() == 3
    assert chat(api, session_id)[-1] == (
        "agent",
        "Paused. Scene 1's picture is finishing. What should change?",
    )

    # The running step finishes and is kept; the cancelled ones are never worked on.
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond(
        "produce",
        turn(calls=[picture_call(3, "Scene 3 still needs its picture.")]),
        turn(says="Scene 1's picture is ready."),
    )
    steps.run_held()
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["finished", "cancelled", "cancelled"]
    assert paid_for().count("choose_starting_picture") == 1
    assert (
        ToolCall.objects.filter(tool="make_starting_picture")
        .latest("id")
        .result.startswith("Refused: paused")
    )

    # The owner's message clears the pause, and the producer calls again what is needed.
    fake_model.respond(
        "produce",
        turn(calls=[picture_call(2, "The owner wants scene 2 after all.")]),
        turn(says="Making scene 2's picture again."),
    )
    say("Go on, but only scene 2")

    assert step_of(2).status == "running"
    assert (
        ToolCall.objects.filter(tool="make_starting_picture")
        .latest("id")
        .result.startswith("Started scene 2's starting picture")
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
            turn(calls=[("create_music", {"mood": "calm"}), picture_call(1, "Scene 1 first.")]),
        ),
        turn(says="Paused. Nothing is running. What should change?"),
    )

    say("Make the music and scene 1's picture")

    # Neither ran: the producer read the pause's note first.
    assert [result.split(".")[0] for result in results_of("create_music")] == ["Refused: paused"]
    assert [result.split(".")[0] for result in results_of("make_starting_picture")] == [
        "Refused: paused"
    ]
    assert "create_music" not in paid_for()
    assert not SceneStep.objects.exists()
    assert "The shop owner pressed pause" in notes_given(producer_turns())[-1]


def test_steps_the_pause_cancelled_stay_cancelled_as_the_worker_starts_again(
    api: APIClient,
    fake_model: FakeModel,
    three_pictures_started: None,
    steps: HeldSteps,
    session_id: str,
) -> None:
    taken_by_the_worker(1)
    fake_model.respond("produce", turn(says="Paused. Scene 1's picture is finishing."))
    pause(api, session_id)
    turns = producer_turns()

    fake_model.respond("produce", turn(says="Scene 1's picture stopped partway."))
    tasks.carry_on_after_the_worker_starts()

    # Only the step the worker had taken is failed by the restart; the producer is told of it.
    assert [step_of(scene).status for scene in (1, 2, 3)] == ["failed", "cancelled", "cancelled"]
    assert producer_turns() == turns + 1
    told = [each["text"] for each in given_to_the_producer(producer_turns())[-1:]]
    assert "scene 1's starting picture" in told[0]
    # The chat says once that each cancelled picture wasn't made, and never that it failed.
    texts = [text for _, text in chat(api, session_id)]
    assert sum("Scene 2's starting picture" in text for text in texts) == 1
    assert sum("Scene 3's starting picture" in text for text in texts) == 1


def test_the_chat_shows_the_pause_until_the_owner_writes_and_a_second_press_does_nothing_more(
    api: APIClient,
    fake_model: FakeModel,
    three_pictures_started: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="Paused. Nothing is running."))
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False

    pause(api, session_id)
    shown = chat(api, session_id)
    turns = producer_turns()
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
        ("info", {"scene": scene, "kind": "starting_picture", "state": "not_made"})
        for scene in (1, 2, 3)
    ]

    fake_model.respond("produce", turn(says="What should change?"))
    say("Hold on, let me think")
    assert api.get(f"/api/sessions/{session_id}/").json()["paused"] is False
