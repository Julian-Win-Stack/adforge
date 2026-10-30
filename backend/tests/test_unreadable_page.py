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
    PASTE_OR_LINK,
)
from chat.messages import page_panel_wanted
from chat.models import Session
from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import (
    READABLE,
    SHAMPOO_PHOTO_PATH,
    FakeFirecrawl,
    paid_for,
    recorded,
    results_of,
)

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
    return f"{PAGE_TEXT_UNUSABLE} The page is only a cookie banner. {PASTE_OR_LINK}"


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


# A shop's bot check, as Firecrawl hands it back: an answer like any other, the shop's 200.
BOT_CHECK = "To continue, please click the Continue shopping button below."


def a_bot_check(firecrawl: FakeFirecrawl, *, times: int) -> None:
    """Have Firecrawl's next `times` page reads give the shop's bot check."""
    data = {
        "markdown": BOT_CHECK,
        "rawHtml": f"<html><body><p>{BOT_CHECK}</p></body></html>",
        "metadata": {**firecrawl.page_read["data"]["metadata"], "statusCode": 200},
    }
    firecrawl.first["page"] += [(200, {"success": True, "data": data}, {})] * times


UNREADABLE = {"decision": "unreadable", "reason": "The page is a bot check, not the product."}
READ_AGAIN = (
    "The page's first read couldn't be used (The page is a bot check, not the product.), so "
    "it was read again, fresh, and the second read worked."
)


def test_a_page_read_as_a_bot_check_is_read_again_fresh_before_the_owner_is_asked(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    a_bot_check(firecrawl, times=1)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", UNREADABLE, READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    first, again = firecrawl.asked("page")
    assert "proxy" not in first and "maxAge" not in first
    assert (again["proxy"], again["maxAge"]) == ("enhanced", 0)
    assert "Kept 1 product photo." in results_of("read_page")[0]
    assert panel_notices(api, session_id) == []
    # Only recorded for the developer: the shop owner has nothing to do about it.
    assert ("info", READ_AGAIN) in recorded(api, session_id)
    # The shop's own text, read the second time, is the page kept.
    assert BOT_CHECK not in Job.objects.get().page_text_full


def test_the_same_link_sent_again_after_two_bot_checks_is_read_again_not_replayed(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    a_bot_check(firecrawl, times=2)
    reading(fake_model, shampoo_page_url, shampoo_page_url)
    # The fresh read's bot check is the same page, so its check is the first's, unpaid.
    fake_model.respond("check_page", UNREADABLE, READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    told = f"{PAGE_TEXT_UNUSABLE} {UNREADABLE['reason']} {PASTE_OR_LINK}"
    assert panel_notices(api, session_id) == [("problem", told, True)]
    assert results_of("read_page")[0].endswith(PASTE_IN_THE_PANEL)

    say(f"Please try the same link again: {shampoo_page_url}")

    # Asked of Firecrawl again, not given back the saved bot check: the shop answers now.
    assert len(firecrawl.asked("page")) == 3
    assert "proxy" not in firecrawl.asked("page")[2]
    assert "Kept 1 product photo." in results_of("read_page")[1]
    assert BOT_CHECK not in Job.objects.get().page_text_full


def test_a_fresh_read_that_worked_is_kept_for_a_read_run_again(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    say: Callable[..., None],
) -> None:
    # The page's photo can't be used, so the page isn't read yet and is read again when asked.
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    link = httpserver.url_for("/products/detox-shampoo")
    a_bot_check(firecrawl, times=1)
    reading(fake_model, link, link)
    fake_model.respond("check_page", UNREADABLE, READABLE)
    say(f"Make an ad for {link}")

    say("Try it again")

    assert len(results_of("read_page")) == 2
    # The first read and the fresh one: the read run again is given the fresh one back.
    assert len(firecrawl.asked("page")) == 2


def test_a_fresh_read_that_fails_asks_the_owner_as_a_bot_check_does(
    api: APIClient,
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    a_bot_check(firecrawl, times=1)
    # The fresh read: Firecrawl stays down through its tries.
    firecrawl.answers_first(503, "Service unavailable", times=3)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", UNREADABLE)

    say(f"Make an ad for {shampoo_page_url}")

    assert len(firecrawl.asked("page")) == 4
    told = f"{PAGE_TEXT_UNUSABLE} {UNREADABLE['reason']} {PASTE_OR_LINK}"
    assert panel_notices(api, session_id) == [("problem", told, True)]
    assert results_of("read_page")[0].endswith(PASTE_IN_THE_PANEL)
