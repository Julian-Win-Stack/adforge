"""Late owner notes keep completed clips and report why the next run must wait."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models.signals import post_save
from rest_framework.test import APIClient

from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from jobs.models import Scene, SceneStep

from .conftest import HeldSteps, handoffs, recorded
from .test_pause import pause
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_pending_note_on_pause_keeps_the_finished_clip_and_tells_the_producer(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    sent = False

    def note_after_pass(sender: type[ToolCall], instance: ToolCall, **kwargs: Any) -> None:
        nonlocal sent
        if sent or instance.tool != "film" or instance.finished_at is None:
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
                                    "instruction": "Keep the blue handle visible.",
                                    "quotes": ["The handle is blue"],
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed on the handle note."),
        )
        say("The handle is blue")
        assert pause(api, session_id) == {"paused": True}

    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show the mug.",
                "quotes": ["Go on"],
            },
        ),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    post_save.connect(note_after_pass, sender=ToolCall)
    fake_model.answer_unscripted("produce", lambda _: turn(says="Paused; your clip is kept."))
    try:
        steps.run_held()
    finally:
        post_save.disconnect(note_after_pass, sender=ToolCall)

    assert sent
    step = SceneStep.objects.get(kind="scene_agent")
    assert step.status == SceneStep.Status.FINISHED
    scene = Scene.objects.get(number=2)
    assert scene.status == Scene.Status.FINISHED
    assert step.agent_fix.passed_id is not None
    card = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert len(card["versions"]) == 1
    assert card["versions"][0]["picture"] == card["picture"]
    signals = [
        message["step"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["step"] is not None
    ]
    assert signals[-1] == {"scene": 2, "kind": "clip", "state": "made"}
    given = str(handoffs("produce")[-1]).lower()
    assert "scene 2" in given and "paused" in given
    assert "blue handle" in given
    assert "pass_note" in given
    assert not any(
        "tool failed" in text.lower() or "unexpected" in text.lower()
        for _, text in recorded(api, session_id)
    )


def test_a_part_picture_waits_for_yes_without_rolling_back_other_notes_or_drawing_twice(
    fake_model: FakeModel,
    page_read: str,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from copy import deepcopy

    from adforge.retry import OutsideServiceDown

    from .conftest import NO_CHOICES, paid_for, results_of
    from .test_owner_part_library import FACTS_OK, PART_PLAN

    drawing = fake_model.edit

    def unavailable(**asked: Any) -> Any:
        raise OutsideServiceDown("the picture service answered 503")

    # The plan is kept while its outside picture service is unavailable. When it
    # recovers, pass_note must draw the missing part and wait for the owner's yes.
    fake_model.edit = unavailable  # type: ignore[method-assign]
    plan = deepcopy(PART_PLAN)
    plan["plan"]["scenes"][2]["line"] += " At home, every day."
    fake_model.respond("plan_ad", plan)
    calling(fake_model, say, ("plan_ad", {}))
    fake_model.edit = drawing  # type: ignore[method-assign]
    fake_model.respond("fact_check", FACTS_OK)
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    before = paid_for().count("make_part_picture")

    def notes(other: int) -> tuple[str, dict[str, Any]]:
        return "pass_note", {
            "scenes": [
                {"scene": 2, "instruction": "Show its lid.", "quotes": ["Go on"]},
                {"scene": other, "instruction": "Show the mug.", "quotes": ["Go on"]},
            ]
        }

    calling(fake_model, say, notes(1))
    answer = results_of("pass_note")[-1]
    assert "scene 2" in answer.lower() and "until they say yes" in answer
    assert "Scene 1's note opens a new run" in answer
    assert len(steps.held) == 1
    assert paid_for().count("make_part_picture") == before + 1

    calling(fake_model, say, notes(4))
    answer = results_of("pass_note")[-1]
    assert "scene 2" in answer.lower() and "says yes" in answer
    assert "Scene 4's note opens a new run" in answer
    assert len(steps.held) == 2
    assert paid_for().count("make_part_picture") == before + 1
    assert not any(
        "tool failed" in text.lower() or "unexpected" in text.lower()
        for _, text in recorded(api, session_id)
    )
