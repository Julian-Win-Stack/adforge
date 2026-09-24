"""The producer's rules. Its model can skip a step, repeat one or loop, so every rule that
matters is kept by the tool that would break it, or by the loop, never by the producer's
instructions. Each test scripts the producer breaking a rule and checks the rule held."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import loop
from agents.models import ToolCall
from agents.producer import PRODUCER
from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel, meanwhile, turn
from gateway.types import UnusableReply
from jobs.models import Job

from .conftest import (
    FACTS_OK,
    PLAN,
    READABLE,
    chat,
    facts_ok,
    given_to_the_producer,
    paid_for,
    picture,
    results_of,
)

pytestmark = pytest.mark.django_db(transaction=True)

NO_CHOICES: dict[str, Any] = {"line_choices": [], "length_choice": None}


def plan_with(*lines: str) -> dict[str, Any]:
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": [{"line": line} for line in lines]}}


@pytest.fixture
def person_made(fake_model: FakeModel, planned: None, say: Callable[..., None]) -> None:
    """A chat whose ad is planned and has its person."""
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="Meet your presenter!")
    )
    say("Make the person")


@pytest.fixture
def planned_for_15_seconds(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    """A chat whose 15-second ad is planned: three scenes."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 15})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    say(f"Make a 15 second ad for {product_page_url}")


@pytest.fixture
def nothing_yet() -> None:
    """A chat where nothing has been done."""


# --- A tool that fails ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "told"),
    [
        pytest.param(
            [UnusableReply("the model refused", input_tokens=900, output_tokens=40)],
            "Failed: a model's answer couldn't be used (the model refused).",
            id="unusable answer",
        ),
        pytest.param(
            [OutsideServiceDown("503 Service Unavailable")] * 3,
            "Failed: an outside service stayed down after several tries (The model provider "
            "was still down after 3 tries: 503 Service Unavailable).",
            id="service down",
        ),
    ],
)
def test_a_tool_that_fails_hands_the_reason_back_to_the_producer(
    api: APIClient,
    fake_model: FakeModel,
    page_read: str,
    session_id: str,
    say: Callable[..., None],
    failure: list[BaseException],
    told: str,
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("plan_ad", {})]), turn(says="I couldn't plan the ad just now.")
    )
    fake_model.respond("plan_ad", *failure)

    say("Plan it")

    (result,) = results_of("plan_ad")
    assert result == told
    # The producer's next turn was given the reason, and carried on from it.
    assert given_to_the_producer(4)[-1]["result"] == told
    assert chat(api, session_id)[-1] == ("agent", "I couldn't plan the ad just now.")
    assert not Job.objects.get().scenes.exists()


def test_an_unexpected_error_in_a_tool_is_logged_and_handed_back_without_its_details(
    caplog: pytest.LogCaptureFixture,
    fake_model: FakeModel,
    page_read: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("plan_ad", {})]), turn(says="Something went wrong on my side.")
    )
    fake_model.respond("plan_ad", KeyError("scenes_by_number"))

    with caplog.at_level(logging.ERROR):
        say("Plan it")

    (result,) = results_of("plan_ad")
    assert result == (
        "Failed: an unexpected error stopped the tool. The details are in the server log."
    )
    assert "scenes_by_number" in caplog.text
    assert ToolCall.objects.get(tool="plan_ad").finished


# --- The producer itself can't carry on -----------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "told"),
    [
        pytest.param(
            [UnusableReply("the reply was cut off", input_tokens=900, output_tokens=40)],
            "I had to stop: my AI model's answer couldn't be used (the reply was cut off). "
            "Send a message to try again.",
            id="unusable answer",
        ),
        pytest.param(
            [OutsideServiceDown("503 Service Unavailable")] * 3,
            "I had to stop: the AI service stayed down after several tries (The model "
            "provider was still down after 3 tries: 503 Service Unavailable). Send a message "
            "to try again.",
            id="service down",
        ),
        pytest.param(
            [RuntimeError("the database password is hunter2")],
            "I had to stop: an unexpected error happened, and the details are in the server "
            "log. Send a message to try again.",
            id="unexpected error",
        ),
    ],
)
def test_when_the_producers_own_model_fails_the_chat_is_told_why(
    api: APIClient,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    failure: list[BaseException],
    told: str,
) -> None:
    fake_model.respond("produce", *failure)

    say("Make me an ad")

    assert chat(api, session_id) == [("user", "Make me an ad"), ("agent", told)]

    # Sending a message tries again.
    fake_model.respond("produce", turn(says="Sure. What's the link to your product's page?"))
    say("Try again")
    assert chat(api, session_id)[-1] == ("agent", "Sure. What's the link to your product's page?")


