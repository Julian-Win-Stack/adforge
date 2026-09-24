"""Planning the ad, driven through the chat. The producer's model is faked at the gateway to
read the page and call plan_ad, and each test checks what the tool handed back, what was
stored and what was paid for."""

import base64
import io
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import PIL.Image
import pytest
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job
from jobs.tasks import keep_photo

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    PLAN,
    READABLE,
    openai_answer,
    openai_turn,
    paid_for,
    picture,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)


def planning(
    fake_model: FakeModel, link: str, reply: dict[str, Any], *, target_seconds: int | None = None
) -> None:
    """Script the producer to read `link` and plan the ad, with the planner answering `reply`."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": target_seconds})]),
        turn(says="Now I'll plan the ad.", calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", reply)


def planning_through_openai(
    openai_server: Callable[..., None], link: str, reply: dict[str, Any]
) -> None:
    """The same, through the real OpenAI code talking to a stand-in OpenAI server."""
    openai_server(
        openai_turn("", ("call_1", "read_page", {"link": link, "target_seconds": None})),
        openai_answer(READABLE),
        openai_turn("", ("call_2", "plan_ad", {})),
        openai_answer(reply),
        openai_turn("Here's the plan."),
    )


def a_plan_with(**changes: Any) -> dict[str, Any]:
    """PLAN with some of its plan's fields replaced."""
    return {**PLAN, "plan": {**PLAN["plan"], **changes}}


def plan_ad_result() -> str:
    (result,) = results_of("plan_ad")
    return result


# --- What is kept from the plan -----------------------------------------------------------


