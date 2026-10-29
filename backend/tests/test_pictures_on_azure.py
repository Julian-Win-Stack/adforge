"""Pictures fall back to our Azure copy of the picture model when OpenAI refuses to draw for
money (#247 point 19a), driven through the chat: the producer's model is faked at the
gateway to make scene 1's starting picture, and the fake picture model refuses as told."""

import threading
import time
from collections.abc import Callable
from itertools import pairwise
from typing import Any

import pytest
from django.db import connection
from pytest_django import Settings
from rest_framework.test import APIClient

from chat.models import Session
from gateway import gateway
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Picture, RefusedForMoney
from jobs.models import Job, SceneStep

from .conftest import HeldSteps, ScriptedProducer, chat
from .test_producer_scenes import CHOICE, making

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

OUT_OF_MONEY = RefusedForMoney(
    "Error code: 429 - You exceeded your current quota, please check your plan and billing "
    "details. (insufficient_quota)"
)


@pytest.fixture(autouse=True)
def azure_set_up(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Azure's copy of the picture model is set up, and every wait for its pace is noted
    rather than slept."""
    settings.AZURE_PICTURES_KEY = "azure-key"
    settings.AZURE_PICTURES_BASE_URL = "https://ours.openai.azure.com/openai/v1/"
    waited: list[float] = []
    monkeypatch.setattr(gateway, "_wait", waited.append)
    return waited


def make_scene_1(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], note: str | None = None
) -> None:
    making(fake_model, (1, note))
    say("Make scene 1's starting picture" if note is None else note)
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Done."))
    steps.run_held()


def red_lines(api: APIClient, session_id: str) -> list[str]:
    return [
        message["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["level"] == "problem"
    ]


def test_pictures_openai_refuses_for_money_are_drawn_on_azure_from_then_on(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    drawn_before = len(fake_model.pictures_drawn_on)
    red_before = len(red_lines(api, session_id))
    warned_before = len(Job.objects.get().warnings)
    fake_model.respond("make_starting_picture", OUT_OF_MONEY)

    make_scene_1(fake_model, steps, say)

    # The refused picture is drawn on Azure at once, so the scene still gets its picture.
    assert SceneStep.objects.get().status == "finished"
    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "azure"]
    # The owner is told in red, and the ad's warnings keep the same line.
    (told,) = red_lines(api, session_id)[red_before:]
    assert "out of money" in told and "Azure" in told
    assert [warning["text"] for warning in Job.objects.get().warnings[warned_before:]] == [told]

    make_scene_1(fake_model, steps, say, note="Smiling more.")

    # The next picture goes straight to Azure, and the owner isn't told again.
    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "azure", "azure"]
    assert red_lines(api, session_id)[red_before:] == [told]
    assert [
        (call.provider, call.outcome)
        for call in ModelCall.objects.filter(purpose="make_starting_picture").order_by("id")
    ] == [("fake", "failed"), ("azure", "succeeded"), ("azure", "succeeded")]


def test_another_sessions_pictures_stay_on_openai(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("make_starting_picture", OUT_OF_MONEY)
    make_scene_1(fake_model, steps, say)
    other = Job.objects.create(
        session=Session.objects.create(), product_url="https://shop.example/products/mug"
    )
    drawn_before = len(fake_model.pictures_drawn_on)

    with gateway.use_model(fake_model):
        gateway.draw_picture(job=other, purpose="draw_person", prompt="A potter.")

    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai"]


def test_a_picture_the_safety_filter_refuses_isnt_drawn_on_azure(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    drawn_before = len(fake_model.pictures_drawn_on)
    fake_model.respond("make_starting_picture", BlockedBySafetyFilter("content_filter"))
    make_scene_1(fake_model, steps, say)

    make_scene_1(fake_model, steps, say, note="Smiling more.")

    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "openai"]
    assert not any("Azure" in text for _, text in chat(api, session_id))


def test_pictures_on_azure_are_paced_to_azures_limit(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Settings,
    azure_set_up: list[float],
) -> None:
    settings.AZURE_PICTURES_PER_MINUTE = 1
    fake_model.respond("make_starting_picture", OUT_OF_MONEY)
    make_scene_1(fake_model, steps, say)
    assert azure_set_up == []

    make_scene_1(fake_model, steps, say, note="Smiling more.")

    # One a minute: the second waits out what is left of the minute since the first.
    (waited,) = azure_set_up
    assert 30 < waited <= 60


def test_without_azure_set_up_a_picture_refused_for_money_fails_saying_so(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.AZURE_PICTURES_KEY = ""
    drawn_before = len(fake_model.pictures_drawn_on)
    fake_model.respond("make_starting_picture", OUT_OF_MONEY)

    make_scene_1(fake_model, steps, say)

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert "out of money" in step.reason
    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai"]
    assert any("out of money" in text for text in red_lines(api, session_id))


def test_when_azure_refuses_for_money_too_the_step_fails_saying_so(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("make_starting_picture", OUT_OF_MONEY, OUT_OF_MONEY)

    make_scene_1(fake_model, steps, say)

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert "out of money" in step.reason
    # Azure is set up: the reason doesn't say it isn't.
    assert "set up" not in step.reason


def switched_to_azure(fake_model: FakeModel) -> Job:
    """A job whose session's pictures are drawn on Azure: OpenAI refused one for money."""
    job = Job.objects.create(
        session=Session.objects.create(), product_url="https://shop.example/products/mug"
    )
    fake_model.respond("draw_person", OUT_OF_MONEY)
    with gateway.use_model(fake_model):
        gateway.draw_picture(job=job, purpose="draw_person", prompt="A potter.")
    return job


def test_azures_wait_isnt_counted_in_how_long_its_call_took(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.AZURE_PICTURES_PER_MINUTE = 120
    monkeypatch.setattr(gateway, "_wait", time.sleep)
    job = switched_to_azure(fake_model)

    with gateway.use_model(fake_model):
        gateway.draw_picture(job=job, purpose="draw_person", prompt="A potter.")

    # It waited half a second for Azure's pace, but the call itself was quick.
    paced = ModelCall.objects.filter(provider="azure").order_by("id").last()
    assert paced is not None and paced.duration_ms is not None
    assert paced.duration_ms < 300


def test_pictures_made_at_once_are_drawn_on_azure_one_at_a_time_at_its_pace(
    fake_model: FakeModel, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Many workers make pictures at the same time; Azure's limit holds for them all.
    settings.AZURE_PICTURES_PER_MINUTE = 120
    monkeypatch.setattr(gateway, "_wait", time.sleep)
    job = switched_to_azure(fake_model)
    started: list[float] = []
    drawing = fake_model.draw

    def slow_draw(**asked: Any) -> Picture:
        started.append(time.monotonic())
        time.sleep(0.2)
        return drawing(**asked)

    monkeypatch.setattr(fake_model, "draw", slow_draw)

    def draw() -> None:
        try:
            with gateway.use_model(fake_model):
                gateway.draw_picture(job=job, purpose="draw_person", prompt="A potter.")
        finally:
            connection.close()

    threads = [threading.Thread(target=draw) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(started) == 3
    # Each starts once the one before has ended and half a second more has passed.
    starts = sorted(started)
    assert all(later - earlier >= 0.65 for earlier, later in pairwise(starts))


# --- Whole-build check C (spec #247 Testing Decisions, ticket #266) --------------------------


def picture_of_scene_1(note: str | None = None) -> tuple[str, dict[str, Any]]:
    return ("make_starting_picture", {"scene": 1, "note": note})


def test_the_scripted_producers_pictures_move_to_azure_once_openai_refuses_for_money(
    api: APIClient,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
) -> None:
    drawn_before = len(fake_model.pictures_drawn_on)
    red_before = len(red_lines(api, session_id))
    fake_model.respond("make_starting_picture", OUT_OF_MONEY)
    fake_model.respond("choose_starting_picture", CHOICE, CHOICE)

    producer.told("Make scene 1's starting picture", picture_of_scene_1(), "On it.")
    producer.woken(picture_of_scene_1("Smiling more."), "Scene 1's picture is made.")
    producer.steps_run()

    # The refused picture was drawn on Azure at once, and the next one went straight there.
    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "azure", "azure"]
    assert [step.status for step in SceneStep.objects.order_by("id")] == ["finished"] * 2
    # The shop owner was told once, in red, and the ad's warnings keep the same line.
    (told,) = red_lines(api, session_id)[red_before:]
    assert "out of money" in told and "Azure" in told
    assert told in [warning["text"] for warning in Job.objects.get().warnings]


def test_the_scripted_producers_picture_refused_for_safety_stays_on_openai_as_an_answer(
    api: APIClient,
    producer: ScriptedProducer,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
) -> None:
    drawn_before = len(fake_model.pictures_drawn_on)
    fake_model.respond("make_starting_picture", BlockedBySafetyFilter("content_filter"))
    fake_model.respond("choose_starting_picture", CHOICE, CHOICE)

    producer.told("Make scene 1's starting picture", picture_of_scene_1(), "On it.")
    # The refusal reaches the producer as the step's answer, and it goes on with a new note.
    producer.woken(picture_of_scene_1("Her hands in her lap."), "Trying another picture.")
    producer.steps_run()

    refused, made = SceneStep.objects.order_by("id")
    assert refused.status == "failed" and "safety filter" in refused.result
    assert made.status == "finished"
    # Neither went to Azure, and nothing said so.
    assert fake_model.pictures_drawn_on[drawn_before:] == ["openai", "openai"]
    assert not any("Azure" in text for _, text in chat(api, session_id))