def test_an_unexpected_error_in_the_producer_is_logged(
    caplog: pytest.LogCaptureFixture, fake_model: FakeModel, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", RuntimeError("the database password is hunter2"))

    with caplog.at_level(logging.ERROR):
        say("Make me an ad")

    assert "hunter2" in caplog.text


# --- The limit on tool calls for each message -----------------------------------------------


def test_a_tool_call_past_the_limit_for_one_message_is_refused_and_the_count_restarts(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.MAX_TOOL_CALLS_PER_MESSAGE = 2
    told = "I read your page and planned the ad, then hit my limit of work for one message."
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(says=told),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    say(f"Make an ad for {product_page_url}")

    refused = ToolCall.objects.get(tool="create_person")
    assert refused.result == (
        "Refused: this would be tool call 3 since the shop owner's last message, and the "
        "limit is 2. Nothing was done. Tell the shop owner plainly that you hit the limit of "
        "work for one message, what is done so far, and that sending a message lets you "
        "carry on."
    )
    assert refused.finished
    assert paid_for() == ["check_page", "plan_ad"]
    assert chat(api, session_id)[-1] == ("agent", told)

    # The user's next message starts the count again.
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="Meet your presenter!")
    )
    say("Go on")
    assert paid_for()[2:] == ["draw_person", "design_voice", "measure_voice"]


def test_a_producer_that_asks_for_another_tool_after_the_limit_is_stopped(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.MAX_TOOL_CALLS_PER_MESSAGE = 2
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        # Its one turn to tell the user, spent asking for the tool again.
        turn(says="Let me try that again.", calls=[("create_person", {})]),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)

    say(f"Make an ad for {product_page_url}")

    assert chat(api, session_id)[-2:] == [
        ("agent", "Let me try that again."),
        ("agent", "I hit my limit of 2 steps for one message. Send a message and I'll carry on."),
    ]
    assert ToolCall.objects.filter(tool="create_person").count() == 1
    assert paid_for() == ["check_page", "plan_ad"]


