"""Requirement 22 for the one-shot calls that show pictures to a model, driven through the
chat: when the safety filter refuses a call over one picture, the call is made again without
it, the run carries on, and the chat says in red which picture was left out and why."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs.models import Job, SceneStep

from .conftest import (  # noqa: F401 (FakeFirecrawl for the fixtures)
    PLAN,
    READABLE,
    FakeFirecrawl,
    HeldSteps,
    chat,
    handoffs,
    photo,
    results_of,
)
from .test_producer_page import notices
from .test_producer_scenes import CHOICE, making
from .test_product_videos import picks_videos, player, tiny_mp4  # noqa: F401 (fixtures)
from .test_video_reader import KEPT, gallery_video, read_and_plan, thinned  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

FIVE_PHOTOS_PAGE = """<!doctype html>
<html>
<head>
  <title>Stoneware Mug</title>
  <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Product", "name": "Stoneware Mug",
      "image": [{images}],
      "offers": {{"@type": "Offer", "price": "24.00", "priceCurrency": "USD"}}}}
  </script>
</head>
<body>
  <h1>Stoneware Mug</h1>
  <p>Hand-thrown, holds 350 ml, dishwasher safe.</p>
</body>
</html>
"""


@pytest.fixture
def five_photos_page(httpserver: HTTPServer) -> str:
    """A product page with five photos of its product."""
    paths = [f"/cdn/mug-{number}.png" for number in range(1, 6)]
    for number, path in enumerate(paths, 1):
        httpserver.expect_request(path).respond_with_data(
            photo(number, 300, 400), content_type="image/png"
        )
    images = ", ".join(f'"{path}"' for path in paths)
    httpserver.expect_request("/products/mug").respond_with_data(
        FIVE_PHOTOS_PAGE.format(images=images), content_type="text/html; charset=utf-8"
    )
    return httpserver.url_for("/products/mug")


def refused_over(label: str, answer: dict[str, Any]) -> Callable[[ModelRequest[Any]], Any]:
    """A model whose safety filter refuses any call showing the picture labelled `label`."""

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        if label in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return answer

    return answering


def labels_shown(purpose: str) -> list[list[str]]:
    """The labels of the pictures each call for `purpose` was shown, oldest first."""
    calls = ModelCall.objects.filter(purpose=purpose).order_by("created_at", "id")
    return [[image["label"] for image in call.images] for call in calls]


def refusals(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """Every notice in the chat about the safety filter: its level and what it says."""
    return [(level, text) for level, text in notices(api, session_id) if "safety filter" in text]


def test_a_photo_the_planners_safety_filter_refuses_is_left_out_and_the_ad_is_planned(
    api: APIClient,
    fake_model: FakeModel,
    five_photos_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": five_photos_page, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.answer_unscripted("plan_ad", refused_over("Photo 3", PLAN))

    say(f"Make an ad for {five_photos_page}")

    # Shown every photo, then without each in turn, last first, until one is let through.
    every = [f"Photo {number}" for number in range(1, 6)]
    assert labels_shown("plan_ad") == [
        every,
        [label for label in every if label != "Photo 5"],
        [label for label in every if label != "Photo 4"],
        [label for label in every if label != "Photo 3"],
    ]
    job = Job.objects.get()
    assert job.status == Job.Status.PLANNED and job.scenes.count() == 3
    ((level, said),) = refusals(api, session_id)
    assert level == "problem" and "photo 3" in said
    assert said in [warning["text"] for warning in job.warnings]
    # The run carried on past the plan: the producer read its result and replied.
    assert results_of("plan_ad")[0].startswith("Planned")
    assert chat(api, session_id)[-1] == ("agent", "Here's the plan.")


def test_a_photo_the_video_readers_safety_filter_refuses_is_left_out_and_the_video_is_read(
    api: APIClient,
    fake_model: FakeModel,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    gallery_video: None,  # noqa: F811
) -> None:
    fake_model.answer_unscripted("read_video", refused_over("Photo 1", KEPT))

    read_and_plan(fake_model, say, shampoo_page_url, PLAN)

    grid = (
        "The product's video, grid 1 of 1: 0.25 s, 0.75 s, 1.25 s, 1.75 s "
        "(each labelled in its black bar)"
    )
    # Shown everything, then without the grid, last first, then without the photo.
    assert labels_shown("read_video") == [["Photo 1", grid], ["Photo 1"], [grid]]
    (video,) = Job.objects.get().videos.all()
    assert video.read == {**KEPT, "dropped": []}
    ((level, said),) = refusals(api, session_id)
    assert level == "problem" and "photo 1" in said and "video 1" in said
    assert said in [warning["text"] for warning in Job.objects.get().warnings]
    assert Job.objects.get().scenes.exists()


def test_a_photo_the_picture_choosers_safety_filter_refuses_is_left_out_and_the_picture_made(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (2, None))
    say("Make scene 2's starting picture")
    # Shown one photo, it isn't asked which: it answers no photo.
    choice = {key: value for key, value in CHOICE.items() if key != "photo"}
    fake_model.answer_unscripted("choose_starting_picture", refused_over("Photo 2", choice))
    fake_model.respond("produce", turn(says="Scene 2's picture is ready!"))

    steps.run_held()

    # Shown the portrait and both photos, then without photo 2, the last picture it may go
    # without: the portrait is the scene's person and always shown.
    assert labels_shown("choose_starting_picture") == [
        ["The portrait", "Photo 1", "Photo 2"],
        ["The portrait", "Photo 1"],
    ]
    job = Job.objects.get()
    assert SceneStep.objects.get().status == "finished"
    assert handoffs("make_starting_picture")[0]["pictures"][1] == job.photos.get(position=1).file
    ((level, said),) = refusals(api, session_id)
    assert level == "problem" and "photo 2" in said and "scene 2" in said
    assert said in [warning["text"] for warning in job.warnings]


def test_moments_in_a_grid_the_video_readers_safety_filter_refuses_are_never_kept(
    api: APIClient,
    fake_model: FakeModel,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    gallery_video: None,  # noqa: F811
) -> None:
    grid = (
        "The product's video, grid 1 of 1: 0.25 s, 0.75 s, 1.25 s, 1.75 s "
        "(each labelled in its black bar)"
    )
    # Read without the grid, it still names moments at the grid's seconds.
    fake_model.answer_unscripted("read_video", refused_over(grid, KEPT))

    read_and_plan(fake_model, say, shampoo_page_url, PLAN)

    assert labels_shown("read_video") == [["Photo 1", grid], ["Photo 1"]]
    (video,) = Job.objects.get().videos.all()
    assert video.read is not None and set(video.read) == {"error"}
    ((level, said),) = refusals(api, session_id)
    assert level == "problem" and "0.25 s" in said and "video 1" in said
    assert Job.objects.get().scenes.exists()


def test_when_the_planners_filter_refuses_two_photos_the_ad_is_planned_without_any(
    api: APIClient,
    fake_model: FakeModel,
    five_photos_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": five_photos_page, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    refusing_4 = refused_over("Photo 4", PLAN)
    fake_model.answer_unscripted(
        "plan_ad", lambda request: refused_over("Photo 2", refusing_4(request))(request)
    )

    say(f"Make an ad for {five_photos_page}")

    # Each photo left out alone is refused, so all are left out together.
    assert [len(labels) for labels in labels_shown("plan_ad")] == [5, 4, 4, 4, 4, 4, 0]
    assert Job.objects.get().status == Job.Status.PLANNED
    assert refusals(api, session_id) == [
        (
            "problem",
            "The AI model's safety filter refused to look at photo 1, photo 2, photo 3, "
            "photo 4 and photo 5, so the ad was planned without them.",
        )
    ]


def test_a_picture_chooser_refused_over_every_photo_is_never_asked_with_none(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (2, None))
    say("Make scene 2's starting picture")

    def refusing_any_photo(request: ModelRequest[Any]) -> dict[str, Any]:
        if any(image.label.startswith("Photo") for image in request.images):
            raise BlockedBySafetyFilter("content_policy_violation")
        return CHOICE

    fake_model.answer_unscripted("choose_starting_picture", refusing_any_photo)
    fake_model.respond("produce", turn(says="The picture was blocked."))

    steps.run_held()

    # A scene's picture is drawn from a product photo, so one is always shown.
    assert labels_shown("choose_starting_picture") == [
        ["The portrait", "Photo 1", "Photo 2"],
        ["The portrait", "Photo 1"],
        ["The portrait", "Photo 2"],
    ]
    assert SceneStep.objects.get().status == "failed"
    assert refusals(api, session_id) == [
        (
            "problem",
            "Scene 2's starting picture couldn't be made: the AI service's safety filter "
            "blocked it.",
        )
    ]
