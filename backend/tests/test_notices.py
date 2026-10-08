"""Notices: what code posts in the chat when a step falls back or fails. A notice is shown
to the user with its level, kept with the job, and never given to a model as part of the
conversation."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs.models import Job
from jobs.notices import post_notice
from jobs.work import NO_FIRECRAWL

from .conftest import PLAN, chat, given_to_the_producer, handoffs

pytestmark = pytest.mark.django_db(transaction=True)

FELL_BACK = (
    "Firecrawl couldn't open the page (timed out after 5 min). Read it with the plain "
    "download instead, so some text hidden in tabs may be missing."
)
NORMAL = "The shop has no official record of the product, which is normal on many pages."


def test_a_notice_is_shown_in_the_chat_with_its_level_in_order_and_kept_with_the_job(
    api: APIClient, session_id: str, page_read: str
) -> None:
    job = Job.objects.get()

    post_notice(job, FELL_BACK, Message.Level.PROBLEM)
    post_notice(job, NORMAL, Message.Level.INFO)

    assert chat(api, session_id) == [
        ("user", f"Make an ad for {page_read}"),
        # Tests read pages with the plain download, which says so.
        ("notice", NO_FIRECRAWL),
        ("agent", "I read your mug's page."),
        ("notice", FELL_BACK),
        ("notice", NORMAL),
    ]
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert [(message["seq"], message["role"], message["level"]) for message in shown] == [
        (1, "user", ""),
        (2, "notice", "problem"),
        (3, "agent", ""),
        (4, "notice", "problem"),
        (5, "notice", "info"),
    ]
    job.refresh_from_db()
    assert [(warning["level"], warning["text"]) for warning in job.warnings] == [
        ("problem", NO_FIRECRAWL),
        ("problem", FELL_BACK),
        ("info", NORMAL),
    ]
    assert all(warning["at"] for warning in job.warnings)


def test_a_notice_is_not_in_the_producers_conversation_nor_the_planners(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    post_notice(Job.objects.get(), FELL_BACK, Message.Level.PROBLEM)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)

    say("Plan it")

    given = given_to_the_producer(3)
    assert [each["text"] for each in given if each["kind"] == "said"] == [
        f"Make an ad for {page_read}",
        "I read your mug's page.",
        "Plan it",
    ]
    assert FELL_BACK not in str(given)
    [planned] = handoffs("plan_ad")
    assert [(each["by"], each["text"]) for each in planned["conversation"]] == [
        ("user", f"Make an ad for {page_read}"),
        ("producer", "I read your mug's page."),
        ("user", "Plan it"),
    ]


def test_a_job_with_no_session_keeps_the_notice_without_a_chat_to_post_in() -> None:
    job = Job.objects.create(product_url="https://shop.example/products/mug")

    post_notice(job, FELL_BACK, Message.Level.PROBLEM)

    job.refresh_from_db()
    assert [warning["text"] for warning in job.warnings] == [FELL_BACK]
    assert not Message.objects.exists()


def test_notices_posted_through_two_copies_of_the_same_job_are_both_kept() -> None:
    first = Job.objects.create(product_url="https://shop.example/products/mug")
    second = Job.objects.get(pk=first.pk)

    post_notice(first, FELL_BACK, Message.Level.PROBLEM)
    post_notice(second, NORMAL, Message.Level.INFO)

    stored = Job.objects.get(pk=first.pk)
    assert [warning["text"] for warning in stored.warnings] == [FELL_BACK, NORMAL]
    assert second.warnings == stored.warnings
