"""The sole B-roll note interface remains the scene's persistent chat."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel
from jobs.models import OwnerQuestion

from .conftest import HeldSteps
from .test_producer_broll import checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_producer_offers_one_broll_note_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from gateway.fake import turn

    fake_model.respond("produce", turn(says="Ready for your note."))
    say("Which scene can I change?")
    producer = next(
        request for request in reversed(fake_model.turns_taken) if request.purpose == "produce"
    )
    names = [tool.name for tool in producer.tools]
    assert "pass_note" in names
    assert "fix_scene" not in names
    assert "fixing agent" not in producer.instructions.lower()


def historical_question(session_id: str) -> OwnerQuestion:
    """Stored data from an old worker, without invoking the retired fixing loop."""
    from django.utils import timezone

    from agents.models import ToolCall
    from chat.models import Session
    from jobs.models import AgentFix, AgentFixTry, OwnerQuestion, Scene, SceneStep

    scene = Scene.objects.get(number=2)
    call = ToolCall.objects.create(
        session=Session.objects.get(pk=session_id),
        job=scene.job,
        agent="producer",
        tool="make_clip",
        call_id="historical-scene",
        arguments={"scene": 2},
        finished_at=timezone.now(),
    )
    step = SceneStep.objects.create(
        scene=scene,
        tool_call=call,
        kind="clip",
        status="finished",
        finished_at=timezone.now(),
        **SceneStep.snapshot(scene),
    )
    fixing = AgentFix.objects.create(
        step=step, door="judge", gave_up="Need a missing view.", finished_at=timezone.now()
    )
    asked = ToolCall.objects.create(
        session=call.session,
        job=scene.job,
        agent="fixing_agent",
        agent_fix=fixing,
        tool="ask_owner",
        call_id="historical-question",
        arguments={},
        finished_at=timezone.now(),
    )
    AgentFixTry.objects.create(agent_fix=fixing, number=1, tool_call=asked, scene_row={})
    return OwnerQuestion.objects.create(
        scene=scene,
        agent_fix=fixing,
        tool_call=asked,
        question="Can you send the missing view?",
        wants="photo",
    )


@pytest.mark.parametrize("video_answer", [False, True])
def test_old_question_answer_reaches_a_new_scene_chat_without_reopening_the_old_loop(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
    video_answer: bool,
    api: APIClient,
) -> None:
    from agents import tasks
    from gateway.fake import turn
    from jobs.models import SceneStep

    from .conftest import handoffs, paid_for, picture
    from .sent_lists import piece

    historical_question(session_id)
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
                                "instruction": "Use the owner's missing view.",
                                "quotes": ["Here is the missing view."],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    if video_answer:
        from django.core.files.uploadedfile import SimpleUploadedFile

        sent = api.post(
            f"/api/sessions/{session_id}/messages/",
            {
                "text": "Here is the missing view.",
                "videos": [SimpleUploadedFile("view.mp4", piece(3.0), "video/mp4")],
            },
            format="multipart",
        )
        assert sent.status_code == 201, sent.json()
    else:
        say("Here is the missing view.", ("view.png", picture(40, 30, (30, 90, 30))))
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    fake_model.respond("produce", turn(says="The new scene run ended."))
    steps.run_held()
    given = str(handoffs("scene_agent")[-1])
    assert "Here is the missing view." in given
    assert "Use the owner's missing view." in given
    assert ("Their video" if video_answer else "Their photo is library item") in given
    assert not handoffs("fix_scene")
    assert len(handoffs("scene_agent")) == 1
    before = paid_for()
    tasks.run_scene_step(SceneStep.objects.get(kind="scene_agent").pk)
    assert paid_for() == before


def test_old_question_media_waits_through_pause_and_reaches_the_next_live_run(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    from agents import pause
    from chat.models import Session
    from gateway.fake import turn

    from .conftest import handoffs, paid_for, picture

    historical_question(session_id)
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
                                "instruction": "Use the missing view.",
                                "quotes": ["Here is the missing view."],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    say("Here is the missing view.", ("view.png", picture(40, 30, (20, 80, 30))))
    pause.pause(Session.objects.get(pk=session_id), wake=False)
    fake_model.respond("produce", turn(says="Paused."))
    steps.run_held()
    assert "make_reference_photo_picture" not in paid_for()
    assert not handoffs("scene_agent")

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
                                "instruction": "Carry on with the owner's view.",
                                "quotes": ["Go on"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Continuing."),
    )
    say("Go on")
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need another view."})]))
    fake_model.respond("produce", turn(says="The scene has your view."))
    steps.run_held()
    assert "Their photo is library item" in str(handoffs("scene_agent")[-1])
    assert "Here is the missing view." in str(handoffs("scene_agent")[-1])
    assert paid_for().count("make_reference_photo_picture") == 1