def test_every_agents_tool_calls_count_towards_the_limit(
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.MAX_TOOL_CALLS_PER_MESSAGE = 2

    def a_director_draws_a_scene() -> None:
        ToolCall.objects.create(
            session_id=session_id,
            agent="director",
            tool="draw_scene",
            call_id="call_director_1",
            arguments={},
        )

    fake_model.respond(
        "produce",
        meanwhile(
            a_director_draws_a_scene,
            turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        ),
        turn(calls=[("plan_ad", {})]),
        turn(says="I hit my limit of work for one message."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    (refused,) = results_of("plan_ad")
    assert refused.startswith("Refused: this would be tool call 3 since the shop owner's")
    assert paid_for() == ["check_page"]


# --- What the producer is given each turn ---------------------------------------------------


def what_happened(given: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """A turn's conversation in short: who said what, and which tool was called."""
    return [
        ("said", each["by"], each["text"])
        if each["kind"] == "said"
        else ("tool_use", each["tool"], each["call_id"])
        for each in given
    ]


def test_a_message_sent_while_the_producer_works_is_read_on_its_next_turn(
    fake_model: FakeModel, product_page_url: str, session_id: str, say: Callable[..., None]
) -> None:
    def the_user_sends_another_message() -> None:
        # What the chat does with a message the moment it arrives: stores it.
        messages.add(
            Session.objects.get(pk=session_id),
            role=Message.Role.USER,
            text="Make it 10 seconds, please.",
        )

    fake_model.respond(
        "produce",
        meanwhile(
            the_user_sends_another_message,
            turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        ),
        turn(says="Got it: a 10 second ad."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    read_page = ToolCall.objects.get(tool="read_page")
    assert what_happened(given_to_the_producer(2)) == [
        ("said", "user", f"Make an ad for {product_page_url}"),
        ("said", "user", "Make it 10 seconds, please."),
        ("tool_use", "read_page", read_page.call_id),
    ]


def test_the_producer_is_never_given_a_directors_tool_calls(
    fake_model: FakeModel, product_page_url: str, session_id: str, say: Callable[..., None]
) -> None:
    def a_director_draws_a_scene() -> None:
        ToolCall.objects.create(
            session_id=session_id,
            agent="director",
            tool="draw_scene",
            call_id="call_director_1",
            arguments={"scene": 1},
            result="Drew scene 1.",
        )

    fake_model.respond(
        "produce",
        meanwhile(
            a_director_draws_a_scene,
            turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        ),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {product_page_url}")

    read_page = ToolCall.objects.get(tool="read_page")
    assert what_happened(given_to_the_producer(2)) == [
        ("said", "user", f"Make an ad for {product_page_url}"),
        ("tool_use", "read_page", read_page.call_id),
    ]


# --- Reading the page --------------------------------------------------------------------


def _serve_broken_link(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/products/old-mug").respond_with_data("not found", status=404)
    return httpserver.url_for("/products/old-mug")


def _serve_collection_page(httpserver: HTTPServer, fake_model: FakeModel) -> str:
    httpserver.expect_request("/collections/mugs").respond_with_data(
        "<html><body><h1>All mugs</h1><p>12 products</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond(
        "check_page", {"decision": "unreadable", "reason": "The page lists 12 mugs, not one."}
    )
    return httpserver.url_for("/collections/mugs")


@pytest.mark.parametrize(
    "serve_first_link",
    [
        pytest.param(_serve_broken_link, id="a broken link"),
        pytest.param(_serve_collection_page, id="a page that isn't one product's"),
    ],
)
def test_until_a_page_has_been_read_a_new_link_is_read_for_the_same_ad(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
    serve_first_link: Callable[[HTTPServer, FakeModel], str],
) -> None:
    first_link = serve_first_link(httpserver, fake_model)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": first_link, "target_seconds": 15})]),
        turn(says="That link didn't work. What's the link to your mug's own page?"),
    )
    say(f"Make a 15 second ad for {first_link}")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 15})]),
        turn(says="Got it: I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Sorry, it's {product_page_url}")

    job = Job.objects.get()
    assert (job.product_url, job.target_seconds) == (product_page_url, 15)
    # What the ad is made from is the new page's, not the first one's.
    assert "Stoneware Mug" in job.page_text
    assert "12 products" not in job.page_text
    assert job.photos.count() == 2
    assert [call.job_id for call in ToolCall.objects.all()] == [job.pk, job.pk]


def test_a_page_without_a_usable_photo_isnt_read_yet_so_a_new_link_is_read_for_the_same_ad(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    bare = httpserver.url_for("/products/mug-bare")
    httpserver.expect_request("/products/mug-bare").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": bare, "target_seconds": None})]),
        turn(says="That page has no photo of your mug I can use. Is there another link?"),
    )
    fake_model.respond("check_page", READABLE, READABLE)
    say(f"Make an ad for {bare}")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="Got it: this page has two photos of your mug."),
    )

    say(f"Try {product_page_url}")

    job = Job.objects.get()
    assert job.product_url == product_page_url
    assert job.photos.count() == 2
    assert paid_for() == ["check_page", "check_page"]


def test_the_same_link_sent_again_after_a_read_that_kept_no_photo_isnt_checked_again(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
) -> None:
    bare = httpserver.url_for("/products/mug-bare")
    httpserver.expect_request("/products/mug-bare").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": bare, "target_seconds": None})]),
        turn(says="That page has no photo of your mug I can use. Is there another link?"),
        turn(calls=[("read_page", {"link": bare, "target_seconds": None})]),
        turn(says="It still has no photo I can use."),
    )
    # A second check, were the page wrongly paid for again.
    fake_model.respond("check_page", READABLE, READABLE)
    say(f"Make an ad for {bare}")

    say("I've added photos to the page, try it again")

    assert paid_for() == ["check_page"]
    assert results_of("read_page")[1].startswith(
        f"Started job {Job.objects.get().pk} and read {bare}. "
        "The page names the mug, its price and its size."
    )


