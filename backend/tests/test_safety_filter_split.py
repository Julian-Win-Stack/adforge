"""Requirement 22 for the one-shot calls that show pictures to a model, driven through the
chat: when the safety filter refuses a call over one picture, the call is made again without
it, the run carries on, and the chat says in red which picture was left out and why."""

import re
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


# The five photos' library on the planner's sheets: four on the first, one on the second.
FIRST_SHEET = "Library pictures 1 to 4 (each numbered and labelled under it)"
SECOND_SHEET = "Library picture 5 (numbered and labelled under it)"


def refused_over(label: str, answer: dict[str, Any]) -> Callable[[ModelRequest[Any]], Any]:
    """A model whose safety filter refuses any call showing the picture labelled `label`."""

    def answering(request: ModelRequest[Any]) -> dict[str, Any]:
        if label in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return answer

    return answering


def picture(number: int) -> str:
    """The label of the sheet holding library picture `number` alone."""
    return f"Library picture {number} (numbered and labelled under it)"


def refused_over_picture(
    number: int, answer: Callable[[ModelRequest[Any]], Any] | dict[str, Any]
) -> Callable[[ModelRequest[Any]], Any]:
    """A model whose safety filter refuses any call showing library picture `number`, on
    whichever sheet it is."""

    def answering(request: ModelRequest[Any]) -> Any:
        for image in request.images:
            found = re.match(r"Library pictures? (\d+)(?: to (\d+))?", image.label)
            assert found is not None
            first = int(found[1])
            if first <= number <= int(found[2] or first):
                raise BlockedBySafetyFilter("content_policy_violation")
        return answer(request) if callable(answer) else answer

    return answering


def labels_shown(purpose: str) -> list[list[str]]:
    """The labels of the pictures each call for `purpose` was shown, oldest first."""
    calls = ModelCall.objects.filter(purpose=purpose).order_by("created_at", "id")
    return [[image["label"] for image in call.images] for call in calls]


def refusals(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """Every notice in the chat about the safety filter: its level and what it says."""
    return [(level, text) for level, text in notices(api, session_id) if "safety filter" in text]


def test_a_picture_the_planners_safety_filter_refuses_is_left_out_and_the_ad_is_planned(
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
    fake_model.answer_unscripted("plan_ad", refused_over_picture(3, PLAN))

    say(f"Make an ad for {five_photos_page}")

    # Shown every sheet, then without each in turn, last first, until one is let through;
    # then the refused sheet's pictures one by one, without each in turn, last first.
    one_by_one = [picture(1), picture(2), picture(3), picture(4), SECOND_SHEET]
    assert labels_shown("plan_ad") == [
        [FIRST_SHEET, SECOND_SHEET],
        [FIRST_SHEET],
        [SECOND_SHEET],
        one_by_one,
        [label for label in one_by_one if label != picture(4)],
        [label for label in one_by_one if label != picture(3)],
    ]
    job = Job.objects.get()
    assert job.status == Job.Status.PLANNED and job.scenes.count() == 3
    # Only the refused picture is left out, and named.
    ((level, said),) = refusals(api, session_id)
    assert (level, said) == (
        "problem",
        "The AI model's safety filter refused to look at library picture 3, so the ad was "
        "planned without it.",
    )
    assert said in [warning["text"] for warning in job.warnings]
    # The run carried on past the plan: the producer read its result and replied.
    assert results_of("plan_ad")[0].startswith("Planned")
    assert chat(api, session_id)[-1] == ("agent", "Here's the plan.")


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


def test_when_the_planners_filter_refuses_a_picture_on_each_sheet_both_are_left_out(
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
    fake_model.answer_unscripted("plan_ad", refused_over_picture(2, refused_over_picture(5, PLAN)))

    say(f"Make an ad for {five_photos_page}")

    # Each sheet left out alone is refused, so both are left out together; then the first
    # sheet's pictures are shown one by one until the refused one is found. The second
    # sheet holds one picture alone: it is the refused one.
    assert [len(labels) for labels in labels_shown("plan_ad")] == [2, 1, 1, 0, 4, 3, 3, 3]
    assert Job.objects.get().status == Job.Status.PLANNED
    assert refusals(api, session_id) == [
        (
            "problem",
            "The AI model's safety filter refused to look at library picture 2 and library "
            "picture 5, so the ad was planned without them.",
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


def test_a_plan_made_again_past_the_same_refusal_tells_the_chat_once(
    api: APIClient,
    fake_model: FakeModel,
    five_photos_page: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    question = {
        "decision": "ask",
        "reason": "The page sells two sizes.",
        "question": "Which size should the ad sell?",
        "plan": None,
    }
    answers = [question, PLAN]
    fake_model.answer_unscripted(
        "plan_ad", lambda request: refused_over_picture(1, answers[0])(request)
    )
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": five_photos_page, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Which size?"),
    )
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {five_photos_page}")
    answers.pop(0)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))

    say("The big one.")

    assert Job.objects.get().status == Job.Status.PLANNED
    assert len(refusals(api, session_id)) == 1


def test_a_picture_the_planner_wasnt_shown_is_never_marked_in_the_ads_colour(
    fake_model: FakeModel,
    five_photos_page: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": five_photos_page, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    # The plan names pictures 1 and 5 as in the ad's colour, though it never saw picture 5.
    colours = {**PLAN, "plan": {**PLAN["plan"], "colour_photos": [1, 5]}}
    fake_model.answer_unscripted("plan_ad", refused_over(SECOND_SHEET, colours))

    say(f"Make an ad for {five_photos_page}")

    marked = Job.objects.get().photos.filter(shows_product_colour=True)
    assert list(marked.values_list("position", flat=True)) == [1]
