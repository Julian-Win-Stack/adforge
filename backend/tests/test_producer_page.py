"""Reading the product page, driven through the chat. The producer's model is faked at the
gateway to call read_page, and each test checks what the tool handed back, what was stored
and what was paid for, with the shop served from a real local web server."""

import json
import logging
import socket
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import IntegrityError
from pytest_django import Settings
from pytest_httpserver import HTTPServer

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents.models import ToolCall
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job
from jobs.tasks import keep_photo

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    PLAN,
    PRODUCT_PAGE,
    PUBLIC_ADDRESS,
    READABLE,
    FakeDns,
    openai_answer,
    openai_reply,
    openai_turn,
    paid_for,
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
    assert "Stoneware Mug" in job.page_text
    assert "$24.00" in job.page_text
    assert "Hand-thrown, holds 350 ml, dishwasher safe." in job.page_text
    assert "tracking code" not in job.page_text
    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in job.page_text
    check = ModelCall.objects.get(purpose="check_page")
    assert "InStock" in check.handoff["page_text"]
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

    assert len(Job.objects.get().page_text) > 200_000
    for purpose in ("check_page", "plan_ad"):
        sent = ModelCall.objects.get(purpose=purpose).handoff["page_text"]
        assert len(sent) <= 200_000
        assert sent.startswith("Stoneware Mug | Kiln & Co\nStoneware Mug\n$24.00")
        assert '"availability": "https://schema.org/InStock"' in sent


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
    _, check, _, plan, _ = [
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
