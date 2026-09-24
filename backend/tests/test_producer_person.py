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
pytestmark = pytest.mark.django_db(transaction=True)


def test_the_person_is_drawn_then_given_a_voice_that_is_measured_on_the_script(
    fake_model: FakeModel, planned: None, say: Callable[..., None]
) -> None:
    fake_model.words_per_second = 2.5
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="Meet your presenter!")
    )

    say("Make the person")

    assert paid_for() == ["check_page", "plan_ad", "draw_person", "design_voice", "measure_voice"]
    (drawn,) = handoffs("draw_person")
    assert "A potter in her thirties in a linen apron, in a sunny workshop." in drawn["prompt"]
    (designed,) = handoffs("design_voice")
    assert designed["description"] == (
        "A warm, relaxed woman in her thirties with a soft British accent."
    )
    # The voice reads the whole script, 18 words, so its speed is measured on it.
    assert handoffs("measure_voice") == [
        {
            "voice_id": "fake-voice-1",
            "text": "Meet the Stoneware Mug from Kiln & Co. Hand-thrown, holds 350 ml, and "
            "dishwasher safe. Yours for $24.00.",
        }
    ]
    job = Job.objects.get()
    portrait, voice = job.produced.get(kind="portrait"), job.produced.get(kind="voice")
    assert (portrait.version, portrait.scene) == (1, None)
    assert (voice.version, voice.voice_id) == (1, "fake-voice-1")
    assert voice.words_per_second == pytest.approx(2.5)
    assert results_of("create_person") == [
        "Made the person, and showed the shop owner their portrait and their voice reading "
        "the script in the chat. The voice speaks 2.5 words a second, measured on the script."
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
