"""Spec round-7-other-reds section 2a: the voice service's account holds only so many voices,
and in round 7 it filled, stopping every ad that wanted a presenter. When it is full, the
voices of this machine's ad idle the longest are deleted to make room and the voice is
designed again, once; voices named for another machine, or kept before voices were named,
are never deleted. Through the chat, with the voice service faked at the gateway."""

import threading
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.utils import timezone
from pytest_django import Settings
from rest_framework.test import APIClient

from agents.models import ToolCall
from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from jobs.job_locks import one_at_a_time
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.threads import closing_its_connection
from jobs.voices import VOICE_DESIGN_LOCK, designed_voice, voice_name

from .conftest import chat, results_of

pytestmark = pytest.mark.django_db(transaction=True)


def an_old_ad(fake_model: FakeModel, idle_for: timedelta, *, named: str | None = None) -> Job:
    """Another ad on this machine, with a presenter's voice kept in the voice account under
    `named` (by default its own session's name), its last message `idle_for` ago."""
    session = Session.objects.create(name="Old mug ad")
    job = Job.objects.create(session=session, product_url="https://shop.example/products/old")
    voice = ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.VOICE,
        voice_id=f"old-voice-{session.pk}",
        words_per_second=2.0,
    )
    fake_model.voice_account[voice.voice_id] = named or voice_name(session.pk)
    said = messages.add(session, role=Message.Role.USER, text="Thanks!")
    Message.objects.filter(pk=said.pk).update(created_at=timezone.now() - idle_for)
    return job


def voice_of(job: Job) -> ProducedItem:
    voice = job.produced.filter(kind=ProducedItem.Kind.VOICE).order_by("version").last()
    assert voice is not None
    return voice


def warned(job: Job) -> list[str]:
    job.refresh_from_db()
    return [each["text"] for each in job.warnings]


@pytest.fixture
def full_account(fake_model: FakeModel) -> FakeModel:
    """A voice account with room for 2 voices."""
    fake_model.voice_room = 2
    return fake_model


def make_the_person(fake_model: FakeModel, say: Callable[..., None]) -> None:
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="Here's your presenter.")
    )
    say("Make the person")


def test_a_full_voice_account_frees_the_longest_idle_ads_voice_and_the_person_is_made(
    api: APIClient,
    full_account: FakeModel,
    planned: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    idle_longest = an_old_ad(full_account, timedelta(days=3))
    idle_lately = an_old_ad(full_account, timedelta(hours=1))

    make_the_person(full_account, say)

    ours = Job.objects.get(session_id=session_id)
    assert voice_of(ours).voice_id in full_account.voice_account
    assert full_account.voice_account[voice_of(ours).voice_id] == voice_name(ours.session_id)
    assert full_account.voices_deleted == [voice_of(idle_longest).voice_id]
    assert voice_of(idle_longest).voice_deleted
    assert not voice_of(idle_lately).voice_deleted
    # Recorded on both ads, never said to either shop owner.
    assert any("voice service's account was full" in text for text in warned(ours))
    assert any("voice service's account was full" in text for text in warned(idle_longest))
    assert chat(api, session_id)[-1] == ("agent", "Here's your presenter.")
    assert not Message.objects.filter(role=Message.Role.NOTICE, text__contains="voice").exists()


def test_an_ad_with_work_running_never_loses_its_voice(
    full_account: FakeModel, planned: None, session_id: str, say: Callable[..., None]
) -> None:
    rendering = an_old_ad(full_account, timedelta(days=3))
    scene = Scene.objects.create(job=rendering, number=1, line="Hello.")
    assert rendering.session is not None
    started = ToolCall.objects.create(
        session=rendering.session,
        agent="producer",
        tool="make_clip",
        call_id="c0",
        arguments={},
        finished_at=timezone.now(),
    )
    # Its clip is still marked as being made, though its worker hasn't been heard from
    # lately (so it doesn't keep this chat waiting): a step that may yet finish keeps its voice.
    SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.CLIP,
        line="Hello.",
        tool_call=started,
        seen_at=timezone.now() - timedelta(days=1),
    )
    working = an_old_ad(full_account, timedelta(days=2))
    assert working.session is not None
    ToolCall.objects.create(
        session=working.session, agent="producer", tool="read_ad", call_id="c1", arguments={}
    )
    idle = an_old_ad(full_account, timedelta(hours=1))
    full_account.voice_room = 3

    make_the_person(full_account, say)

    assert full_account.voices_deleted == [voice_of(idle).voice_id]


def test_voices_named_for_another_machine_or_before_names_are_never_deleted(
    full_account: FakeModel,
    planned: None,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.LANGFUSE_ENVIRONMENT = "azure"
    an_old_ad(full_account, timedelta(days=3), named="AdForge presenter")
    an_old_ad(full_account, timedelta(days=2), named="AdForge development whatever")

    make_the_person(full_account, say)

    assert full_account.voices_deleted == []
    assert not Job.objects.get(session_id=session_id).produced.filter(kind="voice").exists()
    # The producer is told plainly why, not "an unexpected error".
    [why] = results_of("create_person")
    assert "voice service's account is full" in why


def test_an_ad_whose_voice_was_deleted_gets_a_new_voice_when_the_person_is_made_again(
    full_account: FakeModel, planned: None, session_id: str, say: Callable[..., None]
) -> None:
    full_account.voice_room = None
    make_the_person(full_account, say)
    ours = Job.objects.get(session_id=session_id)
    old = voice_of(ours)
    ProducedItem.objects.filter(pk=old.pk).update(voice_deleted=True)
    del full_account.voice_account[old.voice_id]

    make_the_person(full_account, say)

    new = voice_of(ours)
    assert (new.version, new.voice_deleted) == (old.version + 1, False)
    assert new.voice_id != old.voice_id and new.voice_id in full_account.voice_account
    # Measured afresh in the new voice, not read back from the old one's measuring.
    assert new.words_per_second is not None and new.file != old.file


def test_a_line_isnt_said_in_a_voice_that_was_deleted_and_the_producer_is_told_why(
    fake_model: FakeModel, planned: None, session_id: str, say: Callable[..., None]
) -> None:
    make_the_person(fake_model, say)
    ours = Job.objects.get(session_id=session_id)
    ProducedItem.objects.filter(pk=voice_of(ours).pk).update(voice_deleted=True)
    ours.scenes.filter(number=1).update(fact_checked=True)
    spoken = len(fake_model.spoken)
    fake_model.respond(
        "produce",
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(says="I'll make the person again."),
    )

    say("Make scene 1's audio")

    [why] = results_of("make_line_audio")
    assert "voice was deleted from the voice service" in why and "Create the person" in why
    assert len(fake_model.spoken) == spoken


def test_voices_are_designed_one_at_a_time_on_a_machine(
    fake_model: FakeModel, planned: None, session_id: str
) -> None:
    # Round 7: 8 ads designing voices at once drew 429s from the voice service.
    job = Job.objects.get(session_id=session_id)
    designing = threading.Thread(
        target=closing_its_connection,
        args=(lambda: designed_voice(job, description="A calm voice.", sample="Hello."),),
    )

    with one_at_a_time(VOICE_DESIGN_LOCK, "voice design"):
        designing.start()
        designing.join(timeout=1)
        # Waiting for the voice being designed elsewhere.
        assert designing.is_alive() and fake_model.voices == 0
    designing.join(timeout=10)

    assert not designing.is_alive() and fake_model.voices == 1
