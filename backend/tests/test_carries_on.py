"""The producer carries on by itself (spec #272, Part 2): the shop owner never has to type
"please carry on" to get an unfinished ad moving. Through the chat, the producer's model
faked: a turn that ends with the ad unfinished and nothing waiting is nudged (2a), and a turn
the safety filter stopped is taken again (2b). Neither shows in the owner's chat; each is
recorded and sent to Sentry."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents.loop import STOPPED, SYSTEM_NOTE
from agents.models import SystemNote
from agents.tasks import FILTER_KEPT_STOPPING, FILTER_RETRY_NOTE
from chat.models import Session
from gateway.fake import turn
from gateway.types import BlockedBySafetyFilter, Turn
from jobs.models import Job

from .conftest import PLAN, READABLE, ScriptedProducer, chat, given_to_the_producer

pytestmark = pytest.mark.django_db(transaction=True)

REFUSED = BlockedBySafetyFilter("content_policy_violation")


@pytest.fixture(autouse=True)
def _carries_on(settings: Settings) -> None:
    """The producer carries on by itself, as it does outside tests, with no time waited."""
    settings.PRODUCER_MOST_NUDGES = 2
    settings.PRODUCER_FILTER_RETRY_SECONDS = [0, 0]


@pytest.fixture
def no_nudges(settings: Settings) -> None:
    """Only the filter's retries: a test of them, asked before the page is read, scripts no
    turn for a nudge."""
    settings.PRODUCER_MOST_NUDGES = 0


def _planned(producer: ScriptedProducer, product_page_url: str, *after: Any) -> None:
    """The page read and the ad planned in one message, the producer then going to stop with
    every scene unmade, and taking `after`."""
    producer.fake_model.respond("check_page", READABLE)
    producer.fake_model.respond("plan_ad", PLAN)
    producer.told(
        f"Make an ad for {product_page_url}",
        ("read_page", {"link": product_page_url, "target_seconds": None, "why": "To start."}),
        ("plan_ad", {"why": "The page is read."}),
        *after,
    )


def _nudges(session_id: str) -> list[str]:
    return list(
        SystemNote.objects.filter(session_id=session_id, kind=SystemNote.Kind.NUDGE).values_list(
            "text", flat=True
        )
    )


def _kinds(*names: str) -> list[str]:
    job = Job.objects.get()
    return [warning["kind"] for warning in job.warnings if warning.get("kind") in names]


def test_a_producer_that_stops_with_the_ad_unfinished_is_nudged_and_carries_on(
    producer: ScriptedProducer,
    product_page_url: str,
    api: APIClient,
    session_id: str,
    sentry_events: list[dict[str, Any]],
) -> None:
    _planned(
        producer,
        product_page_url,
        "Here's the plan.",
        # Nudged, it carries on with a tool.
        ("cost_so_far", {"why": "Nudged to carry on."}),
        "Carrying on with the ad.",
        # Nudged again, it tells the shop owner why it can't: that answers the nudge.
        "I need your go-ahead on the plan before I spend money on clips.",
    )

    [first, second] = _nudges(session_id)
    assert first == (
        "The clips of scenes 1, 2 and 3 isn't made yet, and nothing is running or waiting on "
        "the shop owner. Carry on, or tell the shop owner in one short message why you can't. "
        "If you are waiting on the shop owner's answer to something you asked, end your turn "
        "without saying anything."
    )
    assert second == first
    # The nudge is given to the producer as a note from the system, never shown to the owner.
    assert f"{SYSTEM_NOTE} {first}" in str(given_to_the_producer(4))
    shown = chat(api, session_id)
    assert all(first not in text for _, text in shown)
    assert shown[-1] == (
        "agent",
        "I need your go-ahead on the plan before I spend money on clips.",
    )
    assert _kinds("producer_nudged") == ["producer_nudged", "producer_nudged"]
    assert [event["fingerprint"] for event in sentry_events].count(
        ["notice", "producer_nudged"]
    ) == 2


def test_a_nudge_answered_with_words_alone_is_not_followed_by_another(
    producer: ScriptedProducer, product_page_url: str, session_id: str
) -> None:
    _planned(producer, product_page_url, "Here's the plan.", "I'll wait for your yes on the plan.")

    assert len(_nudges(session_id)) == 1


def test_after_two_nudges_with_no_end_the_owner_is_told_plainly_that_it_stopped(
    producer: ScriptedProducer, product_page_url: str, api: APIClient, session_id: str
) -> None:
    producer.expect_stop()
    _planned(
        producer,
        product_page_url,
        turn(says="Here's the plan."),
        turn(calls=[("cost_so_far", {"why": "Nudged."})]),
        turn(),
        turn(calls=[("cost_so_far", {"why": "Nudged again."})]),
        turn(),
    )

    assert len(_nudges(session_id)) == 2
    assert chat(api, session_id)[-1] == (
        "notice",
        "I stopped before scenes 1, 2 and 3 were made. Send me any message and I'll pick it up.",
    )
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert messages[-1]["level"] == "problem"


def test_no_nudge_while_the_shop_owner_is_asked_something(
    producer: ScriptedProducer, product_page_url: str, session_id: str
) -> None:
    _planned(
        producer,
        product_page_url,
        turn(
            calls=[
                (
                    "ask_owner",
                    {"question": "Should the presenter be a woman?", "why": "Not on the page."},
                )
            ]
        ),
    )

    assert _nudges(session_id) == []


def test_no_nudge_while_the_page_panel_waits_for_the_shop_owners_page(
    producer: ScriptedProducer, httpserver: HTTPServer, session_id: str
) -> None:
    httpserver.expect_request("/products/gone").respond_with_data("Not here", status=404)
    gone = httpserver.url_for("/products/gone")

    producer.told(
        f"Make an ad for {gone}",
        ("read_page", {"link": gone, "target_seconds": None, "why": "To start."}),
        "I'll make the ad once I have the page.",
    )

    assert _nudges(session_id) == []


def test_no_nudge_while_the_shop_owner_paused(
    producer: ScriptedProducer, product_page_url: str, session_id: str
) -> None:
    def paused_as_it_replies() -> Turn:
        # Pressed as the model answered: the producer's next look finds the run paused.
        Session.objects.filter(pk=session_id).update(paused_at=datetime.now(UTC))
        return turn(says="Here's the plan.")

    _planned(producer, product_page_url, paused_as_it_replies)

    assert _nudges(session_id) == []


def test_a_turn_the_filter_stopped_is_taken_again_and_the_ad_carries_on_unseen(
    no_nudges: None,
    producer: ScriptedProducer,
    page_read: str,
    api: APIClient,
    session_id: str,
    sentry_events: list[dict[str, Any]],
) -> None:
    before = chat(api, session_id)

    producer.told("Tell me what it costs so far", REFUSED, "It has cost $0.10 so far.")

    assert chat(api, session_id) == [
        *before,
        ("user", "Tell me what it costs so far"),
        ("agent", "It has cost $0.10 so far."),
    ]
    assert _kinds("producer_retried") == ["producer_retried"]
    assert ["notice", "producer_retried"] in [event["fingerprint"] for event in sentry_events]
    # Retried once with the same conversation: no note yet.
    assert not SystemNote.objects.filter(kind=SystemNote.Kind.FILTER_RETRY).exists()


def test_the_second_retry_adds_a_note_to_carry_on(
    no_nudges: None, producer: ScriptedProducer, page_read: str
) -> None:
    producer.told("Carry on", REFUSED, REFUSED, "Carrying on.")

    assert f"{SYSTEM_NOTE} {FILTER_RETRY_NOTE}" in str(given_to_the_producer(5))
    assert _kinds("producer_retried") == ["producer_retried", "producer_retried"]


def test_after_two_retries_the_owner_is_told_plainly_never_to_start_a_new_session(
    no_nudges: None, producer: ScriptedProducer, page_read: str, api: APIClient, session_id: str
) -> None:
    producer.expect_stop()

    producer.told("Carry on", REFUSED, REFUSED, REFUSED)

    shown = chat(api, session_id)
    assert shown[-1] == ("notice", FILTER_KEPT_STOPPING)
    assert not any("new session" in text for _, text in shown)
    assert STOPPED != FILTER_KEPT_STOPPING
    assert _kinds("producer_gave_up") == ["producer_gave_up"]