def test_another_link_to_the_same_page_after_a_read_that_kept_no_photo_is_checked_again(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
) -> None:
    bare = "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>"
    first = httpserver.url_for("/products/mug-bare")
    httpserver.expect_request("/products/mug-bare").respond_with_data(
        bare, content_type="text/html"
    )
    second = httpserver.url_for("/collections/mugs/mug-bare")
    httpserver.expect_request("/collections/mugs/mug-bare").respond_with_data(
        bare, content_type="text/html"
    )
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": first, "target_seconds": None})]),
        turn(says="That page has no photo of your mug I can use. Is there another link?"),
        turn(calls=[("read_page", {"link": second, "target_seconds": None})]),
        turn(says="That page has no photo I can use either."),
    )
    fake_model.respond("check_page", READABLE, READABLE)
    say(f"Make an ad for {first}")

    say(f"Try {second}")

    assert paid_for() == ["check_page", "check_page"]
    assert Job.objects.get().product_url == second


def test_a_new_link_that_cant_be_used_leaves_the_ad_without_a_page(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
) -> None:
    bare = httpserver.url_for("/products/mug-bare")
    httpserver.expect_request("/products/mug-bare").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    walled = httpserver.url_for("/products/mug-walled")
    httpserver.expect_request("/products/mug-walled").respond_with_data(
        "<html><body>Accept our cookies to continue</body></html>", content_type="text/html"
    )
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": bare, "target_seconds": None})]),
        turn(says="That page has no photo of your mug I can use. Is there another link?"),
    )
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {bare}")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": walled, "target_seconds": None})]),
        turn(calls=[("use_photos", {})]),
        turn(says="That page is only a cookie banner."),
    )
    fake_model.respond(
        "check_page", {"decision": "unreadable", "reason": "The page is only a cookie banner."}
    )

    say(f"Try {walled}", ("mine.png", picture(300, 400, (60, 90, 70))))

    assert results_of("use_photos") == [
        "Refused: the product page hasn't been read yet, and the photos belong to its ad. "
        "Nothing was done."
    ]
    assert not Job.objects.get().photos.exists()


@pytest.mark.parametrize(
    ("arguments", "told"),
    [
        pytest.param(
            {"link": "", "target_seconds": None},
            'link: "" isn\'t a link to a web page',
            id="no link",
        ),
        pytest.param(
            {"link": "not a link", "target_seconds": None},
            'link: "not a link" isn\'t a link to a web page',
            id="not a link",
        ),
        pytest.param(
            # 30 characters of address and 1,971 of name: one over the limit.
            {"link": "https://shop.example/products/" + "a" * 1971, "target_seconds": None},
            "link: it is over 2,000 characters, too long to be stored",
            id="a link of 2,001 characters",
        ),
        pytest.param(
            {"link": "https://shop.example/products/mug", "target_seconds": 0},
            "target_seconds: the length has to be from 1 to 32,767 seconds",
            id="no seconds",
        ),
        pytest.param(
            {"link": "https://shop.example/products/mug", "target_seconds": 32_768},
            "target_seconds: the length has to be from 1 to 32,767 seconds",
            id="one second too many",
        ),
        pytest.param(
            {"link": "https://shop.example/products/mug", "target_seconds": 12.5},
            "target_seconds: Input should be a valid integer, got a number with a fractional part",
            id="part of a second",
        ),
    ],
)
def test_a_link_or_length_that_cant_be_used_is_refused(
    fake_model: FakeModel, say: Callable[..., None], arguments: dict[str, Any], told: str
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("read_page", arguments)]), turn(says="What's the link?")
    )

    say("Make me an ad")

    assert results_of("read_page") == [
        f"Refused: it was called with arguments it can't use ({told}). Nothing was done."
    ]
    assert not Job.objects.exists()
    assert paid_for() == []


def test_once_a_page_has_been_read_a_link_to_another_page_is_refused(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    tracked = f"{page_read}?utm_source=ig"
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": tracked, "target_seconds": None})]),
        turn(says="This chat is for your mug. Start a new chat for another ad."),
    )

    say(f"Now make one for {tracked}")

    assert results_of("read_page")[1] == (
        f"Refused: this chat's ad is for {page_read}, whose page has already been read, and "
        f"{tracked} is a different link. Ads for other products come later: tell the shop "
        "owner to start a new chat for this one for now. Nothing was done."
    )
    assert Job.objects.get().product_url == page_read
    assert paid_for() == ["check_page"]


