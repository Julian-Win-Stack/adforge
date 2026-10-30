"""Who sees a notice (spec #272, Parts 1 and 3): the shop owner's chat shows only what they
can act on; every other fallback and failure leaves the chat, but is kept in job.warnings and
sent to Sentry, grouped by its kind and tagged with what the work was on."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from chat.models import Message, Session
from jobs import notices
from jobs.models import Job
from jobs.notices import post_notice
from jobs.work import NO_FIRECRAWL

from .conftest import chat

pytestmark = pytest.mark.django_db(transaction=True)

# The kinds the spec's "what stays in the chat" table keeps for the shop owner.
FOR_THE_OWNER = {
    "page_unreadable",
    "no_product_photo",
    "paste_refused",
    "paste_kept",
    "paste_wants_photos",
    "panel_closed",
    "part_needs_yes",
    "part_not_drawn",
    "agent_asked",
    "tries_to_pick",
    "check_it_yourself",
    "owner_file_unusable",
    "reply",
    "producer_stopped",
}


def test_only_the_kinds_the_owner_can_act_on_are_for_the_owner() -> None:
    names = [kind.name for kind in notices.KINDS]
    assert len(names) == len(set(names)), "each kind has a name of its own"
    assert {kind.name for kind in notices.KINDS if kind.for_owner} == FOR_THE_OWNER


@pytest.mark.parametrize("kind", notices.KINDS, ids=lambda kind: kind.name)
def test_each_kind_lands_in_the_chat_or_the_developer_record_never_both_never_neither(
    kind: notices.Kind, sentry_events: list[dict[str, Any]]
) -> None:
    session = Session.objects.create(name="R8 a mug")
    job = Job.objects.create(session=session, product_url="https://shop.example/mug")

    post_notice(job, kind, "What happened.", Message.Level.PROBLEM)

    shown = list(session.messages.values_list("text", flat=True))
    job.refresh_from_db()
    assert [warning["kind"] for warning in job.warnings] == [kind.name]
    if kind.for_owner:
        assert shown == ["What happened."]
    else:
        assert shown == []
    sent = [event["fingerprint"] for event in sentry_events]
    assert sent == ([["notice", kind.name]] if kind.failure else [])


def test_a_fallback_leaves_the_owners_chat_but_is_kept_and_sent_to_sentry_by_kind(
    sentry_events: list[dict[str, Any]],
    api: APIClient,
    session_id: str,
    page_read: str,
) -> None:
    # Tests read pages with the plain download, a fallback the developer is told of.
    shown = chat(api, session_id)
    assert all(NO_FIRECRAWL not in text for _, text in shown)
    assert [role for role, _ in shown] == ["user", "agent"]
    job = Job.objects.get()
    assert (notices.NO_FIRECRAWL.name, "problem", NO_FIRECRAWL) in [
        (warning["kind"], warning["level"], warning["text"]) for warning in job.warnings
    ]
    [event] = [e for e in sentry_events if e["fingerprint"] == ["notice", "no_firecrawl"]]
    assert event["level"] == "warning"
    assert event["extra"]["text"] == NO_FIRECRAWL
    assert event["tags"] == {
        "kind": "no_firecrawl",
        "session": session_id,
        "ad": Session.objects.get(pk=session_id).name,
        "job": str(job.pk),
        "step": "producer",
    }


def test_the_same_kind_in_other_words_is_one_group(
    sentry_events: list[dict[str, Any]],
) -> None:
    session = Session.objects.create(name="R8 a mug")
    job = Job.objects.create(session=session, product_url="https://shop.example/mug")

    for scene in (1, 2, 3):
        post_notice(
            job,
            notices.RECOLOUR_FAILED,
            f"Picture {scene} couldn't be recoloured red, for scene {scene}.",
            Message.Level.PROBLEM,
            scene=scene,
        )

    assert [event["fingerprint"] for event in sentry_events] == [["notice", "recolour_failed"]] * 3
    assert [event["tags"]["scene"] for event in sentry_events] == ["1", "2", "3"]


def test_a_failure_in_a_traced_turn_links_its_langfuse_trace(
    page_read: str,
    sentry_events: list[dict[str, Any]],
    traces: Any,
) -> None:
    del page_read
    from adforge import tracing

    with tracing.message_work("producer", session_id="s", message="m", trace_id="", woken_by=[]):
        post_notice(Job.objects.get(), notices.CARD_PICTURE, "No card.", Message.Level.PROBLEM)

    [event] = [e for e in sentry_events if e["fingerprint"] == ["notice", "card_picture"]]
    assert event["tags"]["langfuse_trace"]
    assert event["contexts"]["langfuse"]["trace_id"] == event["tags"]["langfuse_trace"]


def test_sentry_starts_only_with_a_dsn_with_its_environment_and_no_personal_data() -> None:
    import sentry_sdk  # noqa: TID251 (checks how the app starts it)

    from adforge import monitoring

    monitoring.start("", "azure")
    assert not sentry_sdk.get_client().is_active()
    try:
        monitoring.start("https://public@sentry.invalid/1", "azure")
        client = sentry_sdk.get_client()
        assert client.is_active()
        assert client.options["environment"] == "azure"
        assert not client.options["send_default_pii"]
        assert client.options["traces_sample_rate"] == 0
        assert {"django", "celery"} <= set(client.integrations)
    finally:
        sentry_sdk.get_client().close()
        # Back to no client at all, as with no DSN.
        sentry_sdk.get_global_scope().set_client(None)
