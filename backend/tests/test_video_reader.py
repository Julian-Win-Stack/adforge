"""The reader of a product video: once per gallery video with a file, as the page is read and
before the ad is planned, the planner's model is given the planner's own instructions with
one job added, the page, the product photos, the video's transcript and its picked frames in
labelled grids, and names the video moments it would need as pictures. Driven through the
chat, with the picker's picked players set by the test."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents import producer
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelRequest, UnusableReply
from jobs import product_videos
from jobs.models import Job, ProductVideo
from jobs.planning import PLAN_INSTRUCTIONS
from jobs.work import keep_photo

from .conftest import PLAN, READABLE, FakeFirecrawl, paid_for, picture, results_of
from .test_producer_page import notices
from .test_product_videos import picks_videos, player, tiny_mp4  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

ADDED_JOB = (
    "You are not planning the ad here and you ask the shop owner nothing. Think through the "
    "plan you would make, then name only the video moments you would need as pictures, and why."
)
SAID = "Tap a dot on each cheek and blend it out with your fingertips."
KEPT = {
    "kept": [
        {
            "second": 0.75,
            "shows": "The applicator placing a dot on a cheek.",
            "why": "A scene shows how little is used.",
        }
    ],
    "rejected": [{"second": 0.25, "why": "Only the closed bottle, as the photos show."}],
}


@pytest.fixture
def thinned(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each kept video file comes with its picked frames and its transcript: its stored
    frames stand in for the picked ones (the thinning is tested in test_product_videos), and
    every file says SAID (the transcript is tested in test_video_transcripts)."""
    saving = product_videos.save_videos

    def saved(job: Job, players: Any) -> list[ProductVideo]:
        videos = saving(job, players)
        for video in videos:
            if video.file:
                video.frames_picked = [
                    {"key": key, "second": second}
                    for key, second in zip(
                        video.frames, product_videos.frame_times(video.duration or 0), strict=True
                    )
                ]
                video.save(update_fields=["frames_picked"])
        return videos

    def heard(job: Job) -> list[str]:
        job.videos.exclude(file="").update(transcript=SAID)
        return []

    monkeypatch.setattr(producer, "save_videos", saved)
    monkeypatch.setattr(producer, "transcribe_videos", heard)
    yield


@pytest.fixture
def gallery_video(
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    picks_videos: list[dict[str, Any]],  # noqa: F811
    tiny_mp4: bytes,  # noqa: F811
    thinned: None,
) -> None:
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/gallery.mp4"))


def reader_answers(fake_model: FakeModel, *answers: dict[str, Any]) -> list[ModelRequest[Any]]:
    """Script the reader's answers, and hand back each request it is sent."""
    asked: list[ModelRequest[Any]] = []
    left = list(answers)

    def answer(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request)
        return left.pop(0)

    fake_model.answer_unscripted("read_video", answer)
    return asked