def test_reading_the_same_page_again_hands_back_what_was_read_and_pays_nothing(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": page_read, "target_seconds": None})]),
        turn(says="I already have your mug's page."),
    )

    say("Read my page again")

    again = results_of("read_page")[1]
    assert again.startswith(
        f"{page_read} was already read for this ad, so nothing was read or paid for again. "
        "Reading it cost $0.00045. The ad has 2 product photos."
    )
    assert paid_for() == ["check_page"]
    assert Job.objects.get().photos.count() == 2
    assert ToolCall.objects.filter(tool="read_page").last().cost_usd() == 0  # type: ignore[union-attr]


# --- Tools whose inputs don't exist yet --------------------------------------------------


@pytest.mark.parametrize(
    ("state", "tool", "arguments", "refused"),
    [
        pytest.param(
            "nothing_yet",
            "plan_ad",
            {},
            "Refused: the product page hasn't been read yet, and the ad is planned from it.",
            id="plan before the page is read",
        ),
        pytest.param(
            "nothing_yet",
            "use_photos",
            {},
            "Refused: the product page hasn't been read yet, and the photos belong to its ad.",
            id="photos before the page is read",
        ),
        pytest.param(
            "page_read",
            "create_person",
            {},
            "Refused: the ad hasn't been planned yet, and the person is made from the plan.",
            id="person before the plan",
        ),
        pytest.param(
            "planned_for_15_seconds",
            "run_planning_checks",
            NO_CHOICES,
            "Refused: the person hasn't been made yet, and the length check needs their "
            "voice's measured speed.",
            id="checks of a length before the person",
        ),
        pytest.param(
            "planned",
            "use_photos",
            {},
            "Refused: the ad is already planned, so its photos can't change now. Changing a "
            "planned ad's photos comes later.",
            id="photos after the plan",
        ),
        pytest.param(
            "page_read",
            "run_planning_checks",
            NO_CHOICES,
            "Refused: the ad hasn't been planned yet, and the checks are run on its script.",
            id="checks before the plan",
        ),
    ],
)
def test_a_tool_whose_inputs_dont_exist_yet_refuses_and_does_nothing(
    request: pytest.FixtureRequest,
    fake_model: FakeModel,
    say: Callable[..., None],
    state: str,
    tool: str,
    arguments: dict[str, Any],
    refused: str,
) -> None:
    request.getfixturevalue(state)
    fake_model.respond("produce", turn(calls=[(tool, arguments)]), turn(says="Not yet."))
    already_paid_for = paid_for()

    say("Go on", ("mine.png", picture(300, 400, (60, 90, 70))))

    assert results_of(tool)[-1] == f"{refused} Nothing was done."
    assert paid_for() == already_paid_for


def test_with_no_target_length_the_script_is_checked_before_the_person_is_made(
    fake_model: FakeModel, planned: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Every line checks out."),
    )
    fake_model.respond("fact_check", FACTS_OK)

    say("Check the script")

    assert results_of("run_planning_checks")[0].startswith(
        "The checks passed. Every line matches the product page."
    )
    assert Job.objects.get().status == Job.Status.READY_TO_RENDER


def test_an_ad_isnt_planned_without_a_product_photo(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    link = httpserver.url_for("/products/mug")
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Can you attach a photo of your mug?"),
    )
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {link}")

    assert results_of("plan_ad") == [
        "Refused: the ad has no product photos yet, and every scene is made from one. Ask "
        "the shop owner to attach at least one, then add it with use_photos. Nothing was done."
    ]
    assert paid_for() == ["check_page"]


# --- Redoing finished work ---------------------------------------------------------------


