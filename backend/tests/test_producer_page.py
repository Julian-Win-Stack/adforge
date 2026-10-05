"""Reading the product page, driven through the chat. The producer's model is faked at the
gateway to call read_page, and each test checks what the tool handed back, what was stored
and what was paid for, with the shop served from a real local web server."""

import json
import logging
import re
import socket
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import IntegrityError
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient
from werkzeug import Request, Response

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents.models import ToolCall
from chat.models import Attachment
from gateway.fake import FakeModel, Outcome, turn
from gateway.models import ModelCall
from jobs.models import Job
from jobs.page import DECLARED_DATA_HEADING
from jobs.work import NO_FIRECRAWL, keep_photo

from .conftest import (
    COPIED,
    MUG_FRONT,
    MUG_SIDE,
    NO_FACE,
    PLAN,
    PRODUCT_PAGE,
    PUBLIC_ADDRESS,
    READABLE,
    SHAMPOO_PHOTO,
    SHAMPOO_PHOTO_PATH,
    SHAMPOO_PICKED,
    FakeDns,
    FakeFirecrawl,
    handoffs,
    openai_answer,
    openai_reply,
    openai_turn,
    paid_for,
    photo,
    picture,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def reading(fake_model: FakeModel, link: str, *then: tuple[str, dict[str, object]]) -> None:
    """Script the producer to read `link`, call each tool in `then`, then reply."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        *(turn(calls=[call]) for call in then),
        turn(says="Done."),
    )


def read_page_result() -> str:
    (result,) = results_of("read_page")
    return result


# --- What is kept from the page ---------------------------------------------------------------


def test_the_page_text_and_the_html_exactly_as_served_are_kept(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert "Stoneware Mug" in job.page_text_full
    assert "$24.00" in job.page_text_full
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in job.page_text_full
    assert "tracking code" not in job.page_text_full
    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in job.page_text_full
    check = ModelCall.objects.get(purpose="check_page")
    assert check.handoff["page_text"] == job.page_text_full
    # The copy model copied every line here; "$24.00" is too short to be a sentence, and the
    # price is still in the declared data.
    assert job.page_text.startswith("Stoneware Mug\nHand-thrown, holds 350 ml, dishwasher safe.")
    assert '"price": "24.00"' in job.page_text
    served = PRODUCT_PAGE.format(side=httpserver.url_for("/cdn/mug-side.png")).encode()
    assert file_store.read(job.page_html_key) == served


def test_a_long_page_still_hands_the_models_its_product_data(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    # A page with 203,000 characters of reviews: more than the models are sent. The stock
    # is only in the structured data, which the page text puts after all the words.
    reviews = "<p>" + "Lovely mug, would buy again. " * 7_000 + "</p>"
    httpserver.expect_request("/products/long-mug").respond_with_data(
        PRODUCT_PAGE.format(side="/cdn/mug-side.png").replace("</body>", reviews + "</body>"),
        content_type="text/html; charset=utf-8",
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    httpserver.expect_request("/cdn/mug-side.png").respond_with_data(
        MUG_SIDE, content_type="image/png"
    )
    link = httpserver.url_for("/products/long-mug")
    reading(fake_model, link, ("plan_ad", {}))
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    say(f"Make an ad for {link}")

    assert len(Job.objects.get().page_text_full) > 200_000
    sent = ModelCall.objects.get(purpose="check_page").handoff["page_text"]
    assert len(sent) <= 200_000
    assert sent.startswith("Stoneware Mug | Kiln & Co\nStoneware Mug\n$24.00")
    assert '"availability": "https://schema.org/InStock"' in sent
    # The product's own text keeps its declared data however long the page was.
    planned_from = ModelCall.objects.get(purpose="plan_ad").handoff["page_text"]
    assert '"availability": "https://schema.org/InStock"' in planned_from


def test_the_page_check_is_recorded_with_its_cost_time_outcome_and_judgement(
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)
    # The gateway reads its clock when a call starts, then again when the answer arrives:
    # the producer's first turn, the page check, then its second turn.
    clock = iter([0.0, 1.0, 100.0, 100.25, 200.0, 201.0])
    monkeypatch.setattr("gateway.gateway.time", SimpleNamespace(monotonic=lambda: next(clock)))

    say(f"Make an ad for {product_page_url}")

    call = ModelCall.objects.get(purpose="check_page")
    assert (call.model, call.outcome, call.attempt) == ("gpt-5-mini", "succeeded", 1)
    # 1,000 input tokens at $0.25 per million plus 100 output tokens at $2.00 per million.
    assert call.cost_usd == Decimal("0.000450")
    assert call.duration_ms == 250
    assert (call.decision, call.reason) == ("readable", READABLE["reason"])
    assert "Stoneware Mug" in call.handoff["page_text"]
    assert call.tool_call == ToolCall.objects.get(tool="read_page")
    assert str(call.session_id) == session_id


def test_a_link_that_redirects_says_which_page_was_actually_read(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    # Shops often send a removed product's link somewhere else, like a category page.
    httpserver.expect_request("/products/old-mug").respond_with_data(
        "", status=301, headers={"Location": "/products/mug"}
    )
    old_link = httpserver.url_for("/products/old-mug")
    reading(fake_model, old_link)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {old_link}")

    result = read_page_result()
    assert f"and read {product_page_url}." in result
    assert f"The link led to {product_page_url}, so that is the page read." in result
    assert ModelCall.objects.get(purpose="check_page").handoff["page_url"] == product_page_url


# --- Links that can't be read -------------------------------------------------------------------


def _serve_missing_page(httpserver: HTTPServer, *_: object) -> None:
    httpserver.expect_request("/products/mug").respond_with_data("not found", status=404)


def _serve_huge_page(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr("jobs.page.MAX_PAGE_BYTES", 100)
    httpserver.expect_request("/products/mug").respond_with_data(
        PRODUCT_PAGE.format(side="/cdn/mug-side.png"), content_type="text/html"
    )


def _serve_page_at_a_private_address(
    httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    # The test shop is served from this machine, like localhost.
    settings.FETCH_PRIVATE_ADDRESSES = False
    httpserver.expect_request("/products/mug").respond_with_data(
        PRODUCT_PAGE.format(side="/cdn/mug-side.png"), content_type="text/html"
    )


def _serve_redirect_loop(httpserver: HTTPServer, *_: object) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "", status=302, headers={"Location": "/products/mug"}
    )


@pytest.mark.parametrize(
    ("serve_link", "why", "requests_made"),
    [
        pytest.param(
            _serve_missing_page,
            "{url} answered 404 NOT FOUND, so trying again won't help.",
            1,
            id="a missing page, not tried again",
        ),
        pytest.param(
            _serve_huge_page,
            "{url} is too big to be a product page, at over 100 bytes.",
            1,
            id="too big to be a product page",
        ),
        pytest.param(
            _serve_page_at_a_private_address,
            "{url} leads to a private network address, which is never fetched.",
            0,
            id="a private network address, never fetched",
        ),
        pytest.param(
            _serve_redirect_loop,
            "{url} redirected more than 10 times.",
            11,  # The link itself, then 10 redirects followed.
            id="a redirect loop",
        ),
    ],
)
def test_a_link_that_cant_be_read_says_why_and_pays_for_nothing(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    serve_link: Callable[[HTTPServer, pytest.MonkeyPatch, Settings], None],
    why: str,
    requests_made: int,
) -> None:
    serve_link(httpserver, monkeypatch, settings)
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert read_page_result() == (
        f"The page couldn't be read: {why.format(url=link)} Ask the shop owner for a working "
        "link to the product's own page."
    )
    assert len(httpserver.log) == requests_made
    assert paid_for() == []


@pytest.mark.parametrize("private_addresses_blocked", [True, False])
def test_a_link_to_a_website_that_doesnt_exist_is_looked_up_once_and_not_read(
    fake_model: FakeModel,
    dns: FakeDns,
    say: Callable[..., None],
    settings: Settings,
    private_addresses_blocked: bool,
) -> None:
    settings.FETCH_PRIVATE_ADDRESSES = not private_addresses_blocked
    link = "https://shopp.example/products/mug"  # A typo: no such website.
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert read_page_result() == (
        "The page couldn't be read: shopp.example doesn't exist, so the link can't be opened. "
        "Check it for typos. Ask the shop owner for a working link to the product's own page."
    )
    assert dns.lookups == ["shopp.example"]
    assert paid_for() == []


def test_a_link_that_redirects_to_a_private_network_address_is_not_followed(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    dns: FakeDns,
    say: Callable[..., None],
) -> None:
    dns.records["localhost"] = PUBLIC_ADDRESS  # The shop passes the check...
    dns.records["127.0.0.1"] = "127.0.0.1"  # ...and sends us to this machine's own admin.
    admin_url = f"http://127.0.0.1:{httpserver.port}/internal/admin"
    httpserver.expect_request("/old-mug").respond_with_data(
        "", status=302, headers={"Location": admin_url}
    )
    httpserver.expect_request("/internal/admin").respond_with_data("the admin's secrets")
    link = httpserver.url_for("/old-mug")
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert read_page_result() == (
        f"The page couldn't be read: {admin_url} leads to a private network address, which is "
        "never fetched. Ask the shop owner for a working link to the product's own page."
    )
    assert [request.path for request, _ in httpserver.log] == ["/old-mug"]
    assert paid_for() == []


# --- Outside services that are down ---------------------------------------------------------


def test_a_shop_that_is_briefly_down_is_tried_again(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    httpserver.expect_oneshot_request("/products/mug").respond_with_data("busy", status=503)
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    assert read_page_result().endswith("Kept 2 product photos.")
    page_requests = [request for request, _ in httpserver.log if request.path == "/products/mug"]
    assert len(page_requests) == 2


def test_a_shop_that_stays_down_is_tried_three_times_and_the_tool_says_why(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data("busy", status=503)
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert read_page_result() == (
        "Failed: an outside service stayed down after several tries "
        f"({link} answered 503 SERVICE UNAVAILABLE)."
    )
    assert len(httpserver.log) == 3
    assert paid_for() == []


def test_a_shop_whose_name_lookup_keeps_failing_is_tried_three_times(
    fake_model: FakeModel, dns: FakeDns, say: Callable[..., None]
) -> None:
    # The name exists, but the lookup service is having a moment: that can pass.
    dns.records["shop.example"] = socket.gaierror(
        socket.EAI_AGAIN, "Temporary failure in name resolution"
    )
    link = "https://shop.example/products/mug"
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert read_page_result().startswith(
        f"Failed: an outside service stayed down after several tries ({link} could not be "
        "looked up:"
    )
    assert dns.lookups == ["shop.example"] * 3


def test_a_model_provider_that_is_briefly_down_is_tried_again_and_each_try_is_recorded(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", OutsideServiceDown("503 from the provider"), READABLE)

    say(f"Make an ad for {product_page_url}")

    assert read_page_result().endswith("Kept 2 product photos.")
    calls = ModelCall.objects.filter(purpose="check_page").order_by("attempt")
    assert [(call.attempt, call.outcome) for call in calls] == [(1, "failed"), (2, "succeeded")]
    assert "503 from the provider" in calls[0].error


def test_a_model_provider_that_stays_down_leaves_the_page_to_be_read_again_later(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", *[OutsideServiceDown("503 from the provider")] * 3)
    say(f"Make an ad for {product_page_url}")

    assert read_page_result() == (
        "Failed: an outside service stayed down after several tries (The model provider was "
        "still down after 3 tries: 503 from the provider)."
    )
    assert ModelCall.objects.filter(purpose="check_page", outcome="failed").count() == 3
    # Nothing marked the job as failed: once the provider is back, the page is read.
    assert Job.objects.get().status == "queued"

    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)
    say("Try again")
    assert results_of("read_page")[1].endswith("Kept 2 product photos.")


# --- Through Firecrawl: only this product's text ---------------------------------------------

# What the shampoo's page says about the shampoo, and what the copy model answers.
ON_THE_PAGE = (
    "This clarifying shampoo deeply cleanses away dirt, oil and product buildup with apple "
    "cider vinegar while keratin helps strengthen hair."
)
PAGE_WROTE = (
    "This concentrated shampoo with apple cider vinegar will deeply cleanse away dirt, oil, "
    "and impurities."
)
REWORDED = (
    "This concentrated shampoo with apple cider vinegar deeply cleanses away dirt, oil and "
    "impurities."
)
MADE_UP = "Dermatologists rank it the number one shampoo for hair growth."
SHAMPOO_COPIED = {
    "product": "Detox Clarifying Hair Shampoo",
    "passages": [ON_THE_PAGE, REWORDED, MADE_UP],
}


def notices(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """Every notice in the chat, oldest first: its level and what it says."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [
        (message["level"], message["text"]) for message in messages if message["role"] == "notice"
    ]


