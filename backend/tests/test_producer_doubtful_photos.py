"""When the photos read off the page are doubtful, the ad isn't planned until the shop owner
has seen them and answered: the page blocked our reader, the page's declared photos were used
instead of picked ones, or fewer than 2 photos were kept. Driven through the chat, with the
producer's model faked at the gateway and the shop and Firecrawl served from a local server."""

from collections.abc import Callable

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job

from .conftest import (
    PLAN,
    READABLE,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    photo,
    results_of,
)
from .test_producer_page import notices, serve_the_other_photos

pytestmark = pytest.mark.django_db(transaction=True)


def read_then_plan(fake_model: FakeModel, link: str) -> None:
    """Script the producer to read `link`, then try to plan the ad straight away."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here are the photos I found."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)


def shown_photos(api: APIClient, session_id: str) -> list[list[str]]:
    """The pictures on each of the agent's messages that carry any, as the chat shows them."""
    return [
        [attached["kind"] for attached in message["attachments"]]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["role"] == "agent" and message["attachments"]
    ]


def test_a_blocked_page_stops_before_planning_until_the_owner_attaches_photos(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    serve_the_other_photos(httpserver)
    read_then_plan(fake_model, shampoo_page_url)
    # The picker saw a block page, though it named pictures anyway: none are taken from it.
    fake_model.respond(
        "pick_photos",
        {**SHAMPOO_PICKED, "gallery_images": [8, 10, 15], "page_blocked": True},
    )

    say(f"Make an ad for {shampoo_page_url}")

    job = Job.objects.get()
    # The page's declared photos were kept instead: the shampoo's page declares 2.
    assert job.photos.count() == 2
    assert all("Thumbnail_Hover" not in photo.source_url for photo in job.photos.all())
    assert ("problem", BLOCKED_NOTICE) in notices(api, session_id)
    assert shown_photos(api, session_id) == [["picture", "picture"]]
    read, planning = results_of("read_page")[0], results_of("plan_ad")[0]
    assert "blocked" in read and "go ahead with these photos or attach more" in read
    assert planning.startswith("Refused:")
    assert not job.scenes.exists()
    assert not ModelCall.objects.filter(purpose="plan_ad").exists()

    # The shop owner attaches 2 photos: they're added, and the ad is planned.
    fake_model.respond(
        "produce",
        turn(calls=[("use_photos", {})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    say("Use these instead", ("front.png", photo(40)), ("side.png", photo(41)))

    assert results_of("use_photos")[0].startswith("Added 2 of the shop owner's photos.")
    assert Job.objects.get().scenes.count() == 3


BLOCKED_NOTICE = (
    "The shop's page blocked our page reader: it showed a block or error page instead of the "
    "product, so no photos could be picked off it. Used the photos the page declares for "
    "search engines instead, which may include other products' photos and miss some of this "
    "one's."
)


def test_a_page_with_2_or_more_photos_picked_is_planned_straight_away(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    serve_the_other_photos(httpserver)
    read_then_plan(fake_model, shampoo_page_url)
    fake_model.respond("pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 10, 15]})

    say(f"Make an ad for {shampoo_page_url}")

    assert results_of("read_page")[0].endswith("Kept 3 product photos.")
    assert shown_photos(api, session_id) == []
    assert Job.objects.get().scenes.count() == 3


def _picks_nothing(httpserver: HTTPServer) -> dict[str, object]:
    return {**SHAMPOO_PICKED, "gallery_images": []}


def _picks_photos_that_wont_download(httpserver: HTTPServer) -> dict[str, object]:
    # The shop doesn't serve I10 or I15, so the page's declared photos are used instead.
    return {**SHAMPOO_PICKED, "gallery_images": [10, 15]}


def _picks_1_photo(httpserver: HTTPServer) -> dict[str, object]:
    serve_the_other_photos(httpserver)
    # I16 is a thumbnail of I8's photo, so 1 photo is kept.
    return {**SHAMPOO_PICKED, "gallery_images": [8, 16]}


@pytest.mark.parametrize(
    ("picks", "why", "kept"),
    [
        pytest.param(_picks_nothing, "no photos could be picked off the page", 2, id="none"),
        pytest.param(
            _picks_photos_that_wont_download,
            "no photos could be picked off the page",
            2,
            id="picked ones won't download",
        ),
        pytest.param(_picks_1_photo, "only 1 photo of the product was found", 1, id="1 photo"),
    ],
)
def test_doubtful_photos_are_shown_and_planned_only_once_the_owner_says_go(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    picks: Callable[[HTTPServer], dict[str, object]],
    why: str,
    kept: int,
) -> None:
    read_then_plan(fake_model, shampoo_page_url)
    fake_model.respond("pick_photos", picks(httpserver))

    say(f"Make an ad for {shampoo_page_url}")

    assert why in results_of("read_page")[0]
    assert shown_photos(api, session_id) == [["picture"] * kept]
    assert results_of("plan_ad")[0].startswith("Refused: the shop owner hasn't answered")
    assert not ModelCall.objects.filter(purpose="plan_ad").exists()

    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    say("Those are fine, go ahead")

    assert Job.objects.get().scenes.count() == 3
