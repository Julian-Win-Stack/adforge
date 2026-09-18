from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from adforge import file_store
from gateway.fake import FakeModel
from gateway.gateway import UnreadableImage, call_model
from gateway.models import ModelCall
from gateway.types import Image, UnusableReply
from jobs.tasks import PageCheck, PageCheckHandoff

from .conftest import openai_reply

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


def test_an_image_in_a_format_models_cant_read_is_refused_before_the_model_is_called(
    fake_model: FakeModel,
) -> None:
    key = file_store.save("photos/side.heic", b"ftypheic")

    # The fake has nothing scripted, so reaching it would raise AssertionError instead.
    with pytest.raises(UnreadableImage) as refused:
        call_model(
            job=None,
            purpose="check_page",
            instructions="Check the page.",
            handoff=PageCheckHandoff(
                product_url="https://shop.example/products/mug",
                page_url="https://shop.example/products/mug",
                page_text="Stoneware Mug",
                photo_count=1,
            ),
            output=PageCheck,
            images=[Image(label="Photo 1", key=key)],
        )
    assert str(refused.value) == (
        "Photo 1 (photos/side.heic) is image/heic, which models can't read"
    )
    assert not ModelCall.objects.exists()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            openai_reply({"type": "refusal", "refusal": "I can't help with that."}),
            id="refused",
        ),
        pytest.param(
            openai_reply(
                {"type": "output_text", "text": '{"decision": "read', "annotations": []},
                status="incomplete",
            ),
            id="cut off mid-answer",
        ),
    ],
)
def test_a_reply_that_cant_be_used_still_records_what_it_cost(
    openai_server: Callable[[Any], None], reply: dict[str, Any]
) -> None:
    openai_server(reply)

    with pytest.raises(UnusableReply):
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
