import base64
import io
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import PIL.Image
import pytest
from pydantic import ValidationError
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import FakeModel
from gateway.gateway import UnreadableImage, call_model
from gateway.models import ModelCall
from gateway.types import Image, UnusableReply
from jobs.tasks import PageCheck, PageCheckHandoff

from .conftest import READABLE, openai_answer, openai_reply, picture

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


def _check_page(images: list[Image]) -> None:
    call_model(
        job=None,
        purpose="check_page",
        instructions="Check the page.",
        handoff=PageCheckHandoff(
            product_url="https://shop.example/products/mug",
            page_url="https://shop.example/products/mug",
            page_text="Stoneware Mug",
            photo_count=len(images),
        ),
        output=PageCheck,
        images=images,
    )


@pytest.mark.parametrize(
    ("key", "content", "why"),
    [
        pytest.param(
            "photos/side.bmp",
            picture(40, 30, (143, 170, 140), "BMP"),
            "is image/bmp, which models can't read",
            id="another format",
        ),
        pytest.param(
            "photos/side.png",
            b"\x89PNG cut off",
            "can't be opened as a picture",
            id="not a picture",
        ),
    ],
)
def test_an_image_models_cant_read_is_refused_before_the_model_is_called(
    fake_model: FakeModel, key: str, content: bytes, why: str
) -> None:
    file_store.save(key, content)

    # The fake has nothing scripted, so reaching it would raise AssertionError instead.
    with pytest.raises(UnreadableImage) as refused:
        _check_page([Image(label="Photo 1", key=key)])
    assert str(refused.value) == f"Photo 1 ({key}) {why}"
    assert not ModelCall.objects.exists()


def test_a_photo_a_phone_saved_sideways_is_shown_the_right_way_up(
    httpserver: HTTPServer, openai_server: Callable[..., None]
) -> None:
    # Phones store a portrait photo as landscape pixels plus a note saying "turn it 90 degrees
    # clockwise to view". The model is shown the photo as a person would see it: tall.
    sideways = PIL.Image.new("RGB", (40, 20), (143, 170, 140))
    note = PIL.Image.Exif()
    note[0x0112] = 6  # Orientation: turn 90 degrees clockwise to view.
    file = io.BytesIO()
    sideways.save(file, format="JPEG", exif=note)
    key = file_store.save("photos/portrait.jpg", file.getvalue())
    openai_server(openai_answer(READABLE))

    _check_page([Image(label="Photo 1", key=key)])

    [request] = [request for request, _ in httpserver.log if request.path == "/v1/responses"]
    [message] = request.get_json()["input"]
    image_url = message["content"][2]["image_url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    shown = PIL.Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1])))
    assert shown.size == (20, 40)


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
