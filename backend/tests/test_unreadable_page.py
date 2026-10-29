"""A product page that can't be read opens the page panel, driven through the chat: the
read-page step's two unreadable exits (the page can't be fetched; its text is junk) each post
one plain notice that asks for the panel, and tell the producer to point the shop owner to
it. A page read later from a working link closes it."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents.producer import (
    INSTRUCTIONS,
    PAGE_NOT_OPENED,
    PAGE_TEXT_UNUSABLE,
    PASTE_IN_THE_PANEL,
)
from chat.messages import page_panel_wanted
from chat.models import Session
from gateway.fake import FakeModel, turn

from .conftest import READABLE, FakeFirecrawl, paid_for, results_of

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def reading(fake_model: FakeModel, *links: str) -> None:
    """Script the producer to read each of `links`, each in its own turn of the chat."""
    for link in links:
        fake_model.respond(
            "produce",
            turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
            turn(says="Done."),
        )


def messages(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    response = api.get(f"/api/sessions/{session_id}/messages/")
    assert response.status_code == 200
    found: list[dict[str, Any]] = response.json()
    return found


def panel_notices(api: APIClient, session_id: str) -> list[tuple[str, str, bool]]:
    """Each message that says whether the page panel is wanted: its level, its text, and
    what it says, oldest first."""
    return [
        (message["level"], message["text"], message["wants_pasted_page"])
        for message in messages(api, session_id)
        if message["wants_pasted_page"] is not None
    ]


def _a_shop_that_answers_404(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/products/mug").respond_with_data("Not here", status=404)
    return PAGE_NOT_OPENED


def _a_cookie_wall(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body>Accept our cookies to continue</body></html>", content_type="text/html"
    )
    fake_model.respond(
        "check_page", {"decision": "unreadable", "reason": "The page is only a cookie banner."}
    )
    return f"{PAGE_TEXT_UNUSABLE} The page is only a cookie banner."


@pytest.mark.parametrize(
    "serve",
    [
        pytest.param(_a_shop_that_answers_404, id="the page can't be fetched"),
        pytest.param(_a_cookie_wall, id="the page's text is junk"),
    ],
)
def test_an_unreadable_page_asks_for_the_page_panel_in_one_line(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    session_id: str,
    say: Callable[..., None],
    serve: Callable[[HTTPServer, FakeModel], str],
) -> None:
    told = serve(httpserver, fake_model)
    link = httpserver.url_for("/products/mug")
    reading(fake_model, link)

    say(f"Make an ad for {link}")

    assert panel_notices(api, session_id) == [("problem", told, True)]
    (result,) = results_of("read_page")
    assert result.endswith(PASTE_IN_THE_PANEL)
    assert page_panel_wanted(Session.objects.get(pk=session_id))
    # Only the text check, for a page fetched whose text is junk: nothing else is paid for.
    assert set(paid_for()) <= {"check_page"}


def test_a_page_firecrawl_hears_404_for_asks_for_the_page_panel(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # Firecrawl answers 200 even when the shop didn't: the shop's answer is in the metadata.
    firecrawl.page_read["data"]["metadata"]["statusCode"] = 404
    reading(fake_model, shampoo_page_url)

    say(f"Make an ad for {shampoo_page_url}")

    assert panel_notices(api, session_id) == [
        (
            "problem",
            PAGE_NOT_OPENED,
            True,
        )
    ]
    # The producer is still given why, in the tool's result.
    assert results_of("read_page")[0] == (
        f"Failed: the page couldn't be read: {shampoo_page_url} answered 404 Not Found, so trying "
        f"again won't help. {PASTE_IN_THE_PANEL}"
    )
    assert paid_for() == []


def test_a_working_link_read_after_an_unreadable_one_closes_the_page_panel(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    httpserver.expect_request("/products/gone").respond_with_data("Not here", status=404)
    gone = httpserver.url_for("/products/gone")
    reading(fake_model, gone, product_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {gone}")
    say(f"Try this one: {product_page_url}")

    assert [wanted for _, _, wanted in panel_notices(api, session_id)] == [True, False]
    assert panel_notices(api, session_id)[-1][:2] == (
        "info",
        "The page from your new link was read, so there's no need to paste it.",
    )
    assert not page_panel_wanted(Session.objects.get(pk=session_id))
    assert "Kept 2 product photos." in results_of("read_page")[1]


def test_a_page_read_with_no_panel_wanted_says_nothing_of_the_panel(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    assert panel_notices(api, session_id) == []
    assert not page_panel_wanted(Session.objects.get(pk=session_id))


def test_the_producer_is_told_of_the_panel_in_general_words() -> None:
    # Pinned word for word: these lines are prompt wording, which changes only on Julian's
    # yes (CODING_STANDARDS.md: general rules, no shop or product names).
    assert PASTE_IN_THE_PANEL == (
        "The chat has shown the shop owner why and opened a panel under it where they can "
        "paste the page's text and attach its photos. Tell them in one line that the page "
        "can't be read, and that they can paste its text and attach its photos in the panel "
        "below, or send a working link to the product's own page."
    )
    assert (
        "When a product page can't be read, the chat opens a panel where the shop owner can "
        "paste the page's text and attach its photos. If they paste a page's text into the "
        "chat instead, don't take facts from it: point them to the panel, which keeps it as "
        "the page.\n"
    ) in INSTRUCTIONS