# The shampoo's other gallery photos on the local shop: I10, which the page offers at several
# sizes, and I15. I23 is the "Pairs Well With" conditioner's photo.
SPLASH_PATH = "/cdn/shop/files/Detox_Shampoo_PDP_Asset_Thumbnail_Hover.jpg"
SPLASH, SPLASH_SMALL = photo(10, 1296, 1600), photo(10, 180, 222)
LATHER_PATH = "/cdn/shop/files/Detox_Shampoo_PDP_Asset_Image_Carousel_1.jpg"
LATHER = photo(15)
CONDITIONER_PATH = (
    "/s/files/1/1043/7322/files/Update_2_FineHairCondtioner_260611-17-23_Site_Asset_PDP_"
    "Product_Thumbnail_1440x1780_15_400x400.jpg"
)


def serve_the_other_photos(httpserver: HTTPServer) -> None:
    def splash(request: Request) -> Response:
        small = "width" in request.args
        return Response(SPLASH_SMALL if small else SPLASH, content_type="image/png")

    httpserver.expect_request(SPLASH_PATH).respond_with_handler(splash)
    httpserver.expect_request(LATHER_PATH).respond_with_data(LATHER, content_type="image/png")
    httpserver.expect_request(CONDITIONER_PATH).respond_with_data(
        photo(23), content_type="image/png"
    )