def read_and_plan(
    fake_model: FakeModel, say: Callable[..., None], link: str, *plans: dict[str, Any]
) -> None:
    """The shop owner sends the link; the page has one photo, so they are asked whether to go
    ahead with it, and say yes; the producer plans."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="The page has one photo. Go ahead with it?"),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", *plans)
    say(f"Make an ad for {link}")
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Done."))
    say("Go ahead with it.")


def test_a_gallery_video_is_read_once_before_planning_with_the_planners_own_instructions(
    fake_model: FakeModel,
    shampoo_page_url: str,
    say: Callable[..., None],
    gallery_video: None,
) -> None:
    asked = reader_answers(fake_model, KEPT)

    read_and_plan(fake_model, say, shampoo_page_url, PLAN)

    assert paid_for().count("read_video") == 1
    assert paid_for().index("read_video") < paid_for().index("plan_ad")
    (request,) = asked
    assert request.instructions == f"{PLAN_INSTRUCTIONS}\n\n{ADDED_JOB}"
    (call,) = ModelCall.objects.filter(purpose="read_video")
    plan_call = ModelCall.objects.get(purpose="plan_ad")
    # The same page and photos the planner gets, the conversation so far, and the video's
    # own words. The planner is also shown the moment the reader kept, after the page's photo.
    assert call.handoff == {
        **plan_call.handoff,
        "photo_count": 1,
        "conversation": plan_call.handoff["conversation"][:1],
        "transcript": SAID,
    }
    photos = [image for image in call.images if image["label"].startswith("Photo ")]
    assert photos == plan_call.images[:1]
    assert [image["label"] for image in plan_call.images[1:]] == [
        "Photo 2 (from the product's video at 0.8 s: The applicator placing a dot on a cheek.)"
    ]
    (grid,) = call.images[len(photos) :]
    assert grid["label"] == (
        "The product's video, grid 1 of 1: 0.25 s, 0.75 s, 1.25 s, 1.75 s "
        "(each labelled in its black bar)"
    )
    (video,) = Job.objects.get().videos.all()
    assert video.read == {**KEPT, "dropped": []}
    assert results_of("plan_ad")
    # The shop owner, asked about the page's one photo, is shown that photo only: the
    # picture cut from the video is the planner's.
    shown = Attachment.objects.filter(
        message__role=Message.Role.AGENT, kind=Attachment.Kind.PICTURE
    ).values_list("file", flat=True)
    assert list(shown) == [Job.objects.get().photos.get(from_video=None).file]


def test_the_owner_answering_the_planners_question_plans_again_without_reading_again(
    fake_model: FakeModel,
    shampoo_page_url: str,
    say: Callable[..., None],
    gallery_video: None,
) -> None:
    asked = reader_answers(fake_model, KEPT)
    which_size = {
        "decision": "ask",
        "reason": "The page sells two sizes.",
        "question": "Which size should the ad sell?",
        "plan": None,
    }
    read_and_plan(fake_model, say, shampoo_page_url, which_size, PLAN)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Done."))

    say("The big one.")

    assert paid_for().count("plan_ad") == 2
    assert Job.objects.get().scenes.exists()
    assert len(asked) == 1
    assert paid_for().count("read_video") == 1


@pytest.mark.parametrize(
    "fails",
    [
        UnusableReply("the answer could not be read", input_tokens=1, output_tokens=1),
        # Moments only at seconds no frame was shown at.
        {
            "kept": [{"second": 5.0, "shows": "A hand.", "why": "A scene."}],
            "rejected": [{"second": 6.0, "why": "Nothing new."}],
        },
    ],
    ids=["the model fails", "only moments no frame shows"],
)
def test_a_video_that_cant_be_read_is_told_to_the_producer_only_and_the_ad_is_planned(
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    shampoo_page_url: str,
    say: Callable[..., None],
    gallery_video: None,
    fails: Any,
) -> None:
    fake_model.respond("read_video", fails)

    read_and_plan(fake_model, say, shampoo_page_url, PLAN)

    (video,) = Job.objects.get().videos.all()
    assert video.read is not None
    assert set(video.read) == {"error"}
    (result,) = results_of("read_page")
    assert (
        f"The product's video 1 couldn't be read ({video.read['error']}), so the ad is "
        "planned without it." in result
    )
    assert not any("video" in text for _, text in notices(api, session_id))
    assert Job.objects.get().scenes.exists()


def test_a_page_with_many_photos_shows_the_reader_45_and_five_grids_at_most(
    fake_model: FakeModel,
    shampoo_page_url: str,
    say: Callable[..., None],
    gallery_video: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 59 more of the page's photos than the shampoo's one, and 100 picked frames: more than
    # the thinning ever keeps.
    def with_more(job: Job, players: Any) -> list[ProductVideo]:
        for position in range(2, 61):
            keep_photo(
                job,
                position,
                picture(100 + position, 100, (143, 170, 140)),
                "image/png",
                source_url=f"https://shop.example/{position}.png",
            )
        videos = product_videos.save_videos(job, players)
        (video,) = videos
        video.frames_picked = [{"key": video.frames[n % 4], "second": n / 5} for n in range(100)]
        video.save(update_fields=["frames_picked"])
        return videos

    monkeypatch.setattr(producer, "save_videos", with_more)
    reader_answers(fake_model, {"kept": [], "rejected": []})
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})]),
        turn(says="Done."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    (call,) = ModelCall.objects.filter(purpose="read_video")
    labels = [image["label"] for image in call.images]
    assert len(labels) == 50
    assert labels[:45] == [f"Photo {position}" for position in range(1, 46)]
    assert [label.split(":")[0] for label in labels[45:]] == [
        f"The product's video, grid {number} of 5" for number in range(1, 6)
    ]
    assert labels[-1].endswith(
        ": 12.8 s, 13 s, 13.2 s, 13.4 s, 13.6 s, 13.8 s, 14 s, 14.2 s, "
        "14.4 s, 14.6 s, 14.8 s, 15 s, 15.2 s, 15.4 s, 15.6 s, 15.8 s "
        "(each labelled in its black bar)"
    )
    assert call.handoff["photo_count"] == 60


def test_a_moment_no_frame_shows_is_dropped_and_the_others_are_kept(
    fake_model: FakeModel,
    shampoo_page_url: str,
    say: Callable[..., None],
    gallery_video: None,
) -> None:
    made_up = {"second": 5.0, "shows": "A hand.", "why": "A scene."}
    reader_answers(fake_model, {**KEPT, "kept": [*KEPT["kept"], made_up]})

    read_and_plan(fake_model, say, shampoo_page_url, PLAN)

    (video,) = Job.objects.get().videos.all()
    assert video.read == {**KEPT, "dropped": [{"second": 5.0}]}
    assert "couldn't be read" not in results_of("read_page")[0]
