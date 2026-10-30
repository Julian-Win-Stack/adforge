"""Notices: what code records when a step falls back or fails, and tells the shop owner when
they can act on it. A notice for the owner is shown with its level; every notice is kept with
the job, and none is given to a model as part of the conversation."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import notices
from jobs.models import Job
from jobs.notices import post_notice
from jobs.work import NO_FIRECRAWL

from .conftest import PLAN, chat, given_to_the_producer, handoffs, labelled_by_the_call

pytestmark = pytest.mark.django_db(transaction=True)

FELL_BACK = (
    "Firecrawl couldn't open the page (timed out after 5 min). Read it with the plain "
    "download instead, so some text hidden in tabs may be missing."
)
NORMAL = "The shop has no official record of the product, which is normal on many pages."
ASKED = "I couldn't find a photo of your product on the page. Attach one and I'll make the ad."
THANKS = "Thanks, I have your 1 photo."


def test_only_a_notice_for_the_owner_is_shown_with_its_level_and_every_one_is_kept(
    api: APIClient, session_id: str, page_read: str
) -> None:
    job = Job.objects.get()

    post_notice(job, notices.FIRECRAWL_FAILED, FELL_BACK, Message.Level.PROBLEM)
    post_notice(job, notices.NO_PRODUCT_PHOTO, ASKED, Message.Level.PROBLEM)
    post_notice(job, notices.REPLY, THANKS, Message.Level.INFO)

    # Tests read pages with the plain download, and its two declared photos are labelled
    # for the library by a call of their own: both are the developer's to know.
    assert chat(api, session_id) == [
        ("user", f"Make an ad for {page_read}"),
        ("agent", "I read your mug's page."),
        ("notice", ASKED),
        ("notice", THANKS),
    ]
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert [(message["seq"], message["role"], message["level"]) for message in shown] == [
        (1, "user", ""),
        (2, "agent", ""),
        (3, "notice", "problem"),
        (4, "notice", "info"),
    ]
    job.refresh_from_db()
    assert [(warning["kind"], warning["level"], warning["text"]) for warning in job.warnings] == [
        ("no_firecrawl", "problem", NO_FIRECRAWL),
        ("labelled_for_the_picker", "info", labelled_by_the_call(2)),
        ("firecrawl_failed", "problem", FELL_BACK),
        ("no_product_photo", "problem", ASKED),
        ("reply", "info", THANKS),
    ]
    assert all(warning["at"] for warning in job.warnings)
    assert api.get(f"/api/sessions/{session_id}/warnings/").json() == job.warnings


def test_a_notice_is_not_in_the_producers_conversation_nor_the_planners(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    post_notice(Job.objects.get(), notices.NO_PRODUCT_PHOTO, ASKED, Message.Level.PROBLEM)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)

    say("Plan it")

    given = given_to_the_producer(3)
    assert [each["text"] for each in given if each["kind"] == "said"] == [
        f"Make an ad for {page_read}",
        "I read your mug's page.",
        "Plan it",
    ]
    assert ASKED not in str(given)
    [planned] = handoffs("plan_ad")
    assert [(each["by"], each["text"]) for each in planned["conversation"]] == [
        ("user", f"Make an ad for {page_read}"),
        ("producer", "I read your mug's page."),
        ("user", "Plan it"),
    ]


def test_a_job_with_no_session_keeps_the_notice_without_a_chat_to_post_in() -> None:
    job = Job.objects.create(product_url="https://shop.example/products/mug")

    post_notice(job, notices.NO_PRODUCT_PHOTO, ASKED, Message.Level.PROBLEM)

    job.refresh_from_db()
    assert [warning["text"] for warning in job.warnings] == [ASKED]
    assert not Message.objects.exists()


def test_notices_posted_through_two_copies_of_the_same_job_are_both_kept() -> None:
    first = Job.objects.create(product_url="https://shop.example/products/mug")
    second = Job.objects.get(pk=first.pk)

    post_notice(first, notices.FIRECRAWL_FAILED, FELL_BACK, Message.Level.PROBLEM)
    post_notice(second, notices.PHOTO_PICKER, NORMAL, Message.Level.INFO)

    stored = Job.objects.get(pk=first.pk)
    assert [warning["text"] for warning in stored.warnings] == [FELL_BACK, NORMAL]
    assert second.warnings == stored.warnings
