"""An owner question stays open until the shop owner answers it (round 7 unlisted, item 4):
the message that ends their pause is often only a word to go on, so the producer is told the
question it asked before is still open, and to ask it again unless the message answers it.
Driven through the chat, with the producer's model faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents import loop
from chat.models import Session
from gateway.fake import FakeModel, turn

from .conftest import given_to_the_producer, producer_turns
from .test_pause import pause

pytestmark = pytest.mark.django_db(transaction=True)

QUESTION = "Only one product photo was found. Shall I go on with it, or will you send more?"


def notes_given(number: int) -> list[str]:
    """The app's notes the producer was given on its `number`th turn."""
    return [
        each["text"]
        for each in given_to_the_producer(number)
        if each["kind"] == "step_finished" and each["text"].startswith("From the app")
    ]


@pytest.fixture
def asked(fake_model: FakeModel, checked: None, say: Callable[..., None]) -> None:
    """The producer asked the shop owner QUESTION and waits on their answer."""
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))
    say("Go on")


def resumed(
    api: APIClient, fake_model: FakeModel, session_id: str, text: str, *replies: Any
) -> None:
    """The shop owner pauses, then writes `text`, which ends the pause."""
    fake_model.respond("produce", turn(says="Paused."), *replies)
    pause(api, session_id)
    sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
    assert sent.status_code == 201, sent.json()


def test_a_message_ending_the_pause_leaves_the_question_asked_before_it_open(
    api: APIClient, fake_model: FakeModel, asked: None, session_id: str
) -> None:
    resumed(
        api,
        fake_model,
        session_id,
        "Please carry on.",
        turn(calls=[("ask_owner", {"question": QUESTION})]),
    )

    told = notes_given(producer_turns())[-1]
    assert QUESTION in told
    assert "ask it again" in told


def test_a_question_answered_before_the_pause_isnt_said_to_be_open(
    api: APIClient, fake_model: FakeModel, asked: None, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="Going on with the one photo."))
    say("Yes, go on with it.")

    resumed(api, fake_model, session_id, "Please carry on.", turn(says="Carrying on."))

    assert not any(QUESTION in note for note in notes_given(producer_turns()))


def test_a_message_after_the_producer_stopped_leaves_the_question_asked_before_it_open(
    api: APIClient, fake_model: FakeModel, asked: None, session_id: str
) -> None:
    loop.say_it_stopped(
        Session.objects.get(pk=session_id), "I stopped.", "The producer stopped, as a test."
    )
    fake_model.respond("produce", turn(calls=[("ask_owner", {"question": QUESTION})]))

    sent = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "Please continue."}, format="json"
    )

    assert sent.status_code == 201, sent.json()
    told = notes_given(producer_turns())[-1]
    assert "after the stop" in told and QUESTION in told


def test_an_answer_with_no_pause_or_stop_before_it_gets_no_note(
    api: APIClient, fake_model: FakeModel, asked: None, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="Going on with the one photo."))

    say("Yes, go on with it.")

    assert notes_given(producer_turns()) == []