def test_planning_again_hands_back_the_plan_and_pays_nothing(
    fake_model: FakeModel, planned: None, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Same plan as before."))

    say("Plan it again")

    plan = (
        "Planned 3 scenes. Three scenes: what the mug is, what it's like to use, and its "
        "price.\n"
        "The script:\n"
        "1. Meet the Stoneware Mug from Kiln & Co.\n"
        "2. Hand-thrown, holds 350 ml, and dishwasher safe.\n"
        "3. Yours for $24.00.\n"
        "The product's colour: sage green, shown in photos 1.\n"
        "The person: A potter in her thirties in a linen apron, in a sunny workshop. Their "
        "voice: A warm, relaxed woman in her thirties with a soft British accent."
    )
    assert results_of("plan_ad") == [
        plan,
        "The ad was already planned, so nothing was planned or paid for again. Planning it "
        f"cost $0.006.\n{plan}",
    ]
    assert paid_for() == ["check_page", "plan_ad"]
    assert Job.objects.get().scenes.count() == 3


def test_making_the_person_again_hands_back_the_person_and_pays_nothing(
    api: APIClient,
    fake_model: FakeModel,
    person_made: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("create_person", {})]), turn(says="That's still your presenter.")
    )

    say("Make the person again")

    # The portrait: 1,000 x $5.00/M in + 100 x $30.00/M out = $0.008. The voice, at $25.00/M
    # characters: designed saying the 38-character first line ($0.00095), then measured
    # reading the 104-character script ($0.0026). The script's 18 words take the fake 9 s.
    again = results_of("create_person")[1]
    assert again == (
        "The person was already made and shown to the shop owner, so nothing was made or "
        "paid for again. Making them cost $0.01155. The voice speaks 2.0 words a second, "
        "measured on the script."
    )
    assert paid_for() == ["check_page", "plan_ad", "draw_person", "design_voice", "measure_voice"]
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert len([message for message in messages if message["attachments"]]) == 1


# --- Choices only the shop owner can make ------------------------------------------------


def own(scene: int, line: str) -> dict[str, Any]:
    return {"scene": scene, "choice": "own", "own_line": line}


def choosing(*line_choices: dict[str, Any]) -> dict[str, Any]:
    """The checks' arguments, passing the user's choices for these lines."""
    return {**NO_CHOICES, "line_choices": list(line_choices)}


def script_scene_2_failing(fake_model: FakeModel) -> None:
    """Plan a two-scene ad whose second line still fails the fact check after 2 rewrites."""
    fake_model.respond("plan_ad", plan_with("Meet the mug.", "Yours for $19.99."))
    fails: dict[str, Any] = {
        "decision": "checked",
        "reason": "Scene 2's price isn't the page's.",
        "question": None,
        "lines": [
            {"scene": 2, "verdict": "wrong", "problem": "Wrong price.", "page_says": "$24.00"}
        ],
    }
    first = {**fails, "lines": [facts_ok(1)["lines"][0], *fails["lines"]]}
    fake_model.respond("fact_check", first, fails, fails)
    fake_model.respond("rewrite_line", {"line": "Only $19.99."}, {"line": "Just $19.99."})


PLAN_AND_CHECK = [
    turn(calls=[("plan_ad", {})]),
    turn(calls=[("create_person", {})]),
    turn(calls=[("run_planning_checks", NO_CHOICES)]),
]


