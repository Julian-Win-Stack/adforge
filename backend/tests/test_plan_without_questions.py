"""Planning and producer tools never stop an ad to ask the owner (#662, #663)."""

from collections.abc import Callable

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn, TurnRequest

from .conftest import PLAN, READABLE, chat, handoffs, results_of

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_the_producer_has_no_tool_to_ask_the_owner(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    def decide(request: TurnRequest) -> Turn:
        assert "ask_owner" not in {tool.name for tool in request.tools}
        return turn(says="I'll use the main option on your page.")

    fake_model.answer_unscripted("produce", decide)
    say("Make an ad for my product")
    assert fake_model.turns_taken
    assert all(
        "ask_owner" not in {tool.name for tool in request.tools}
        for request in fake_model.turns_taken
    )


@pytest.mark.without_end_card_rules
def test_doubtful_photos_plan_at_once_without_an_owner_question(
    api: APIClient,
    fake_model: FakeModel,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": shampoo_page_url, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="I planned with the product photo on your page."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLAN)
    say(shampoo_page_url)

    assert len(handoffs("plan_ad")) == 1
    assert handoffs("plan_ad")[0]["photo_worry"]
    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    assert not any("?" in text for role, text in chat(api, session_id) if role != "user")


def test_no_clear_product_photo_still_stops_before_keeping_a_plan(
    api: APIClient,
    fake_model: FakeModel,
    page_read: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from jobs.models import Job

    from .conftest import a_plan_with

    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says=""))
    fake_model.respond(
        "plan_ad", {**a_plan_with(colour_photos=[], product_photos=[]), "has_product_photo": False}
    )
    say("Plan the ad")

    assert not Job.objects.get().scenes.exists()
    notices = [text for role, text in chat(api, session_id) if role == "notice"]
    assert any("None of the photos clearly shows the product" in text for text in notices)


def test_main_option_and_its_price_reach_the_producer_without_a_question(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from .conftest import PRODUCT_PAGE
    from .test_producer_plan import planning

    choice = "I chose the main 350 ml mug at $24.00."
    page = PRODUCT_PAGE.format(side="/cdn/mug-side.png").replace(
        "<h1>Stoneware Mug</h1>",
        "<h1>Stoneware Mug</h1><p>Main option: 350 ml, $24.00. "
        "Larger option: 500 ml, $30.00. Subscription: 350 ml, $21.00.</p>",
    )
    httpserver.expect_request("/options").respond_with_data(page, content_type="text/html")
    link = httpserver.url_for("/options")
    planning(fake_model, link, {**PLAN, "choices": choice})
    say(link)

    assert len(handoffs("plan_ad")) == 1
    assert choice in results_of("plan_ad")[0]
    assert ("notice", choice) in chat(api, session_id)
    assert "Larger option: 500 ml" in handoffs("plan_ad")[0]["page_text"]
    assert not results_of("ask_owner")


def test_a_page_without_price_plans_without_price_or_an_owner_question(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from .conftest import PRODUCT_PAGE, a_plan_with
    from .test_producer_plan import planning

    page = (
        PRODUCT_PAGE.format(side="/cdn/mug-side.png")
        .replace('"price": "24.00", "priceCurrency": "USD",', "")
        .replace('<p class="price">$24.00</p>', "")
    )
    httpserver.expect_request("/no-price").respond_with_data(page, content_type="text/html")
    link = httpserver.url_for("/no-price")
    scenes = [dict(scene) for scene in PLAN["plan"]["scenes"]]
    scenes[-1].update(
        line="Get your Stoneware Mug from Kiln & Co today for your favourite tea.", overlay=None
    )
    planning(fake_model, link, a_plan_with(scenes=scenes, end_card_price=""))
    say(link)

    assert results_of("plan_ad")[0].startswith("Planned 3 scenes.")
    snapshot = fake_model.turns_taken[-1].handoff.snapshot
    assert "$24" not in snapshot
    assert not any("?" in text for role, text in chat(api, session_id) if role != "user")


def test_ordinary_use_is_kept_as_broll_without_approval(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from .conftest import a_plan_with, broll
    from .test_producer_plan import planning

    scene = broll(
        {
            "line": "Enjoy your favourite tea in this hand-thrown mug for your everyday drinks.",
            "shows": "A hand pours tea into the mug.",
            "broll_kind": "does a job",
            "usage": "A hand tips the teapot above the mug.",
            "result": "Tea fills the mug.",
        }
    )
    scenes = [PLAN["plan"]["scenes"][0], scene, PLAN["plan"]["scenes"][2]]
    choice = "I planned an ordinary pour of tea into the mug."
    planning(fake_model, product_page_url, {**a_plan_with(scenes=scenes), "choices": choice})
    say(product_page_url)

    assert len(handoffs("plan_ad")) == 1
    assert "2. Enjoy your favourite tea" in fake_model.turns_taken[-1].handoff.snapshot
    assert "A hand pours tea into the mug" in fake_model.turns_taken[-1].handoff.snapshot
    assert ("notice", choice) in chat(api, session_id)
    assert not results_of("ask_owner")


def test_a_missing_part_is_invented_and_drawn_in_the_first_plan(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    from .conftest import a_plan_with, broll, handoffs
    from .test_producer_plan import planning

    part = {
        "what": "the lid",
        "looks": "A round wooden lid with a small knob.",
        "source": "invented",
    }
    scene = broll(
        {**PLAN["plan"]["scenes"][1], "shows": "A hand lifts the lid.", "owner_part": part}
    )
    scenes = [PLAN["plan"]["scenes"][0], scene, PLAN["plan"]["scenes"][2]]
    choice = "I drew an ordinary round wooden lid."
    planning(fake_model, product_page_url, {**a_plan_with(scenes=scenes), "choices": choice})
    say(product_page_url)

    assert len(handoffs("plan_ad")) == 1
    assert handoffs("check_part_picture")
    assert ("notice", choice) in chat(api, session_id)
    assert not results_of("ask_owner")
