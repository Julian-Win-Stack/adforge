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

from .conftest import handoffs, results_of
from .test_product_videos import tiny_mp4  # noqa: F401
from .test_video_watcher import long_mp4, look_frame, moment, watched  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


def send_video(
    api: APIClient, session_id: str, words: str, video: bytes, *, scene: int | None = None
) -> None:
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": words,
            "videos": [SimpleUploadedFile("movement.mp4", video, content_type="video/mp4")],
            **({"video_scenes": [scene]} if scene is not None else {}),
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
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    notices = [message["text"] for message in shown if message["role"] == "notice"]
    expected = (
        "Your first video doesn't show this product, and you haven't said what to use "
        "from it, so it was left out. Send another video with a note saying what to use, "
        "or carry on without it."
    )
    assert notices == [expected]
    assert f"owner notice: {expected}" in results_of("add_to_library")[-1]


@pytest.mark.parametrize("scene", [None, 2])
def test_an_owner_video_over_the_watch_limit_tells_the_owner_to_send_a_shorter_one(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
    settings: Settings,
    scene: int | None,
) -> None:
    settings.WATCH_VIDEO_MOST_SECONDS = 1
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="I can't watch that much."),
    )

    send_video(api, session_id, "Copy this hand movement.", tiny_mp4, scene=scene)

    assert handoffs("watch_video") == []
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    notices = [message["text"] for message in shown if message["role"] == "notice"]
    name = "The video you sent with your scene 2 note" if scene else "Your first video"
    expected = (
        f"{name} is 2 s long, over the 1 s the app watches, so it wasn't watched. "
        "Send a shorter video or carry on without it."
    )
    assert notices == [expected]
    assert results_of("add_to_library")[-1] == (
        "Failed: nothing was added to the library.\n"
        f"Owner video 1: owner notice: {expected}\n"
        "Owner video 1: their video is left out of the library: "
        "It is 2 s long, over the 1 s the app watches."
    )


def test_an_owner_purpose_keeps_hand_only_parts_after_the_safety_filter_splits_the_video(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    long_mp4: bytes,  # noqa: F811
    sentry_events: list[dict[str, Any]],
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
    assert ["notice", "owner_video_failure"] in [event["fingerprint"] for event in sentry_events]
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert any(
        message["role"] == "notice" and "safety filter" in message["text"] for message in shown
    )


@pytest.mark.parametrize("failure", ["watch", "safety"])
def test_an_unusable_owner_video_posts_one_recognizable_notice_even_when_added_again(
    fake_model: FakeModel,
    planned: None,
    api: APIClient,
    session_id: str,
    tiny_mp4: bytes,  # noqa: F811
    failure: str,
    sentry_events: list[dict[str, Any]],
) -> None:
    def fail(_: VideoRequest[Any]) -> dict[str, Any]:
        if failure == "safety":
            raise BlockedBySafetyFilter("Refused.")
        raise RuntimeError("Watch service unavailable.")

    fake_model.answer_unscripted("watch_video", fail)
    fake_model.answer_unscripted("probe_video", fail)
    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(calls=[("add_to_library", {"photos": [], "videos": [1]})]),
        turn(says="Send another video when you can."),
    )

    send_video(api, session_id, "Copy this hand movement.", tiny_mp4, scene=2)

    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    notices = [message["text"] for message in shown if message["role"] == "notice"]
    why = (
        "couldn't be watched, so nothing from it was kept."
        if failure == "watch"
        else "was refused by the safety filter, so nothing from it was kept."
    )
    expected = (
        f"The video you sent with your scene 2 note {why} "
        "Send another video or carry on without it."
    )
    assert notices == [expected]
    assert f"owner notice: {expected}" in results_of("add_to_library")[0]
    assert ["notice", "owner_video_failure"] in [event["fingerprint"] for event in sentry_events]