@pytest.fixture
def asked_about_scene_2(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """A chat whose ad's scene 2 still failed the fact check after 2 rewrites, and whose
    producer has asked the user what to do about it."""
    script_scene_2_failing(fake_model)
    fake_model.respond("produce", *PLAN_AND_CHECK, turn(says="Keep scene 2, or give your own?"))
    say("Plan it and check it")
    assert (
        "Scene 2's line still fails the fact check after 2 rewrites"
        in (results_of("run_planning_checks")[0])
    )


def lines() -> list[str]:
    return list(Job.objects.get().scenes.values_list("line", flat=True))


def test_a_line_the_user_gives_is_used_only_if_they_wrote_it_word_for_word(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        # Its punctuation isn't the user's.
        turn(calls=[("run_planning_checks", choosing(own(2, "Yours for just $24.00 today.")))]),
        # Only its spacing differs from the user's.
        turn(calls=[("run_planning_checks", choosing(own(2, "Yours  for just $24.00, today.")))]),
        turn(says="I've used your line."),
    )

    say("Say this instead:\nYours for just\n$24.00, today.")

    _, refused, used = results_of("run_planning_checks")
    assert refused == (
        'Refused: the shop owner never wrote "Yours for just $24.00 today." in their reply to '
        "the question. A line they give is used exactly as they wrote it, so pass it word for "
        "word, or ask them. Nothing was done."
    )
    assert used.startswith("The checks passed.")
    assert lines() == ["Meet the mug.", "Yours for just $24.00, today."]


@pytest.mark.parametrize(
    ("wrote", "passed"),
    [
        pytest.param(
            "yours for just $24.00, today.", "Yours for just $24.00, today.", id="capitals"
        ),
        pytest.param('Yours for "just" $24.00.', "Yours for “just” $24.00.", id="quote marks"),
    ],
)
def test_a_line_that_differs_from_the_users_by_more_than_spacing_is_refused(
    fake_model: FakeModel,
    asked_about_scene_2: None,
    say: Callable[..., None],
    wrote: str,
    passed: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", choosing(own(2, passed)))]),
        turn(says="Could you send your line again?"),
    )

    say(f"Use this: {wrote}")

    assert results_of("run_planning_checks")[1] == (
        f'Refused: the shop owner never wrote "{passed}" in their reply to the question. A '
        "line they give is used exactly as they wrote it, so pass it word for word, or ask "
        "them. Nothing was done."
    )
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_a_line_only_the_producer_wrote_is_refused(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    suggested = "Yours for $24.00, made by hand."
    fake_model.respond(
        "produce",
        # The producer writes a line itself, after the question, then passes it as the user's.
        turn(
            says=f"How about: {suggested}",
            calls=[("run_planning_checks", choosing(own(2, suggested)))],
        ),
        turn(says="Would you like that line?"),
    )

    say("Write me a better one")

    assert results_of("run_planning_checks")[1] == (
        f'Refused: the shop owner never wrote "{suggested}" in their reply to the question. A '
        "line they give is used exactly as they wrote it, so pass it word for word, or ask "
        "them. Nothing was done."
    )
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_a_blank_line_is_refused_as_one_the_user_never_wrote(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", choosing(own(2, "  ")))]),
        turn(says="Which line would you like?"),
    )

    say("Use my own line: Yours for $24.00.")

    assert results_of("run_planning_checks")[1] == (
        'Refused: the shop owner never wrote "  " in their reply to the question. A line they '
        "give is used exactly as they wrote it, so pass it word for word, or ask them. Nothing "
        "was done."
    )
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_a_line_the_user_wrote_before_the_question_is_refused(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        # The user's message before the checks asked, not their reply.
        turn(calls=[("run_planning_checks", choosing(own(2, "Plan it and check it")))]),
        turn(says="Sorry, I'll keep it."),
    )

    say("Keep the line as it is.")

    assert results_of("run_planning_checks")[1] == (
        'Refused: the shop owner never wrote "Plan it and check it" in their reply to the '
        "question. A line they give is used exactly as they wrote it, so pass it word for "
        "word, or ask them. Nothing was done."
    )
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_the_user_can_give_their_own_line_only_for_a_line_the_checks_asked_about(
    fake_model: FakeModel, asked_about_scene_2: None, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", choosing(own(1, "Meet my mug.")))]),
        turn(says="I can only change the line I asked about."),
    )

    say("Change scene 1 to: Meet my mug.")

    assert results_of("run_planning_checks")[1] == (
        "Refused: the checks didn't ask the shop owner about scene 1's line. Only a line they "
        "asked about can be kept or replaced; changing other lines comes later. Nothing was done."
    )
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_a_line_choice_is_refused_until_the_user_has_answered(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    script_scene_2_failing(fake_model)
    keep = {"scene": 2, "choice": "keep", "own_line": None}
    fake_model.respond(
        "produce",
        *PLAN_AND_CHECK,
        # Choosing for the user instead of asking them.
        turn(calls=[("run_planning_checks", choosing(keep))]),
        turn(says="Keep scene 2, or give your own?"),
    )

    say("Plan it and check it")

    assert results_of("run_planning_checks")[1] == (
        "Refused: the shop owner hasn't answered since the checks asked about scene 2's line. "
        "Ask them, and wait for their answer. Nothing was done."
    )
    assert Job.objects.get().status == "checking_plan"

    # Once they have answered, the same choice is used.
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", choosing(keep))]),
        turn(says="Kept as it is."),
    )
    say("Keep it")
    assert Job.objects.get().status == "ready_to_render"
    assert lines() == ["Meet the mug.", "Just $19.99."]


def test_a_length_choice_is_refused_until_the_user_has_answered(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    keep_longer = {**NO_CHOICES, "length_choice": "keep_longer"}
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 5})]),
        *PLAN_AND_CHECK,
        # Choosing for the user instead of asking them.
        turn(calls=[("run_planning_checks", keep_longer)]),
        turn(says="Your script runs 4 seconds over. Shorten it, or keep it longer?"),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    fake_model.respond("fact_check", FACTS_OK)

    say(f"Make a 5 second ad for {product_page_url}")

    asked, refused = results_of("run_planning_checks")
    assert "over your 5-second target" in asked
    assert refused == (
        "Refused: the shop owner hasn't answered since the checks asked about the script's "
        "length. Ask them, and wait for their answer. Nothing was done."
    )
    assert Job.objects.get().length_choice == ""

    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", keep_longer)]),
        turn(says="Kept it longer."),
    )
    say("Keep it longer")
    assert Job.objects.get().status == "ready_to_render"


