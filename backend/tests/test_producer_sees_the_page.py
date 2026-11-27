"""The producer reads the page and sees the product photos itself (#769, ADR 0016): once a
page is read, the producer's own conversation holds the page's text and its product photos
as real pictures, labelled by library number. Driven through the chat, the producer's model
faked at the gateway and the shop served from a local web server."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job

from .conftest import (
    READABLE,
    ScriptedProducer,
    given_to_the_producer,
    photo,
    producer_turns,
)

pytestmark = [pytest.mark.django_db(transaction=True)]


def given_tool(tool: str) -> list[dict[str, Any]]:
    """Each call of `tool` as the producer's latest turn was given it, oldest first."""
    return [each for each in given_to_the_producer(producer_turns()) if each.get("tool") == tool]


def library_photos() -> list[tuple[str, str]]:
    """The job's product photos in its library, as (label, file) the producer is shown."""
    job = Job.objects.get()
    return [
        (f"Library item {item.number}", item.file)
        for item in job.library.order_by("number")
        if item.source["came_from"] in ("page_photo", "owner_photo")
    ]


def test_after_the_link_is_read_the_producer_holds_the_page_text_and_its_photos(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    fake_model.respond("check_page", READABLE)

    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        "I read your mug's page.",
    )

    (read,) = given_tool("read_page")
    # The page's own words and its price, in the tool's answer, not only a photo count.
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in read["result"]
    assert "24.00" in read["result"]
    # Each photo's library line, so it can be named by number.
    assert "1. " in read["result"] and "2. " in read["result"]
    # The photos themselves, as pictures labelled by library number, in library order.
    shown = [(picture["label"], picture["key"]) for picture in read["pictures"]]
    assert shown == library_photos()
    assert len(shown) == 2
    assert recorded_kinds() == []


def test_a_photo_that_cannot_be_loaded_is_left_out_with_a_developer_notice(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model.respond("check_page", READABLE)
    real_read = file_store.read
    broken: list[str] = []

    def read_unless_broken(key: str) -> bytes:
        if key in broken:
            raise FileNotFoundError(key)
        return real_read(key)

    # The first product photo's file goes missing once the library is built.
    from agents import page_for_producer

    real_view = page_for_producer.page_for_producer

    def view_with_one_missing(job: Job) -> Any:
        broken.append(job.library.order_by("number").first().file)  # type: ignore[union-attr]
        return real_view(job)

    monkeypatch.setattr(page_for_producer, "page_for_producer", view_with_one_missing)
    monkeypatch.setattr(file_store, "read", read_unless_broken)

    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        "I read your mug's page.",
    )

    (read,) = given_tool("read_page")
    assert [picture["label"] for picture in read["pictures"]] == ["Library item 2"]
    # The ad carries on: the page is read, and the developer is told which photo.
    assert Job.objects.get().page_html_key
    assert recorded_kinds() == ["producer_photo_not_loaded"]
    (warning,) = [
        each for each in Job.objects.get().warnings if each["kind"] == "producer_photo_not_loaded"
    ]
    assert "Library item 1" in warning["text"]


def test_photos_the_shop_owner_adds_are_shown_to_the_producer_with_the_page(
    producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str
) -> None:
    fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        "I read your mug's page.",
    )

    producer.told(
        "Use this one too",
        ("use_photos", {"photos": [{"photo": 1, "shows": "the product"}]}),
        "Added.",
        photos=[("mine.png", photo(7))],
    )

    (added,) = given_tool("use_photos")
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in added["result"]
    shown = [(picture["label"], picture["key"]) for picture in added["pictures"]]
    assert shown == library_photos()
    assert len(shown) == 3


def test_a_page_pasted_in_the_panel_is_shown_to_the_producer_when_it_looks(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    api: Any,
    session_id: str,
    unreadable_page: str,
) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(calls=[("see_page", {})]), turn(says="Got your page."))
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": "Stoneware Mug\nHand-thrown in our studio from speckled stoneware.",
            "photos": [SimpleUploadedFile("front.png", photo(1), content_type="image/png")],
            "pasted_page": "true",
        },
        format="multipart",
    )
    assert sent.status_code == 201, sent.json()

    # The note telling the producer of the paste points it at the page's view.
    assert "see_page" in str(given_to_the_producer(producer_turns() - 1))
    (seen,) = given_tool("see_page")
    assert "Hand-thrown in our studio from speckled stoneware." in seen["result"]
    assert [(p["label"], p["key"]) for p in seen["pictures"]] == library_photos()


def test_see_page_before_any_page_is_read_is_refused(
    producer: ScriptedProducer, fake_model: FakeModel
) -> None:
    producer.told("Hi", ("see_page", {}), "Send me your product link.")

    (seen,) = ToolCall.objects.filter(tool="see_page")
    assert seen.answer == "cant"
    assert seen.pictures == []


def recorded_kinds() -> list[str]:
    return [
        warning["kind"]
        for warning in Job.objects.get().warnings
        if warning["kind"] == "producer_photo_not_loaded"
    ]


@pytest.fixture
def unreadable_page(
    fake_model: FakeModel, firecrawl: Any, shampoo_page_url: str, say: Callable[..., None]
) -> str:
    """A chat whose product page couldn't be read: the shop answered 404."""
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 404
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})]),
        turn(says="Paste the page below."),
    )
    say(f"Make an ad for {shampoo_page_url}")
    return shampoo_page_url


def test_a_turn_the_filter_stops_is_taken_again_without_the_photos_and_the_ad_carries_on(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    product_page_url: str,
    settings: Any,
) -> None:
    settings.PRODUCER_MOST_NUDGES = 0
    fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        "I read your mug's page.",
    )

    producer.told("Carry on", BlockedBySafetyFilter("content_policy_violation"), "Carrying on.")

    # The photos it had looked at are left out of that turn and every later one, and the
    # developer is told: the existing rule for pictures in an agent's conversation.
    assert producer.said()[-1] == "Carrying on."
    (left_out,) = [
        each for each in Job.objects.get().warnings if each["kind"] == "left_out_by_the_filter"
    ]
    assert "Library item 1" in left_out["text"] and "Library item 2" in left_out["text"]
    (read,) = given_tool("read_page")
    assert read.get("pictures", []) == []
