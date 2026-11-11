"""Make a copy of a finished session (#566): the copy holds everything the original does,
takes the shop owner's notes as its own, and the original never changes."""

import uuid
from typing import Any

import pytest
from django.apps import apps
from django.db import models
from django.utils import timezone
from rest_framework.test import APIClient

from chat import session_copy
from chat.models import Message, Session
from evals.owner_notes.chat import Chat, Notes, send_notes
from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem, Scene, SceneStep

from .conftest import HeldSteps
from .scene_script import script_scene_agents
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import assembled  # noqa: F401 (a fixture)

pytestmark = [pytest.mark.django_db(transaction=True)]

NOTE = "Scene 2 just sits there. The hand should turn the mug all the way round."
TURN = "The hand turns the mug all the way round."


def fix_scene_2(fake_model: FakeModel, session: Session) -> None:
    """The shop owner's note on scene 2, which the fixing agent draws and films again for."""
    fake_model.respond(
        "produce",
        turn(
            calls=[("pass_note", {"scenes": [{"scene": 2, "instruction": TURN, "quotes": [NOTE]}]})]
        ),
        turn(says="I'm fixing scene 2."),
        turn(says="Scene 2 is fixed."),
    )
    script_scene_agents(fake_model)
    turned = send_notes(Chat(str(session.pk)), Notes([NOTE]))
    assert turned.error == ""


def snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Every row of the session, every field, by table."""
    return {
        model._meta.label: list(model._base_manager.filter(pk__in=pks).order_by("pk").values())
        for model, pks in session_copy.rows_of(session).items()
    }


def assert_self_contained(copy: Session, original: Session) -> None:
    """No row of the copy is the original's, or links to one, by a field or inside JSON."""
    copied = session_copy.rows_of(copy)
    theirs = session_copy.rows_of(original)
    for model, pks in copied.items():
        assert not pks & theirs.get(model, set()), model
        for row in model._base_manager.filter(pk__in=pks):
            for field in model._meta.concrete_fields:
                target = field.related_model
                if field.is_relation and target in theirs:
                    linked = getattr(row, field.attname)
                    assert linked is None or linked in copied[target], (model, field.name)
            for (owner, name), paths in session_copy.IDS_IN_JSON.items():
                if owner is model:
                    for linked, target in session_copy.ids_in(getattr(row, name), paths):
                        assert linked not in theirs[target], (model, name, linked)


def test_a_copy_takes_a_note_and_the_original_stays_as_it_was(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    assembled: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    api: APIClient,
) -> None:
    original = Session.objects.get()
    # A fix first, so the copy carries the fixing agent's history too.
    fix_scene_2(fake_model, original)
    # A scene the producer removed is kept with what was made for it, so it is copied too.
    Scene.with_removed.filter(job__session=original, number=3).update(removed_at=timezone.now())
    # A producer that worked here once leaves its marks; the copy starts with none.
    Session.objects.filter(pk=original.pk).update(
        producer_seen_at=timezone.now(), producer_copy=uuid.uuid4(), producer_beat_at=timezone.now()
    )
    before = snapshot(original)
    chat_before = api.get(f"/api/sessions/{original.pk}/").json()
    cards_before = api.get(f"/api/sessions/{original.pk}/scenes/").json()

    made = api.post(f"/api/sessions/{original.pk}/copy/")

    assert made.status_code == 201, made.content
    copy = Session.objects.get(pk=made.json()["id"])
    assert copy.name == f"Copy of {original.name}"
    assert not copy.producer_running
    assert copy.paused_at is None
    assert (copy.producer_seen_at, copy.producer_copy, copy.producer_beat_at) == (None, None, None)
    # The copy matches the original row for row, and links only to its own rows.
    assert {label: len(rows) for label, rows in snapshot(copy).items()} == {
        label: len(rows) for label, rows in before.items()
    }
    assert_self_contained(copy, original)
    shown = api.get(f"/api/sessions/{copy.pk}/").json()
    assert [(m["role"], m["text"]) for m in shown["messages"]] == [
        (m["role"], m["text"]) for m in chat_before["messages"]
    ]
    # The scene cards show the same versions, with the same files: only row ids differ.
    cards = api.get(f"/api/sessions/{copy.pk}/scenes/").json()
    assert _without_ids(cards) == _without_ids(cards_before)
    assert any(version["url"] for card in cards for version in card["versions"])

    clips_before = ProducedItem.objects.filter(
        job__session=original, kind="clip", scene__number=2
    ).count()
    fix_scene_2(fake_model, copy)

    assert (
        ProducedItem.objects.filter(job__session=copy, kind="clip", scene__number=2).count()
        > clips_before
    )
    assert snapshot(original) == before
    assert api.get(f"/api/sessions/{original.pk}/").json() == chat_before
    assert_self_contained(copy, original)
    # A copy can be copied again.
    assert api.post(f"/api/sessions/{copy.pk}/copy/").status_code == 201


def _without_ids(found: Any) -> Any:
    """An API answer with the row ids it shows taken out."""
    if isinstance(found, dict):
        return {
            key: _without_ids(value)
            for key, value in found.items()
            if key not in ("clip", "scene_run")
        }
    if isinstance(found, list):
        return [_without_ids(value) for value in found]
    return found


@pytest.mark.parametrize("running", ["producer", "scene step"])
def test_a_session_still_working_is_not_copied(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    assembled: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    api: APIClient,
    running: str,
) -> None:
    original = Session.objects.get()
    if running == "producer":
        Session.objects.update(producer_running=True)
    else:
        SceneStep.objects.filter(pk=SceneStep.objects.latest("pk").pk).update(
            status=SceneStep.Status.RUNNING
        )
    messages_before = Message.objects.count()

    refused = api.post(f"/api/sessions/{original.pk}/copy/")

    assert refused.status_code == 409
    assert "still" in refused.json()["detail"]
    assert Session.objects.count() == 1
    assert Message.objects.count() == messages_before
    assert [w["kind"] for w in original.jobs.get().warnings][-1] == "copy_refused"


def test_every_table_that_belongs_to_a_session_is_copied() -> None:
    """A table added later that links to a session, a job or a scene, directly or through
    another table, fails here until the copy knows it: else a copy would quietly lose it."""
    belongs: set[type[models.Model]] = {Session}
    grew = True
    while grew:
        grew = False
        for model in apps.get_models(include_auto_created=True):
            if model not in belongs and any(
                field.is_relation and field.concrete and field.related_model in belongs
                for field in model._meta.get_fields()
            ):
                belongs.add(model)
                grew = True

    assert set(session_copy.MODELS) == belongs


def test_every_json_field_of_a_copied_table_is_known_to_hold_row_ids_or_not() -> None:
    """A JSON field added later fails here until someone says whether it holds row ids:
    else the copy's would point back at the original's rows."""
    json_fields = {
        (model, field.name)
        for model in session_copy.MODELS
        for field in model._meta.concrete_fields
        if isinstance(field, models.JSONField)
    }

    assert json_fields == set(session_copy.IDS_IN_JSON) | session_copy.NO_IDS_IN_JSON
    assert not set(session_copy.IDS_IN_JSON) & session_copy.NO_IDS_IN_JSON