def test_a_length_choice_the_checks_never_asked_about_is_refused(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "length_choice": "shorten"})]),
        turn(says="Checked."),
    )
    fake_model.respond("plan_ad", PLAN)

    say("Plan it, make it short")

    assert results_of("run_planning_checks") == [
        "Refused: the checks haven't asked the shop owner about the script's length. Nothing "
        "was done."
    ]
    assert "fact_check" not in paid_for()


# --- A worker that stopped part-way through a tool -----------------------------------------


@pytest.fixture
def stopped_mid_tool(session_id: str, product_page_url: str) -> ToolCall:
    """What a worker that stopped while a tool ran leaves behind: what was said, and the
    checkpoint written before the tool ran, with no result and no finishing time."""
    session = Session.objects.get(pk=session_id)
    messages.add(
        session, role=Message.Role.USER, text=f"Make me a 15 second ad for {product_page_url}"
    )
    messages.add(session, role=Message.Role.AGENT, text="I'll read your product page first.")
    return ToolCall.objects.create(
        session=session,
        agent="producer",
        tool="read_page",
        call_id="call_the_worker_stopped_on",
        arguments={"link": product_page_url, "target_seconds": 15},
    )


def test_a_tool_a_stopped_worker_left_unfinished_is_run_when_the_loop_starts_again(
    fake_model: FakeModel, stopped_mid_tool: ToolCall, product_page_url: str, session_id: str
) -> None:
    fake_model.respond("produce", turn(says="Your mug's page has what the ad needs."))
    fake_model.respond("check_page", READABLE)

    loop.run(PRODUCER, Session.objects.get(pk=session_id))

    stopped_mid_tool.refresh_from_db()
    assert stopped_mid_tool.result.endswith(
        "The page names the mug, its price and its size.\nKept 2 product photos."
    )
    assert stopped_mid_tool.finished
    assert (
        Job.objects.filter(
            session_id=session_id, product_url=product_page_url, target_seconds=15
        ).count()
        == 1
    )
    assert Job.objects.get(session_id=session_id).photos.count() == 2


def test_the_producer_is_given_the_result_of_the_tool_the_stopped_worker_left(
    fake_model: FakeModel, stopped_mid_tool: ToolCall, product_page_url: str, session_id: str
) -> None:
    fake_model.respond("produce", turn(says="Your mug's page has what the ad needs."))
    fake_model.respond("check_page", READABLE)

    loop.run(PRODUCER, Session.objects.get(pk=session_id))

    # The producer's first turn after the restart, which it can only take once the tool it
    # was waiting on has handed a result back.
    given = given_to_the_producer(1)[-1]
    assert (given["kind"], given["call_id"], given["tool"]) == (
        "tool_use",
        "call_the_worker_stopped_on",
        "read_page",
    )
    assert given["arguments"] == {"link": product_page_url, "target_seconds": 15}
    assert given["result"].endswith(
        "The page names the mug, its price and its size.\nKept 2 product photos."
    )
