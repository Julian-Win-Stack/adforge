"""The total the producer tells the shop owner is what the chat has cost, its own turn that
tells it included (round 7 unlisted, item 6). Driven through the chat, with the producer's
model faked at the gateway."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db.models import Sum
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall

from .conftest import chat

pytestmark = pytest.mark.django_db(transaction=True)


def spent() -> Decimal:
    return ModelCall.objects.aggregate(cost=Sum("cost_usd"))["cost"] or Decimal(0)


def test_the_total_told_is_what_the_chat_cost_with_the_turn_that_tells_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    before = spent()
    fake_model.respond("produce", turn(says="Your ad has cost [total cost] so far."))

    say("What has it cost?")

    assert spent() > before
    assert chat(api, session_id)[-1] == (
        "agent",
        f"Your ad has cost ${spent().quantize(Decimal('0.01'))} so far.",
    )
