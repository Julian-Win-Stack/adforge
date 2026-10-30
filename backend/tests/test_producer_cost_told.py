"""The total the producer tells the shop owner is what the chat has cost, its own turn that
tells it included (round 7 unlisted, item 6). Driven through the chat, with the producer's
model faked at the gateway."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db.models import Sum
from rest_framework.test import APIClient

from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall

from .conftest import chat

pytestmark = pytest.mark.django_db(transaction=True)


def spent(session_id: str) -> Decimal:
    calls = ModelCall.objects.filter(session_id=session_id)
    return calls.aggregate(cost=Sum("cost_usd"))["cost"] or Decimal(0)


def test_the_total_told_is_what_the_chat_cost_with_the_turn_that_tells_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # Another chat's spending isn't this ad's.
    other = Session.objects.create(name="Another ad")
    ModelCall.objects.create(
        session=other,
        purpose="produce",
        provider="fake",
        model="fake",
        attempt=1,
        handoff={},
        outcome=ModelCall.Outcome.SUCCEEDED,
        cost_usd=Decimal("5"),
        duration_ms=1,
    )
    before = spent(session_id)
    fake_model.respond("produce", turn(says="Your ad has cost [total cost] so far."))

    say("What has it cost?")

    assert spent(session_id) > before
    assert chat(api, session_id)[-1] == (
        "agent",
        f"Your ad has cost ${spent(session_id).quantize(Decimal('0.01'))} so far.",
    )


def test_a_question_carries_the_total_not_its_stand_in(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("ask_owner", {"question": "It has cost [total cost]. Shall I go on?"})]),
    )

    say("Go on")

    total = spent(session_id).quantize(Decimal("0.01"))
    assert chat(api, session_id)[-1] == ("agent", f"It has cost ${total}. Shall I go on?")