def test_a_page_selling_two_products_keeps_only_this_ones_text_and_photos(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    serve_the_other_photos(httpserver)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", SHAMPOO_COPIED)
    # I16 is a thumbnail of I8's photo.
    fake_model.respond(
        "pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 10, 16], "more_images": [15]}
    )

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 3 product photos.")
    job = Job.objects.get()
    # Gallery first, each at its biggest size, the copy kept once, and not the conditioner.
    shop = firecrawl.shop
    assert [(photo.source_url, file_store.read(photo.file)) for photo in job.photos.all()] == [
        (f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478", SHAMPOO_PHOTO),
        (f"{shop}{SPLASH_PATH}?v=1782764654", SPLASH),
        (f"{shop}{LATHER_PATH}?v=1782764664", LATHER),
    ]
    # The picker saw the record's official photo, then the screenshot top to bottom.
    (picker,) = ModelCall.objects.filter(purpose="pick_photos")
    assert [image["label"] for image in picker.images] == [
        "R1",
        "Part 1 of 3 (from 0 px down the page):",
        "Part 2 of 3 (from 1200 px down the page):",
        "Part 3 of 3 (from 2400 px down the page):",
    ]
    assert file_store.read(picker.images[0]["key"]) == SHAMPOO_PHOTO
    assert "Title: Detox Clarifying Hair Shampoo" in picker.handoff["official_record"]
    assert 'I23: alt="An ivory OUAI Fine Hair Conditioner' in picker.handoff["numbered_pictures"]
    assert "links to /products/fine-hair-conditioner" in picker.handoff["numbered_pictures"]
    # The sentence on the page is kept, the reworded one is kept as the page wrote it, and
    # the made-up one is dropped. The price is only in the declared data that follows.
    words, heading, declared = job.page_text.partition(DECLARED_DATA_HEADING)
    assert words == f"{ON_THE_PAGE}\n{PAGE_WROTE}"
    assert heading and '"price": 34.0' in declared
    # The product the page says pairs well with this one is on the page, and not in its text.
    assert "Fine Hair Conditioner" in job.page_text_full
    assert "Fine Hair Conditioner" not in job.page_text
    assert job.page_text_full.endswith(heading + declared)
    # Firecrawl read the page, so the shop itself was only asked for the photos.
    assert firecrawl.asked("page") == [
        {"url": shampoo_page_url, "formats": ["markdown", "rawHtml"], "timeout": 300_000}
    ]
    assert "/products/detox-shampoo" not in [request.path for request, _ in httpserver.log]
    assert len(firecrawl.asked("marked")) == len(firecrawl.asked("product")) == 1
    assert file_store.read(job.page_html_key) == firecrawl.page_read["data"]["rawHtml"].encode()
    # The copy model is given the page with no image links, and each link cut to its path.
    (given,) = handoffs("copy_page_text")
    assert given["product"] == "Detox Clarifying Hair Shampoo"
    assert given["page_url"] == shampoo_page_url
    assert "![" not in given["page_text"] and ".jpg" not in given["page_text"]
    assert "[TRAVEL (3 OZ)](/products/detox-shampoo-travel)" in given["page_text"]
    assert "[Fine Hair Conditioner](/products/fine-hair-conditioner)" in given["page_text"]
    assert sorted(paid_for()) == ["check_page", "copy_page_text", "pick_photos"]
    assert notices(api, session_id) == []
    assert job.warnings == []


def _firecrawl_is_down(firecrawl: FakeFirecrawl, monkeypatch: pytest.MonkeyPatch) -> str:
    firecrawl.answers_first(503, "Service unavailable", times=3)
    return "Firecrawl answered 503: Service unavailable"


def _the_shop_is_down_for_firecrawl(
    firecrawl: FakeFirecrawl, monkeypatch: pytest.MonkeyPatch
) -> str:
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 503
    return "the shop answered Firecrawl 503"


def _firecrawl_times_out(firecrawl: FakeFirecrawl, monkeypatch: pytest.MonkeyPatch) -> str:
    # Waiting the real 330 seconds would make the test too slow.
    monkeypatch.setattr("jobs.firecrawl.HTTP_TIMEOUT_SECONDS", 0.2)
    firecrawl.takes_seconds = 0.5
    return "timed out after 5 min"


@pytest.mark.parametrize(
    ("goes_wrong", "tries"),
    [
        pytest.param(_firecrawl_is_down, 3, id="Firecrawl itself stays down"),
        pytest.param(_the_shop_is_down_for_firecrawl, 1, id="the shop was down for Firecrawl"),
        # Firecrawl already waited 5 minutes for the page: asking again would wait as long.
        pytest.param(_firecrawl_times_out, 1, id="Firecrawl times out"),
    ],
)
def test_firecrawl_being_down_reads_the_page_plainly_and_says_so(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    goes_wrong: Callable[[FakeFirecrawl, pytest.MonkeyPatch], str],
    tries: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    why = goes_wrong(firecrawl, monkeypatch)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    assert len(firecrawl.asked("page")) == tries
    # The photos are still picked off Firecrawl's marked screenshot.
    assert read_page_result().endswith("Kept 1 product photo.")
    job = Job.objects.get()
    # The words a visitor sees, from the plain download, and the declared data.
    assert f"{ON_THE_PAGE}\n" in job.page_text
    assert '"price": 34.0' in job.page_text
    assert notices(api, session_id) == [
        (
            "problem",
            f"Firecrawl couldn't open the page ({why}). Read it with the plain download "
            "instead, so text that needs JavaScript or sits in closed tabs may be missing from "
            "the ad.",
        )
    ]


def test_without_a_firecrawl_key_the_page_is_read_plainly_and_says_so(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.FIRECRAWL_API_KEY = ""
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    assert firecrawl.requests == []
    assert read_page_result().endswith("Kept 2 product photos.")
    assert f"{ON_THE_PAGE}\n" in Job.objects.get().page_text
    assert notices(api, session_id) == [("problem", NO_FIRECRAWL)]


def test_a_page_the_shop_answers_firecrawl_404_for_cant_be_read_and_isnt_downloaded_plainly(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # Firecrawl answers 200 even when the shop didn't: the shop's answer is in the metadata.
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 404
    reading(fake_model, shampoo_page_url)

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result() == (
        f"The page couldn't be read: {shampoo_page_url} answered 404 Not Found, so trying "
        "again won't help. Ask the shop owner for a working link to the product's own page."
    )
    assert "/products/detox-shampoo" not in [request.path for request, _ in httpserver.log]
    assert paid_for() == []
    assert notices(api, session_id) == []


def test_a_failed_copy_call_leaves_the_page_unread_and_says_so(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", *[OutsideServiceDown("503 from the provider")] * 3)

    say(f"Make an ad for {shampoo_page_url}")

    why = "The model provider was still down after 3 tries: 503 from the provider"
    assert read_page_result() == (
        f"Failed: picking this product's own text out of the page failed ({why}), so the "
        "page wasn't read. Tell the shop owner they can ask to read it again."
    )
    job = Job.objects.get()
    assert (job.page_text, job.page_text_full, job.page_html_key) == ("", "", "")
    assert not job.photos.exists()
    assert notices(api, session_id) == [
        (
            "problem",
            f"Picking this product's own text out of the page failed ({why}), so the page "
            "wasn't read and nothing was kept from it. Ask to read it again.",
        )
    ]

    # Reading it again works, from the answer Firecrawl gave the first time.
    reading(fake_model, shampoo_page_url)
    fake_model.respond("copy_page_text", SHAMPOO_COPIED)
    say("Try again")

    assert results_of("read_page")[1].endswith("Kept 1 product photo.")
    assert Job.objects.get().page_text.startswith(f"{ON_THE_PAGE}\n{PAGE_WROTE}")
    # Each of Firecrawl's three calls was made once.
    assert len(firecrawl.requests) == 3
    assert paid_for().count("check_page") == paid_for().count("pick_photos") == 1


def test_a_private_address_is_refused_before_firecrawl_is_asked(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    dns: FakeDns,
    say: Callable[..., None],
) -> None:
    dns.records["localhost"] = "127.0.0.1"  # The shop is on this machine.
    reading(fake_model, shampoo_page_url)

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result() == (
        f"The page couldn't be read: {shampoo_page_url} leads to a private network address, "
        "which is never fetched. Ask the shop owner for a working link to the product's own "
        "page."
    )
    assert firecrawl.requests == []
    assert len(httpserver.log) == 0


def test_a_page_read_again_doesnt_pay_for_the_copy_or_the_picker_twice(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # The page's photo can't be used, so the page isn't read yet and is read again when asked.
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    link = httpserver.url_for("/products/detox-shampoo")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="That page has no photo of your shampoo I can use."),
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="It still has no photo I can use."),
    )
    # A second check and a second copy, were the page wrongly paid for again.
    fake_model.respond("check_page", READABLE, READABLE)
    fake_model.respond("copy_page_text", SHAMPOO_COPIED, SHAMPOO_COPIED)
    say(f"Make an ad for {link}")

    say("Try it again")

    assert len(results_of("read_page")) == 2
    assert sorted(paid_for()) == ["check_page", "copy_page_text", "pick_photos"]
    # Each of Firecrawl's three calls was made once.
    assert len(firecrawl.requests) == 3
    no_photo = (
        "problem",
        "No usable product photo was found on the page, and every scene is made from one, so "
        "the ad can't be made until the shop owner attaches at least one.",
    )
    assert notices(api, session_id).count(no_photo) == 2


# --- Through Firecrawl: only this product's photos -------------------------------------------

DECLARED_INSTEAD = (
    "Used the photos the page declares for search engines instead, which may include other "
    "products' photos and miss some of this one's."
)


def test_a_failed_screenshot_falls_back_to_the_declared_photos(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    firecrawl.answers_first(400, "Actions failed", call="marked")
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", SHAMPOO_COPIED)

    say(f"Make an ad for {shampoo_page_url}")

    # The page declares the shampoo's photo twice: at full size, and 1,920 px wide.
    assert read_page_result().endswith("Kept 2 product photos.")
    assert Job.objects.get().page_text.startswith(f"{ON_THE_PAGE}\n{PAGE_WROTE}")
    assert "pick_photos" not in paid_for()
    assert notices(api, session_id) == [
        (
            "problem",
            "Firecrawl couldn't take the page's marked screenshot (Firecrawl answered 400: "
            "Actions failed), so this product's photos couldn't be picked off it. "
            + DECLARED_INSTEAD,
        )
    ]


def _no_record(firecrawl: FakeFirecrawl) -> None:
    del firecrawl.product["data"]["product"]


def _record_call_fails(firecrawl: FakeFirecrawl) -> None:
    firecrawl.answers_first(400, "Product extraction failed", call="product")


@pytest.mark.parametrize(
    ("goes_wrong", "notice"),
    [
        pytest.param(
            _no_record,
            (
                "info",
                "Firecrawl found no record of the product on the page, as on many pages, so its "
                "photos were picked without the shop's official photos to compare them with.",
            ),
            id="no record",
        ),
        pytest.param(
            _record_call_fails,
            (
                "problem",
                "Firecrawl couldn't get the shop's record of the product (Firecrawl answered "
                "400: Product extraction failed), so its photos were picked without the "
                "official photos to compare them with: a look-alike product's photo is a "
                "little more likely to get through.",
            ),
            id="the record call fails",
        ),
    ],
)
def test_a_page_with_no_product_record_is_picked_without_one(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    goes_wrong: Callable[[FakeFirecrawl], None],
    notice: tuple[str, str],
) -> None:
    goes_wrong(firecrawl)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 1 product photo.")
    (picker,) = ModelCall.objects.filter(purpose="pick_photos")
    assert picker.handoff["official_record"] == (
        "Official record of the product: none was found for this page."
    )
    assert [image["label"] for image in picker.images] == [
        "Part 1 of 3 (from 0 px down the page):",
        "Part 2 of 3 (from 1200 px down the page):",
        "Part 3 of 3 (from 2400 px down the page):",
    ]
    assert notices(api, session_id) == [notice]


@pytest.mark.parametrize(
    ("picker_answers", "why"),
    [
        pytest.param(
            [OutsideServiceDown("503 from the provider")] * 3,
            "Picking this product's photos off the page failed (The model provider was still "
            "down after 3 tries: 503 from the provider). ",
            id="the picker fails",
        ),
        pytest.param(
            [{**SHAMPOO_PICKED, "gallery_images": [], "more_images": []}],
            "The photo picker found no photo of this product on the page. ",
            id="it picks nothing",
        ),
        pytest.param(
            # The logo isn't numbered, and there is no I99 on the page.
            [{**SHAMPOO_PICKED, "gallery_images": [99], "more_images": []}],
            "The photo picker found no photo of this product on the page. ",
            id="it picks only numbers that aren't on the page",
        ),
    ],
)
def test_a_picker_that_fails_or_picks_nothing_falls_back_to_the_declared_photos(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    picker_answers: list[Outcome],
    why: str,
) -> None:
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", *picker_answers)

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 2 product photos.")
    shop = firecrawl.shop
    assert [photo.source_url for photo in Job.objects.get().photos.all()] == [
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478&width=1920",
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478",
    ]
    assert notices(api, session_id) == [("problem", why + DECLARED_INSTEAD)]


def test_photos_that_wont_download_are_named_and_the_rest_kept(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    httpserver.expect_request(SPLASH_PATH).respond_with_data("Not here", status=404)
    httpserver.expect_request(LATHER_PATH).respond_with_data(
        "<html>Summer sale!</html>", content_type="text/html"
    )
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 10], "more_images": [15]}
    )

    say(f"Make an ad for {shampoo_page_url}")

    assert "Kept 1 product photo." in read_page_result()
    shop = firecrawl.shop
    splash, lather = f"{shop}{SPLASH_PATH}?v=1782764654", f"{shop}{LATHER_PATH}?v=1782764664"
    assert [photo.source_url for photo in Job.objects.get().photos.all()] == [
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478"
    ]
    assert notices(api, session_id) == [
        (
            "problem",
            "2 product photos picked off the page couldn't be kept, so the ad is made without "
            "them; the rest were kept:\n"
            f"- {splash}: {splash} answered 404 NOT FOUND, so trying again won't help.\n"
            f"- {lather}: It came back as text/html, not a PNG, JPEG, WebP or GIF image.",
        )
    ]


def test_picked_photos_that_all_fail_fall_back_to_the_declared_photos(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    httpserver.expect_request(SPLASH_PATH).respond_with_data("Not here", status=404)
    httpserver.expect_request(LATHER_PATH).respond_with_data(
        "<html>Summer sale!</html>", content_type="text/html"
    )
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**SHAMPOO_PICKED, "gallery_images": [10, 15]})

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 2 product photos.")
    shop = firecrawl.shop
    assert [photo.source_url for photo in Job.objects.get().photos.all()] == [
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478&width=1920",
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478",
    ]
    splash, lather = f"{shop}{SPLASH_PATH}?v=1782764654", f"{shop}{LATHER_PATH}?v=1782764664"
    assert notices(api, session_id) == [
        (
            "problem",
            "None of the 2 product photos picked off the page could be kept. "
            + DECLARED_INSTEAD
            + f"\n- {splash}: {splash} answered 404 NOT FOUND, so trying again won't help."
            f"\n- {lather}: It came back as text/html, not a PNG, JPEG, WebP or GIF image.",
        )
    ]


def test_a_record_that_arrives_when_the_page_is_read_again_is_shown_to_the_picker(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    # The first read gets no record, and stops when the copy call fails.
    firecrawl.answers_first(400, "Product extraction failed", call="product")
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", *[OutsideServiceDown("503 from the provider")] * 3)
    say(f"Make an ad for {shampoo_page_url}")

    reading(fake_model, shampoo_page_url)
    say("Try again")

    first, again = ModelCall.objects.filter(purpose="pick_photos").order_by("created_at")
    assert first.handoff["official_record"].startswith("Official record of the product: none")
    assert "Title: Detox Clarifying Hair Shampoo" in again.handoff["official_record"]
    assert [image["label"] for image in again.images][0] == "R1"


def test_more_than_50_picked_photos_keeps_the_first_50(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    # 55 more numbered pictures, I100 to I154, each a photo of its own.
    returns = firecrawl.marked["data"]["actions"]["javascriptReturns"]
    marks = json.loads(returns[3]["value"])
    marks["imgs"] += [
        {"n": n, "src": f"{firecrawl.shop}/cdn/many/{n}.png", "srcset": "", "alt": "", "href": ""}
        for n in range(100, 155)
    ]
    returns[3]["value"] = json.dumps(marks)
    httpserver.expect_request(re.compile(r"/cdn/many/\d+\.png")).respond_with_handler(
        lambda request: Response(
            photo(int(request.path.split("/")[-1].split(".")[0])), content_type="image/png"
        )
    )
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    # The gallery is numbered after the rest of the page here: it still comes first.
    gallery, more = list(range(130, 155)), list(range(100, 130))
    fake_model.respond(
        "pick_photos", {**SHAMPOO_PICKED, "gallery_images": gallery, "more_images": more}
    )

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 50 product photos.")
    kept = [photo.source_url for photo in Job.objects.get().photos.all()]
    assert kept == [f"{firecrawl.shop}/cdn/many/{n}.png" for n in [*gallery, *more][:50]]


def test_firecrawl_saying_too_many_requests_is_tried_again(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    firecrawl.answers_first(429, "Rate limit exceeded")
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("copy_page_text", SHAMPOO_COPIED)

    say(f"Make an ad for {shampoo_page_url}")

    assert read_page_result().endswith("Kept 1 product photo.")
    assert len(firecrawl.asked("page")) == 2
    assert Job.objects.get().page_text.startswith(f"{ON_THE_PAGE}\n{PAGE_WROTE}")
    assert notices(api, session_id) == []


# --- The page's photos ------------------------------------------------------------------------


def _serve_banner_page(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data(
        "<html>Summer sale!</html>", content_type="text/html"
    )


def _serve_heic_photo(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data(
        b"\x00\x00\x00\x18ftypheic", content_type="image/heic"
    )


def _serve_huge_photo(httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jobs.page.MAX_PHOTO_BYTES", 100)
    httpserver.expect_request("/cdn/bad").respond_with_data(
        b"\x89PNG" + b"0" * 200, content_type="image/png"
    )


def _serve_down_photo_host(httpserver: HTTPServer, _: pytest.MonkeyPatch) -> None:
    httpserver.expect_request("/cdn/bad").respond_with_data("busy", status=503)


@pytest.mark.parametrize(
    ("serve_bad_photo", "why_skipped"),
    [
        pytest.param(
            _serve_banner_page,
            "It came back as text/html, not a PNG, JPEG, WebP or GIF image.",
            id="not an image",
        ),
        pytest.param(
            _serve_heic_photo,
            "It came back as image/heic, not a PNG, JPEG, WebP or GIF image.",
            id="an image models can't read",
        ),
        pytest.param(
            _serve_huge_photo,
            "{url} is too big to be a product photo, at over 100 bytes.",
            id="too big",
        ),
        pytest.param(
            _serve_down_photo_host,
            "{url} answered 503 SERVICE UNAVAILABLE.",
            id="its server stays down",
        ),
    ],
)
def test_a_photo_that_cant_be_used_is_skipped_with_its_reason_and_the_rest_are_kept(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    serve_bad_photo: Callable[[HTTPServer, pytest.MonkeyPatch], None],
    why_skipped: str,
) -> None:
    bad_url = httpserver.url_for("/cdn/bad")
    good_url = httpserver.url_for("/cdn/mug-front.png")
    # 69 bytes: small enough to be kept when the too-big case lowers the limit to 100.
    good_photo = picture(1, 1, (143, 170, 140))
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{bad_url}">'
        f'<meta property="og:image" content="{good_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        good_photo, content_type="image/png"
    )
    serve_bad_photo(httpserver, monkeypatch)
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {link}")

    assert read_page_result().endswith(
        f"Kept 1 product photo.\nSkipped 1 photo:\n- {bad_url}: {why_skipped.format(url=bad_url)}"
    )
    photos = Job.objects.get().photos.all()
    assert [(photo.source_url, file_store.read(photo.file)) for photo in photos] == [
        (good_url, good_photo)
    ]


def test_a_photo_link_to_a_private_network_address_is_never_fetched(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    dns: FakeDns,
    say: Callable[..., None],
) -> None:
    dns.records["localhost"] = PUBLIC_ADDRESS
    dns.records["127.0.0.1"] = "127.0.0.1"
    photo_url = f"http://127.0.0.1:{httpserver.port}/cdn/secret.png"
    httpserver.expect_request("/products/mug").respond_with_data(
        f'<html><head><meta property="og:image" content="{photo_url}"></head>'
        "<body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    httpserver.expect_request("/cdn/secret.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {link}")

    assert (
        f"- {photo_url}: {photo_url} leads to a private network address, which is never fetched."
    ) in read_page_result()
    assert [request.path for request, _ in httpserver.log] == ["/products/mug"]
    assert not Job.objects.get().photos.exists()


def test_photos_the_user_attaches_are_kept_from_position_1_with_no_source_link(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {link}")
    fake_model.respond("produce", turn(calls=[("use_photos", {})]), turn(says="Got them."))

    say("Here are two photos of it", ("front.png", MUG_FRONT), ("side.png", MUG_SIDE))

    assert results_of("use_photos") == [
        "Added 2 of the shop owner's photos. The job now has 2 product photos."
    ]
    photos = list(Job.objects.get().photos.all())
    assert [(photo.position, photo.source_url) for photo in photos] == [(1, ""), (2, "")]
    # The photos are the ones the user attached, kept where the chat stored them.
    attached = Attachment.objects.order_by("position").values_list("file", flat=True)
    assert [photo.file for photo in photos] == list(attached)
    assert [file_store.read(photo.file) for photo in photos] == [MUG_FRONT, MUG_SIDE]


def test_a_job_cant_keep_two_photos_at_the_same_position() -> None:
    # The backstop to a read run again starting its photos over: two photos stored under
    # one number would show the planner "Photo 1" twice. No chat path reaches it.
    job = Job.objects.create(product_url="https://shop.example/products/mug")
    keep_photo(job, 1, MUG_FRONT, "image/png", source_url="https://shop.example/front.png")

    with pytest.raises(IntegrityError, match='unique constraint "one_photo_per_position"'):
        keep_photo(job, 1, MUG_SIDE, "image/png", source_url="https://shop.example/side.png")


# --- Through the real OpenAI code ----------------------------------------------------------


def test_the_real_openai_code_sends_the_page_and_reads_back_the_judgement(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    read_page = {"link": product_page_url, "target_seconds": None}
    openai_server(
        openai_turn("", ("call_1", "read_page", read_page)),
        openai_answer(READABLE),
        openai_answer(COPIED),
        openai_answer(NO_FACE),
        openai_answer(NO_FACE),
        openai_turn("", ("call_2", "plan_ad", {})),
        openai_answer(PLAN),
        openai_turn("Here's the plan."),
    )

    say(f"Make an ad for {product_page_url}")

    assert READABLE["reason"] in read_page_result()
    assert list(Job.objects.get().scenes.values_list("line", flat=True)) == [
        "Meet the Stoneware Mug from Kiln & Co.",
        "Hand-thrown, holds 350 ml, and dishwasher safe.",
        "Yours for $24.00.",
    ]
    _, check, _, _, _, _, plan, _ = [
        request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"
    ]
    assert check["model"] == "gpt-5-mini"
    assert json.loads(check["input"])["page_url"] == product_page_url
    assert "Stoneware Mug" in json.loads(check["input"])["page_text"]
    assert check["text"]["format"]["type"] == "json_schema"
    assert plan["model"] == "gpt-5.6-sol"
    # The plan's handoff comes first in its message, ahead of the product photos.
    [message] = plan["input"]
    assert "Stoneware Mug" in json.loads(message["content"][0]["text"])["page_text"]
    assert plan["text"]["format"]["type"] == "json_schema"
    call = ModelCall.objects.get(purpose="check_page")
    assert (call.provider, call.outcome, call.input_tokens, call.output_tokens) == (
        "openai",
        "succeeded",
        1_200,
        300,
    )
    assert call.cost_usd == Decimal("0.0009")  # 1,200 x $0.25/M + 300 x $2.00/M.


def test_an_openai_reply_that_cant_be_used_is_handed_back_and_keeps_no_photo(
    openai_server: Callable[..., None], product_page_url: str, say: Callable[..., None]
) -> None:
    read_page = {"link": product_page_url, "target_seconds": None}
    openai_server(
        openai_turn("", ("call_1", "read_page", read_page)),
        openai_reply({"type": "refusal", "refusal": "I can't help with that."}),
        openai_turn("I couldn't check your page."),
    )

    say(f"Make an ad for {product_page_url}")

    assert read_page_result() == (
        "Failed: a model's answer couldn't be used (gpt-5-mini gave no usable answer for "
        "check_page)."
    )
    assert not Job.objects.get().photos.exists()


# --- When the tool itself fails -----------------------------------------------------------


def test_an_unexpected_error_is_handed_back_with_the_details_in_the_server_log(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    settings: Settings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Storage points at a file, not a folder, so saving the page's HTML blows up.
    (tmp_path / "not-a-folder").write_text("")
    settings.MEDIA_ROOT = tmp_path / "not-a-folder"
    reading(fake_model, product_page_url)

    with caplog.at_level(logging.ERROR, logger="agents.loop"):
        say(f"Make an ad for {product_page_url}")

    assert read_page_result() == (
        "Failed: an unexpected error stopped the tool. The details are in the server log."
    )
    [logged] = caplog.records
    checkpoint = ToolCall.objects.get(tool="read_page")
    assert logged.getMessage() == f"The read_page tool failed for checkpoint {checkpoint.pk}"
    assert logged.exc_info is not None
