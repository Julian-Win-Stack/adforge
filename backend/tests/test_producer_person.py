"""Creating the person who presents the ad, driven through the chat. The producer's model is
faked at the gateway to call create_person on a planned ad, and each test checks what the
tool handed back, what was stored and what was paid for."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import handoffs, paid_for, results_of

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]


def test_the_person_is_drawn_and_its_voice_is_left_for_the_scene_clips(
    fake_model: FakeModel, planned: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="Meet your presenter!")
    )

    say("Make the person")

    assert paid_for()[-1] == "draw_person"
    assert not {"design_voice", "measure_voice", "speak_line"} & set(paid_for())
    (drawn,) = handoffs("draw_person")
    assert "The person: a woman." in drawn["prompt"]
    job = Job.objects.get()
    portrait = job.produced.get(kind="portrait")
    assert (portrait.version, portrait.scene) == (1, None)
    assert not job.produced.filter(kind="voice").exists()
    assert results_of("create_person") == [
        "The person is made and their portrait is shown. Their voice will be heard in the clips."
    ]


def test_a_person_that_couldnt_be_made_is_handed_back_as_a_failure_and_nothing_is_kept(
    api: APIClient,
    fake_model: FakeModel,
    planned: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="I couldn't make the person.")
    )
    fake_model.respond("draw_person", ConnectionError("picture service down"))

    say("Make the person")

    assert results_of("create_person") == [
        "Failed: an unexpected error stopped the tool. The details are in the server log."
    ]
    assert not Job.objects.get().produced.exists()
    assert "design_voice" not in paid_for()
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert not [message for message in messages if message["attachments"]]
