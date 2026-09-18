from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError
from pytest_django import Settings
from pytest_httpserver import HTTPServer

from gateway.fake import FakeModel
from gateway.gateway import call_model, use_model
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider
from gateway.types import UnusableReply
from jobs.tasks import PageCheck, PageCheckHandoff

pytestmark = pytest.mark.django_db


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")
def test_a_bad_handoff_is_refused_before_the_model_is_called(fake_model: FakeModel) -> None:
    # model_construct skips validation, like a caller that built the handoff carelessly.
    bad = PageCheckHandoff.model_construct(
        product_url="https://shop.example/products/mug",
        page_url="https://shop.example/products/mug",
        page_text="Stoneware Mug",
        photo_count=2.0,  # type: ignore[arg-type]  # A decimal where a whole number belongs.
    )

    # The fake has nothing scripted, so reaching it would raise AssertionError instead.
    with pytest.raises(ValidationError):
        call_model(
            job=None,
            purpose="check_page",
            instructions="Check the page.",
            handoff=bad,
            output=PageCheck,
        )
    assert not ModelCall.objects.exists()


def _openai_reply(content: dict[str, Any], status: str = "completed") -> dict[str, Any]:
    """A Responses API reply as OpenAI sends it, billed for 1,200 tokens in and 300 out."""
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_789_000_000,
        "model": "gpt-5-mini",
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": status,
                "content": [content],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1_200,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 300,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1_500,
        },
    }


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            _openai_reply({"type": "refusal", "refusal": "I can't help with that."}),
            id="refused",
        ),
        pytest.param(
            _openai_reply(
                {"type": "output_text", "text": '{"decision": "read', "annotations": []},
                status="incomplete",
            ),
            id="cut off mid-answer",
        ),
    ],
)
def test_a_reply_that_cant_be_used_still_records_what_it_cost(
    httpserver: HTTPServer, settings: Settings, reply: dict[str, Any]
) -> None:
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")
    httpserver.expect_request("/v1/responses", method="POST").respond_with_json(reply)

    with use_model(OpenAIProvider()), pytest.raises(UnusableReply):
        call_model(
            job=None,
            purpose="check_page",
            instructions="Check the page.",
            handoff=PageCheckHandoff(
                product_url="https://shop.example/products/mug",
                page_url="https://shop.example/products/mug",
                page_text="Stoneware Mug",
                photo_count=2,
            ),
            output=PageCheck,
        )

    call = ModelCall.objects.get()
    assert call.outcome == "failed"
    assert (call.input_tokens, call.output_tokens) == (1_200, 300)
    # gpt-5-mini: 1,200 x $0.25/M in + 300 x $2.00/M out = $0.0003 + $0.0006.
    assert call.cost_usd == Decimal("0.0009")
