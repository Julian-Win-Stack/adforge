"""Videos sent from scene cards use the same chat API as other owner files."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from chat.models import Attachment
from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import given_to_the_producer, producer_turns
from .test_producer_broll import checked  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_steering_rows import Watched, watched  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("reorder", [False, True])
def test_a_note_video_is_kept_with_its_scene_and_labelled_for_the_producer(
    fake_model: FakeModel,
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
    reorder: bool,
) -> None:
    first_scene = Job.objects.get().scenes.get(number=1)
    calls = [("reorder_scenes", {"order": [3, 2, 1]})] if reorder else []
    fake_model.respond(
        "produce",
        turn(
            calls=[
                *calls,
                (
                    "ask_owner",
                    {"question": "Should I copy its movement or its look?", "videos": [1]},
                ),
            ]
        ),
        turn(says="Which part should I use?"),
    )
    first = producer_turns() + 1

    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": "Scene 1: use this",
            "videos": [SimpleUploadedFile("movement.mp4", b"an owner video", "video/mp4")],
            "video_scenes": [1],
        },
        format="multipart",
    )

    assert sent.status_code == 201, sent.json()
    assert sent.json()["message"]["attachments"][0]["note_scene"] == 1
    attachment = Attachment.objects.get(message__role="user", kind=Attachment.Kind.VIDEO)
    assert attachment.note_for_id == first_scene.pk
    assert attachment.note_for is not None
    assert attachment.note_for.number == (3 if reorder else 1)
    assert any(
        "owner video 1, sent with the Scene 1 note" in each.get("text", "")
        for each in given_to_the_producer(first)
        if each["kind"] == "said" and each["by"] == "user"
    )


@pytest.mark.parametrize("field", ["photos", "videos"])
@pytest.mark.parametrize("oversized", [False, True])
def test_scene_note_and_chat_reject_the_same_unsupported_files(
    api: APIClient, session_id: str, field: str, oversized: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chat import api as chat_api

    file_type = "text/plain"
    name = "wrong.txt"
    if oversized:
        monkeypatch.setattr(
            chat_api, "MAX_PHOTO_BYTES" if field == "photos" else "MAX_VIDEO_BYTES", 3
        )
        file_type = "image/png" if field == "photos" else "video/mp4"
        name = "large.png" if field == "photos" else "large.mp4"

    def send(with_scene: bool) -> object:
        data: dict[str, object] = {
            "text": "Scene 2: use this",
            field: [SimpleUploadedFile(name, b"1234", file_type)],
        }
        if with_scene:
            data["photo_scenes" if field == "photos" else "video_scenes"] = [2]
        sent = api.post(f"/api/sessions/{session_id}/messages/", data, format="multipart")
        assert sent.status_code == 400
        return sent.json()

    assert send(True) == send(False)


@pytest.mark.parametrize(
    ("scenes", "text", "refused"),
    [
        ([1, 2], "Scene 1: use this", "Say which scene's note each video goes with."),
        ([5], "Scene 5: use this", "The ad has no scene 5: send the note again."),
        ([2], " ", "Say in the note what to take from its video."),
    ],
)
def test_video_scene_bindings_are_checked_before_any_file_is_kept(
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
    scenes: list[int],
    text: str,
    refused: str,
) -> None:
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": text,
            "videos": [SimpleUploadedFile("movement.mp4", b"video", "video/mp4")],
            "video_scenes": scenes,
        },
        format="multipart",
    )
    assert sent.status_code == 400
    assert refused in str(sent.json())
    assert not Attachment.objects.filter(message__role="user").exists()
