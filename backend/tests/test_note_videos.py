"""A video sent with a scene's note cannot quietly miss that scene (#557)."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_django import Settings
from rest_framework.test import APIClient

from agents.models import SystemNote
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, Turn, TurnRequest
from jobs.library import watch_of
from jobs.models import Job

from .conftest import chat, handoffs, results_of
from .test_product_videos import tiny_mp4  # noqa: F401
from .test_video_watcher import moment, watched

pytestmark = [pytest.mark.django_db(transaction=True)]


def send_note_video(
    api: APIClient, session_id: str, words: str, video: bytes, scene: int = 2
) -> dict[str, Any]:
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": words,
            "videos": [SimpleUploadedFile("movement.mp4", video, content_type="video/mp4")],
            "video_scenes": [scene],
        },
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()
    answer: dict[str, Any] = sent.json()
    return answer


def reminders() -> list[str]:
    return list(
        SystemNote.objects.filter(kind=SystemNote.Kind.NOTE_PHOTO).values_list("text", flat=True)
    )


def test_a_note_video_left_out_is_reminded_twice_and_the_owner_is_told(
    fake_model: FakeModel, planned: None, api: APIClient, session_id: str
) -> None:
    fake_model.respond(
        "produce", turn(says="Noted."), turn(says="Noted again."), turn(says="Noted, really.")
    )

    send_note_video(api, session_id, "Scene 2: copy this movement.", b"an owner video")

    assert len(reminders()) == 2
    assert all("Owner video 1" in reminder for reminder in reminders())
    assert (
        "notice",
        "The video you sent with your Scene 2 note wasn't used in scene 2. Send it again "
        "with your note, or tell me what to do with it.",
    ) in chat(api, session_id)
    (attachment,) = Attachment.objects.filter(message__role="user", kind="video")
    assert attachment.settled_at is not None


def test_a_video_on_the_wrong_scene_does_not_settle_either_scenes_own_video(
    fake_model: FakeModel,
    checked: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    fake_model.answer_unscripted(
        "watch_video",
        lambda _: watched(
            shows_this_product=False,
            moments=[moment(0, 2, "An empty hand twists.")],
            colour="none",
        ),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1, 2]})]),
        turn(calls=[("set_references", {"scene": 2, "piece": {"video": 1, "moment": 1}})]),
        turn(says="I used the movement."),
        turn(
            calls=[
                (
                    "ask_owner",
                    {
                        "question": "Should each scene copy its hand movement or its camera move?",
                        "videos": [1, 2],
                    },
                )
            ]
        ),
    )

    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": "Scene 1: use this.\nScene 2: use this too.",
            "videos": [
                SimpleUploadedFile(f"movement-{number}.mp4", tiny_mp4, content_type="video/mp4")
                for number in (1, 2)
            ],
            "video_scenes": [1, 2],
        },
        format="multipart",
    )

    assert sent.status_code == 201, sent.json()
    assert "Refused:" not in results_of("set_references")[-1]
    (reminder,) = reminders()
    assert "Owner video 1" in reminder and "Owner video 2" in reminder
    assert "scene 1's video moment" in reminder and "scene 2's video moment" in reminder


@pytest.mark.parametrize("why", ["too-long", "refused"])
def test_a_watcher_rejected_note_video_is_settled_after_the_owner_is_told(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
    settings: Settings,
    why: str,
) -> None:
    if why == "too-long":
        settings.WATCH_VIDEO_MOST_SECONDS = 1
    else:

        def refused(_: object) -> dict[str, Any]:
            raise BlockedBySafetyFilter("The video was refused.")

        fake_model.answer_unscripted("watch_video", refused)
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="Please send a different video."),
    )

    send_note_video(api, session_id, "Scene 2: copy this hand movement.", tiny_mp4)

    (video,) = Job.objects.get().videos.all()
    assert video.thrown_out
    assert any(role == "notice" and "video" in text for role, text in chat(api, session_id))
    assert reminders() == []
    (attachment,) = Attachment.objects.filter(message__role="user", kind="video")
    assert attachment.settled_at is not None


@pytest.mark.parametrize("removed", [False, True], ids=["reordered", "removed"])
def test_a_note_video_follows_its_scene_through_plan_changes(
    fake_model: FakeModel,
    checked: None,
    api: APIClient,
    session_id: str,
    removed: bool,
) -> None:
    scene = 2 if removed else 1
    change = (
        ("remove_scene", {"scene": scene})
        if removed
        else (
            "reorder_scenes",
            {"order": [3, 2, 1]},
        )
    )
    fake_model.respond("produce", turn(calls=[change]), turn(says="I changed the plan."))
    if not removed:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "ask_owner",
                        {
                            "question": "Should the scene copy the hand or camera movement?",
                            "videos": [1],
                        },
                    )
                ]
            ),
        )

    send_note_video(api, session_id, f"Scene {scene}: use this.", b"owner video", scene)

    (attachment,) = Attachment.objects.filter(message__role="user", kind="video")
    assert attachment.note_scene == scene
    if removed:
        assert reminders() == []
        assert attachment.settled_at is not None
    else:
        (reminder,) = reminders()
        assert "scene 3's video moment yet (scene 1 is scene 3 now)" in reminder
        assert attachment.note_for is not None and attachment.note_for.number == 3


@pytest.mark.parametrize("scene", [1, 2], ids=["talking", "broll"])
def test_a_note_video_is_settled_when_its_scene_has_its_watched_moment(
    fake_model: FakeModel,
    checked: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
    scene: int,
) -> None:
    words = f"Scene {scene}: copy this twisting movement."
    fake_model.respond(
        "watch_video",
        watched(
            shows_this_product=False,
            moments=[moment(0, 2, "An empty hand twists and lifts.")],
            colour="none",
        ),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(calls=[("set_references", {"scene": scene, "piece": {"video": 1, "moment": 1}})]),
        turn(says="Your scene has the video's movement."),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="I passed on the movement."))

    send_note_video(api, session_id, words, tiny_mp4, scene)

    assert "Refused:" not in results_of("set_references")[-1]
    assert handoffs("watch_video")[-1]["owner_note"] == words
    assert handoffs("watch_video")[-1]["owner_scene"] == scene
    (video,) = Job.objects.get().videos.all()
    assert watch_of(video) is not None
    assert not video.thrown_out
    assert reminders() == []
    (attachment,) = Attachment.objects.filter(message__role="user", kind="video")
    assert attachment.note_scene == scene
    assert attachment.settled_at is not None


def test_a_question_naming_a_vague_note_video_settles_it_after_the_owner_answers(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
) -> None:
    question = "Should I copy the hand movement or the camera movement?"

    def ask(request: TurnRequest) -> Turn:
        # The real model must be offered the video-number argument it needs to name it.
        offered = next(tool for tool in request.tools if tool.name == "ask_owner")
        assert "videos" in offered.arguments.model_fields
        return turn(calls=[("ask_owner", {"question": question, "videos": [1]})])

    fake_model.answer_unscripted("produce", ask)
    send_note_video(api, session_id, "Scene 2: use this.", b"an owner video")
    assert ("agent", question) in chat(api, session_id)
    fake_model.respond("produce", turn(says="I'll copy the hand movement."))
    sent = api.post(
        f"/api/sessions/{session_id}/messages/", {"text": "The hand movement."}, format="json"
    )
    assert sent.status_code == 201, sent.json()

    assert reminders() == []
    (attachment,) = Attachment.objects.filter(message__role="user", kind="video")
    assert attachment.settled_at is not None