def test_the_plan_and_its_scenes_are_stored_with_the_job(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning(fake_model, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert [(scene.number, scene.line, scene.status) for scene in job.scenes.all()] == [
        (1, "Meet the Stoneware Mug from Kiln & Co.", "planned"),
        (2, "Hand-thrown, holds 350 ml, and dishwasher safe.", "planned"),
        (3, "Yours for $24.00.", "planned"),
    ]
    assert (job.person_looks, job.person_voice) == (
        "A potter in her thirties in a linen apron, in a sunny workshop.",
        "A warm, relaxed woman in her thirties with a soft British accent.",
    )


def test_the_products_colour_and_the_photos_showing_it_are_stored(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    # The planner marks photo 2 alone, so marking the first photo by default can't pass.
    planning(fake_model, product_page_url, a_plan_with(colour_photos=[2]))

    say(f"Make an ad for {product_page_url}")

    job = Job.objects.get()
    assert job.product_colour == "sage green"
    assert [(photo.position, photo.shows_product_colour) for photo in job.photos.all()] == [
        (1, False),
        (2, True),
    ]
    assert "The product's colour: sage green, shown in photos 2." in plan_ad_result()


# --- What the planner is given --------------------------------------------------------------


def the_plan_message(httpserver: HTTPServer) -> dict[str, Any]:
    """The one message the plan's request sent the model: after the producer's first turn,
    the page check and its second turn."""
    _, _, _, plan, _ = [
        request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"
    ]
    [message] = plan["input"]
    return message  # type: ignore[no-any-return]


def shown(image: dict[str, Any]) -> tuple[str, PIL.Image.Image]:
    """The media type and picture an input_image part carries."""
    header, data = image["image_url"].split(",", 1)
    assert header.startswith("data:") and header.endswith(";base64")
    return header.removeprefix("data:").removesuffix(";base64"), PIL.Image.open(
        io.BytesIO(base64.b64decode(data))
    )


def test_the_planner_is_handed_the_page_text_the_target_the_photo_count_and_the_conversation(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    planning(fake_model, product_page_url, PLAN, target_seconds=12)

    say(f"Make me a 12 second ad for {product_page_url}")

    handoff = ModelCall.objects.get(purpose="plan_ad").handoff
    assert "$24.00" in handoff["page_text"]
    # Stock is only in the page's structured data, never in the words a visitor sees.
    assert "InStock" in handoff["page_text"]
    del handoff["page_text"]
    assert handoff == {
        "product_url": product_page_url,
        "target_seconds": 12,
        "photo_count": 2,
        # The conversation so far, the producer's own words too, so an answer is read next to its
        # question.
        "conversation": [
            {"by": "user", "text": f"Make me a 12 second ad for {product_page_url}"},
            {"by": "producer", "text": "Now I'll plan the ad."},
        ],
    }


def test_the_planner_is_shown_every_product_photo_by_its_number(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    message = the_plan_message(httpserver)
    assert message["role"] == "user"
    handoff, *photos = message["content"]
    labels, images = photos[0::2], photos[1::2]
    assert labels == [
        {"type": "input_text", "text": "Photo 1"},
        {"type": "input_text", "text": "Photo 2"},
    ]
    assert [(image["type"], image["detail"]) for image in images] == [("input_image", "low")] * 2
    # The front photo is sage green and the side one cream, so each label is on its own photo.
    assert [
        (media_type, image_shown.getpixel((0, 0))) for media_type, image_shown in map(shown, images)
    ] == [
        ("image/png", (143, 170, 140)),
        ("image/png", (236, 229, 206)),
    ]
    # The record says which photos were shown by their keys in the file store, not their bytes.
    call = ModelCall.objects.get(purpose="plan_ad")
    job_id = Job.objects.get().pk
    assert call.images == [
        {"label": "Photo 1", "key": f"jobs/{job_id}/photos/1.png"},
        {"label": "Photo 2", "key": f"jobs/{job_id}/photos/2.png"},
    ]
    assert handoff["type"] == "input_text"
    assert json.loads(handoff["text"]) == call.handoff


def test_the_planner_is_shown_each_photo_shrunk_to_fit_512_pixels_and_the_kept_ones_are_unchanged(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    planning_through_openai(openai_server, product_page_url, PLAN)

    say(f"Make an ad for {product_page_url}")

    _, *photos = the_plan_message(httpserver)["content"]
    # The front photo, 400 x 300, already fits. The side one, 1600 x 1200, is shrunk to fit.
    assert [shown(image)[1].size for image in photos[1::2]] == [(400, 300), (512, 384)]
    kept = Job.objects.get().photos.all()
    assert [file_store.read(photo.file) for photo in kept] == [MUG_FRONT, MUG_SIDE]


# --- A plan that can't be used ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "broken_rule"),
    [
        pytest.param(a_plan_with(scenes=[]), "List should have at least 1 item", id="no scenes"),
        pytest.param(
            a_plan_with(scenes=[{"line": "  "}]),
            "A scene's line can't be empty.",
            id="a scene with nothing to say",
        ),
        pytest.param(a_plan_with(product_colour=" "), "This can't be empty.", id="no colour"),
        pytest.param(
            a_plan_with(person_looks=" "), "This can't be empty.", id="no look for the person"
        ),
        pytest.param(
            a_plan_with(person_voice=""), "This can't be empty.", id="no voice for the person"
        ),
        pytest.param(
            a_plan_with(colour_photos=[]),
            "List should have at least 1 item",
            id="no photo showing the colour",
        ),
        pytest.param(
            a_plan_with(colour_photos=[1, 3]),
            "There's no photo 3: the job has 2.",
            id="a photo after the last one",
        ),
        pytest.param(
            a_plan_with(colour_photos=[0]),
            "There's no photo 0: the job has 2.",
            id="a photo before the first one",
        ),
        pytest.param(
            a_plan_with(colour_photos=["1"]),
            "Input should be a valid integer",
            id="a photo number given as text",
        ),
        pytest.param(
            {**PLAN, "plan": None},
            'A "plan" decision needs a plan and no question.',
            id="a plan decision with no plan",
        ),
        pytest.param(
            {**PLAN, "decision": "ask", "question": "Which size?"},
            'An "ask" decision needs a question and no plan.',
            id="a question and a plan",
        ),
    ],
)
def test_a_plan_that_breaks_the_rules_is_handed_back_keeps_nothing_and_is_still_paid_for(
    openai_server: Callable[..., None],
    product_page_url: str,
    say: Callable[..., None],
    reply: dict[str, Any],
    broken_rule: str,
) -> None:
    planning_through_openai(openai_server, product_page_url, reply)

    say(f"Make an ad for {product_page_url}")

    result = plan_ad_result()
    assert result.startswith(
        "Failed: a model's answer couldn't be used (gpt-5.6-sol gave an answer for plan_ad "
        "that could not be read:"
    )
    assert broken_rule in result
    job = Job.objects.get()
    assert not job.scenes.exists()
    assert (job.product_colour, job.person_looks, job.person_voice) == ("", "", "")
    assert not job.photos.filter(shows_product_colour=True).exists()
    # The bad plan is still paid for: 1,200 x $4.00/M in + 300 x $20.00/M out.
    plan_call = ModelCall.objects.get(purpose="plan_ad")
    assert (plan_call.outcome, plan_call.cost_usd) == ("failed", Decimal("0.0108"))
    # Nothing is made from a plan that broke the rules.
    assert paid_for() == ["check_page", "plan_ad"]


def test_a_photo_models_cant_read_is_handed_back_saying_why_and_the_planner_isnt_paid(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # A job whose photos were kept before they had to be PNG, JPEG, WebP or GIF has a BMP.
    # The chat no longer keeps one, so it is added by hand.
    job = Job.objects.get()
    bmp = picture(40, 30, (143, 170, 140), "BMP")
    keep_photo(job, 3, bmp, "image/bmp", source_url="https://shop.example/mug.bmp")
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="I couldn't plan it."))

    say("Plan it")

    assert plan_ad_result() == (
        f"Failed: Photo 3 (jobs/{job.pk}/photos/3.bmp) is image/bmp, which models can't read. "
        "Only PNG, JPEG, WebP or GIF pictures can be shown to a model."
    )
    assert paid_for() == ["check_page"]
    assert not job.scenes.exists()
