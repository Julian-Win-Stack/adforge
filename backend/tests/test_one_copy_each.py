"""Each producer and scene step runs as one copy. The broker can hand a task out twice (a
step still unfinished after its visibility timeout), and a producer waiting in a full queue
is sent again (fix triage SA8, DW1, DW-bug-1): the second copy quits as it starts, so
nothing is made or paid for twice, and the developer is told."""

from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient

from agents import tasks
from chat.models import Session
from gateway.fake import FakeModel, meanwhile, turn
from jobs.models import SceneAgentRun, SceneStep

from .conftest import HeldSteps, chat, paid_for, producer_turns, recorded
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_scene_step_handed_out_again_while_it_runs_draws_and_pays_once(
    api: APIClient,
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    def handed_out_again() -> None:
        # The broker hands the same step to another worker slot while the first copy works.
        tasks.run_scene_step(SceneStep.objects.get(kind=SceneStep.Kind.SCENE_AGENT).pk)

    fake_model.respond(
        "scene_agent",
        meanwhile(handed_out_again, turn(calls=[("draw_starting_picture", PICTURE_FORM)])),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert paid_for().count("draw_scene_picture") == 1
    assert paid_for().count("scene_agent") == 2
    assert SceneAgentRun.objects.get().pictures == 1
    quit_notes = [text for _, text in recorded(api, session_id) if "second copy" in text]
    assert len(quit_notes) == 1
    assert quit_notes[0].startswith("A second copy of scene 2's scene agent quit as it started")


def test_a_producer_sent_again_while_one_works_quits_and_the_owner_gets_one_reply(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    def sent_again() -> None:
        # The first copy waited in a full queue for longer than a producer may go unseen, so
        # another copy was sent, and it starts while the first works.
        tasks.run_producer(session_id)

    fake_model.respond(
        "produce", meanwhile(sent_again, turn(says="What would you like an ad for?"))
    )

    say("Hi")

    assert producer_turns() == 1
    assert chat(api, session_id) == [("user", "Hi"), ("agent", "What would you like an ad for?")]
    session = Session.objects.get(pk=session_id)
    assert not session.producer_running
    assert session.producer_copy is None


def test_a_late_copy_of_a_producer_that_already_stopped_takes_no_turn(
    fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    say("Hi")

    # A copy sent while the first waited in the queue, started after the first stopped.
    tasks.run_producer(session_id)

    assert producer_turns() == 1
    assert not Session.objects.get(pk=session_id).producer_running


def test_a_producer_whose_working_copy_died_is_taken_over_by_the_next(
    fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    from .test_producer_restarts import the_producer_died

    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    say("Hi")
    say_more = "A mug"
    # The copy working on the next message died without letting go of the session.
    Session.objects.filter(pk=session_id).update(
        producer_copy="00000000-0000-0000-0000-0000000000a1"
    )
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Send me its page."))

    say(say_more)

    assert producer_turns() == 2


def test_several_sessions_make_ads_at_once_with_one_ad_at_a_time_off(
    api: APIClient, fake_model: FakeModel
) -> None:
    def a_session_says(text: str) -> Any:
        created = api.post("/api/sessions/", {}, format="json").json()["id"]
        return api.post(f"/api/sessions/{created}/messages/", {"text": text}, format="multipart")

    assert settings.ONE_AD_AT_A_TIME
    working = api.post("/api/sessions/", {}, format="json").json()["id"]
    Session.objects.filter(pk=working).update(producer_running=True)
    assert a_session_says("Hi").status_code == 409

    fake_model.respond("produce", turn(says="What would you like an ad for?"))
    with override_settings(ONE_AD_AT_A_TIME=False):
        assert a_session_says("Hi").status_code == 201
