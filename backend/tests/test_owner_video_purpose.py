"""Owner video purposes reach the watcher through chat attachments (#554)."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_django import Settings
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, VideoRequest
from jobs.library import LookFrame, library_of, watch_of
from jobs.models import Job

from .conftest import handoffs
from .test_product_videos import tiny_mp4  # noqa: F401
from .test_video_watcher import long_mp4, look_frame, moment, watched  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


def send_video(api: APIClient, session_id: str, words: str, video: bytes) -> None:
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": words,
            "videos": [SimpleUploadedFile("movement.mp4", video, content_type="video/mp4")],
        },
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()


def test_an_owner_video_with_words_keeps_its_hand_only_movement_and_look_frame(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    words = "Use this hand movement to open the product, exactly as I do here."
    fake_model.respond(
        "watch_video",
        watched(
            shows_this_product=False,
            moments=[moment(0, 2, "An empty hand twists and lifts.")],
            look_frames=[look_frame(1, "The hand's open grip.", colour="none")],
            colour="none",
        ),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="I kept the movement from your video."),
    )

    send_video(api, session_id, words, tiny_mp4)

    assert handoffs("watch_video")[-1]["owner_note"] == words
    job = Job.objects.get()
    (video,) = job.videos.all()
    assert video.thrown_out == ""
    kept = watch_of(video)
    assert kept is not None
    assert [(m.start, m.end, m.happens) for m in kept.moments] == [
        (0, 2, "An empty hand twists and lifts.")
    ]
    assert [item.source for item in library_of(job) if isinstance(item.source, LookFrame)] == [
        LookFrame(video=video.position, second=1)
    ]


def test_an_owner_video_without_words_is_still_left_out_when_it_shows_no_product(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
) -> None:
    fake_model.respond(
        "watch_video",
        watched(shows_this_product=False, moments=[moment(0, 2, "An empty hand twists.")]),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="That video doesn't show your product."),
    )

    send_video(api, session_id, "", tiny_mp4)

    assert handoffs("watch_video")[-1]["owner_note"] == ""
    (video,) = Job.objects.get().videos.all()
    assert watch_of(video) is None
    assert video.thrown_out == "The watcher said it doesn't show the product the page sells."


def test_an_owner_video_over_the_watch_limit_tells_the_owner_to_send_a_shorter_one(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
    settings: Settings,
) -> None:
    settings.WATCH_VIDEO_MOST_SECONDS = 1
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="I can't watch that much."),
    )

    send_video(api, session_id, "Copy this hand movement.", tiny_mp4)

    assert handoffs("watch_video") == []
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    notices = [message["text"] for message in shown if message["role"] == "notice"]
    assert any("2 s long" in text and "shorter" in text for text in notices), notices


def test_an_owner_purpose_keeps_hand_only_parts_after_the_safety_filter_splits_the_video(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    long_mp4: bytes,  # noqa: F811
) -> None:
    words = "Copy the full twisting movement, including the empty hand at the end."

    def answer(request: VideoRequest[Any]) -> dict[str, Any]:
        given = request.handoff.model_dump()
        assert given["owner_note"] == words
        part = given["part"]
        if part is None:
            raise BlockedBySafetyFilter("The whole video is refused.")
        first = part["start"] == 0
        return watched(
            shows_this_product=first,
            moments=[
                moment(0, given["seconds"], "Twists the product." if first else "Empty hand.")
            ],
            colour="green" if first else "none",
        )

    fake_model.answer_unscripted("watch_video", answer)
    fake_model.answer_unscripted("probe_video", lambda _: {"word": "hand"})
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="I kept both parts of the movement."),
    )

    send_video(api, session_id, words, long_mp4)

    (video,) = Job.objects.get().videos.all()
    kept = watch_of(video)
    assert kept is not None
    assert [(m.start, m.end, m.happens) for m in kept.moments] == [
        (0, 10, "Twists the product."),
        (10, 24, "Empty hand."),
    ]
