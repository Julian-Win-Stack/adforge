"""The producer reads the page and sees the product photos itself (#769, ADR 0016): once a
page is read, the producer's own conversation holds the page's text and its product photos
as real pictures, labelled by library number. Driven through the chat, the producer's model
faked at the gateway and the shop served from a local web server."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from agents.loop import SHOWN_AGAIN_LATER
from agents.models import ToolCall
from agents.page_for_producer import page_view
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter
from jobs.library import library_of
from jobs.library_items import library_line
from jobs.models import Job, ProductVideo

from .conftest import (
    READABLE,
    ScriptedProducer,
    given_to_the_producer,
    photo,
    producer_turns,
    recorded,
)

pytestmark = [pytest.mark.django_db(transaction=True)]


def given_tool(tool: str) -> list[dict[str, Any]]:
    """Each call of `tool` as the producer's latest turn was given it, oldest first."""
    return [each for each in given_to_the_producer(producer_turns()) if each.get("tool") == tool]


def shown(answer: dict[str, Any]) -> list[tuple[str, str]]:
    """The pictures a tool's answer gave, as (label, file)."""
    return [(picture["label"], picture["key"]) for picture in answer.get("pictures", [])]


def items(*numbers: int) -> list[tuple[str, str]]:
    """The job's library items `numbers`, as the producer is shown them."""
    by_number = {item.number: item for item in library_of(Job.objects.get())}
    return [(f"Library item {number}", by_number[number].file) for number in numbers]


def developer_told(api: APIClient, session_id: str, about: str) -> list[str]:
    return [text for _, text in recorded(api, session_id) if about in text]


@pytest.fixture
def read(producer: ScriptedProducer, fake_model: FakeModel, product_page_url: str) -> None:
    """The mug's page read from its link: 2 product photos."""
    fake_model.respond("check_page", READABLE)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None}),
        "I read your mug's page.",
    )


def test_after_the_link_is_read_the_producer_holds_the_page_text_and_its_photos(
    read: None, api: APIClient, session_id: str
) -> None:
    (answer,) = given_tool("read_page")
    # The page's own words and its price, in the tool's answer, not only a photo count.
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in answer["result"]
    assert "24.00" in answer["result"]
    # Each photo's library line, so it can be named by number, and the photo itself.
    for item in library_of(Job.objects.get()):
        assert library_line(item) in answer["result"]
    assert shown(answer) == items(1, 2)
    assert developer_told(api, session_id, "producer") == []


def test_what_the_shop_says_in_its_videos_is_part_of_the_page(
    read: None, producer: ScriptedProducer
) -> None:
    job = Job.objects.get()
    ProductVideo.objects.create(
        job=job, position=1, kind="file", transcript="Every mug is thrown by hand in Leeds."
    )
    ProductVideo.objects.create(job=job, position=2, kind="file", transcript="music only")

    producer.told("Show me", ("see_page", {}), "Here it is.")

    (answer,) = given_tool("see_page")
    assert "What the shop says in its product videos:" in answer["result"]
    assert "Every mug is thrown by hand in Leeds." in answer["result"]
    assert "music only" not in answer["result"]


def test_a_photo_that_cannot_be_loaded_is_left_out_with_a_developer_notice(
    read: None, api: APIClient, session_id: str
) -> None:
    # Photo 1's file is spoiled on disk. Read straight from the view: once a photo is in an
    # earlier answer, every later turn sends it, so only a photo broken from the start is
    # ever left out of the producer's conversation.
    ((_, spoiled),) = items(1)
    Path(default_storage.path(spoiled)).write_bytes(b"not a picture")

    words, pictures = page_view(Job.objects.get())

    assert [(picture.label, picture.key) for picture in pictures] == items(2)
    assert library_line(library_of(Job.objects.get())[0]) not in words
    (told,) = developer_told(api, session_id, "couldn't be shown to the producer")
    assert told.startswith("Library item 1 couldn't be shown to the producer")


def test_photos_the_shop_owner_adds_are_shown_once_with_the_newest_page(
    read: None, producer: ScriptedProducer
) -> None:
    producer.told(
        "Use this one too",
        ("use_photos", {"photos": [{"photo": 1, "shows": "the product"}]}),
        "Added.",
        photos=[("mine.png", photo(7))],
    )

    (added,) = given_tool("use_photos")
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in added["result"]
    assert shown(added) == items(1, 2, 3)
    # The page's first answer gives its photos no more: each is sent once, with the newest.
    (first,) = given_tool("read_page")
    assert shown(first) == []
    assert first["result"].endswith(SHOWN_AGAIN_LATER)


def test_only_the_first_12_product_photos_are_shown_and_the_rest_are_named(
    read: None, producer: ScriptedProducer, api: APIClient, session_id: str
) -> None:
    producer.told(
        "Use these too",
        ("use_photos", {"photos": [{"photo": n, "shows": "the product"} for n in range(1, 12)]}),
        "Added.",
        photos=[(f"mine-{n}.png", photo(10 + n)) for n in range(11)],
    )

    (added,) = given_tool("use_photos")
    assert shown(added) == items(*range(1, 13))
    assert "The library has 1 more product photos, not shown: items 13." in added["result"]
    assert developer_told(api, session_id, "Only the first 12 of the 13 product photos")


def test_a_page_pasted_in_the_panel_is_shown_to_the_producer_when_it_looks(
    producer: ScriptedProducer,
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
    unreadable_page: str,
) -> None:
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

    # The note of the paste points the producer at the page's view, and with one photo it is
    # told to look before it plans.
    note = str(given_to_the_producer(producer_turns() - 1))
    assert "Call see_page to read the page and see its photos." in note
    assert "Call see_page, then plan" in note
    (seen,) = given_tool("see_page")
    assert "Hand-thrown in our studio from speckled stoneware." in seen["result"]
    assert shown(seen) == items(1)


def test_see_page_before_any_page_is_read_is_refused(producer: ScriptedProducer) -> None:
    producer.told("Hi", ("see_page", {}), "Send me your product link.")

    (seen,) = ToolCall.objects.filter(tool="see_page")
    assert seen.answer == "cant"
    assert seen.pictures == []


def test_a_turn_the_filter_stops_is_taken_again_without_the_photos_for_that_turn_only(
    read: None, producer: ScriptedProducer, api: APIClient, session_id: str, settings: Any
) -> None:
    settings.PRODUCER_MOST_NUDGES = 0

    producer.told("Carry on", BlockedBySafetyFilter("content_policy_violation"), "Carrying on.")

    assert producer.said()[-1] == "Carrying on."
    # The refusal named no picture, so the turn taken again went without the photos...
    (retried,) = given_tool("read_page")
    assert shown(retried) == []
    (told,) = developer_told(api, session_id, "given again on the next turn")
    assert "Library item 1 and Library item 2" in told
    # ...and the next turn is given them again.
    producer.told("And now?", ("cost_so_far", {}), "A few cents.")
    (again,) = given_tool("read_page")
    assert shown(again) == items(1, 2)


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
