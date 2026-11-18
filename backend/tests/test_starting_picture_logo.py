"""The logo sent with a scene's starting picture, through the chat."""

import io
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.scene_agent.product_logo import CROP_LINE, LOGO_SENTENCE
from agents.scene_forms import PRODUCT_DESIGN_LINE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job, ProducedItem, Scene

from .conftest import HeldSteps, WorkerStopped, handoffs, photo, recorded
from .library_photos import label, page_photo
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("scene_agent_ready", "picture_checked"),
]

PURPOSE = "describe_product_logo"
DESCRIPTION = "A small matte gold sun above the word SOL on the upper right."
FOUND = {"shown": True, "description": DESCRIPTION, "image": 2, "box": [250, 250, 750, 750]}
NO_LOGO = {"shown": False, "description": "", "image": 0, "box": []}
DRAW = turn(calls=[("draw_starting_picture", PICTURE_FORM)])
STOP = turn(calls=[("give_up", {"reason": "Wait for the owner."})])
SENTENCE = LOGO_SENTENCE.format(description=DESCRIPTION)


def start_scene(fake_model: FakeModel, say: Callable[..., None], scene: int = 2) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Draw it.", "quotes": ["go on"]}),
    )


def test_draw_sends_the_logo_from_an_unpicked_page_view(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    job = Job.objects.get()
    pick = job.library.get(number=1).file
    view = file_store.save("marked-side.png", photo(7))
    job.library.filter(number=2).update(file=view)
    fake_model.respond(PURPOSE, FOUND)
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say)
    run(fake_model, steps)

    [logo] = [request for request in fake_model.asked if request.purpose == PURPOSE]
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    record = picture.sent["logo"]
    assert record["image"] == 2
    assert record["source_file"] == view
    assert [image.key for image in logo.images] == [pick, view]
    assert [image.label for image in logo.images] == [
        photo["line"] for photo in picture.sent["photos"][:2]
    ]
    crop = record["crop_file"]
    assert draw["pictures"][:2] == [pick, view]
    assert draw["pictures"][-1] == crop
    assert picture.text == draw["prompt"]
    assert record["sentence"] == SENTENCE
    assert draw["prompt"] == logo.handoff.model_dump()["prompt"].replace(
        PRODUCT_DESIGN_LINE,
        f"{SENTENCE} {CROP_LINE.format(number=len(draw['pictures']))} {PRODUCT_DESIGN_LINE}",
    )
    assert crop not in [
        image["key"] for image in ModelCall.objects.get(purpose="check_scene_picture").images
    ]
    assert not job.library.filter(file=crop).exists()

    with PIL.Image.open(io.BytesIO(file_store.read(view))) as source:
        width, height = source.size
        # The model's middle half of the photo plus a quarter of that box on each side.
        expected = source.crop((width // 8, height // 8, width * 7 // 8, height * 7 // 8))
        expected = expected.resize((819, 1024), PIL.Image.Resampling.LANCZOS)
    with PIL.Image.open(io.BytesIO(file_store.read(crop))) as sent:
        assert sent.size == expected.size
        assert sent.tobytes() == expected.convert("RGB").tobytes()


def test_no_logo_leaves_the_words_and_pictures_as_they_were(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(PURPOSE, NO_LOGO)
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say)
    run(fake_model, steps)

    [vision] = handoffs(PURPOSE)
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert picture.sent["logo"] == {"status": "none"}
    assert draw["prompt"] == vision["prompt"]
    assert draw["pictures"] == [photo["file"] for photo in picture.sent["photos"]]
    assert not any(
        text.startswith("Scene 2's starting picture gets") for _, text in recorded(api, session_id)
    )


@pytest.mark.parametrize(
    ("answer", "status", "notice"),
    [
        (OutsideServiceDown("The vision service is down"), "call_failed", "vision call failed"),
        (
            BlockedBySafetyFilter("Refused", of_what_was_made=True),
            "refused",
            "vision safety filter refused",
        ),
        ({**FOUND, "image": 3}, "bad_photo", "was not a product photo sent"),
        ({**FOUND, "box": [250, 250, 250, 750]}, "bad_box", "unusable logo box"),
    ],
)
def test_logo_problems_leave_the_draw_running_and_record_a_notice(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    settings: Settings,
    answer: dict[str, Any] | Exception,
    status: str,
    notice: str,
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = True
    fake_model.respond(PURPOSE, answer, provider="azure")
    fake_model.respond(PURPOSE, FOUND, provider="openai")
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say)
    run(fake_model, steps)

    [vision] = handoffs(PURPOSE)
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert picture.sent["logo"]["status"] == status
    assert "crop_file" not in picture.sent["logo"]
    assert draw["pictures"] == [photo["file"] for photo in picture.sent["photos"]]
    assert draw["prompt"] == (
        vision["prompt"]
        if isinstance(answer, Exception)
        else vision["prompt"].replace(PRODUCT_DESIGN_LINE, f"{SENTENCE} {PRODUCT_DESIGN_LINE}")
    )
    assert any(notice in text for _, text in recorded(api, session_id))


def test_edit_uses_its_numbered_product_photos_and_keeps_the_crop_out_of_evidence(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(PURPOSE, FOUND, {**FOUND, "image": 3})
    fake_model.respond(
        "scene_agent",
        DRAW,
        turn(calls=[("edit_starting_picture", {**END_PICTURE, "picks": []})]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        STOP,
    )
    start_scene(fake_model, say)
    run(fake_model, steps)

    original, edited = ProducedItem.objects.filter(kind="starting_picture").order_by("version")
    assert original.sent is not None and edited.sent is not None
    crop = edited.sent["logo"]["crop_file"]
    first_crop = original.sent["logo"]["crop_file"]
    _, edit = handoffs("draw_scene_picture")
    _, vision = [request for request in fake_model.asked if request.purpose == PURPOSE]
    assert edit["pictures"] == [original.file, *(i.key for i in vision.images), crop]
    assert [i.label.split(":")[0] for i in vision.images] == ["Image 2", "Image 3"]
    assert edited.sent["logo"]["image"] == 3
    assert edited.sent["logo"]["source_file"] == edit["pictures"][2]
    assert edit["prompt"] == vision.handoff.model_dump()["prompt"].replace(
        PRODUCT_DESIGN_LINE,
        f"{SENTENCE} {CROP_LINE.format(number=4)} {PRODUCT_DESIGN_LINE}",
    )
    [end] = handoffs("make_end_picture")
    assert first_crop not in end["pictures"] and crop not in end["pictures"]
    assert all(
        first_crop not in [i["key"] for i in check.images]
        for check in ModelCall.objects.filter(purpose="check_scene_picture")
    )


def test_talking_logo_uses_the_packaging_photo_and_excludes_the_portrait_and_room(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    job = Job.objects.get()
    box = page_photo(job, label("state", "front", "sage-green", "The box front."))
    Scene.objects.filter(number=1).update(packaging_photo=box, product_state="beside its box")
    fake_model.respond(PURPOSE, FOUND)
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say, scene=1)
    run(fake_model, steps)

    [vision] = [request for request in fake_model.asked if request.purpose == PURPOSE]
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert vision.images[1].key == job.library.get(number=box).file
    assert [i.key for i in vision.images] == draw["pictures"][:-3]  # portrait, room, crop
    assert picture.sent["logo"]["source_file"] == vision.images[1].key


def test_full_picture_list_keeps_every_picture_and_sends_the_logo_sentence(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    job = Job.objects.get()
    grip = label("in-use", "none", "sage-green", "A hand holding the mug.", alone="no")
    picks = [*PICTURE_FORM["picks"]] + [
        {"item": page_photo(job, grip), "take": "Copy the grip."} for _ in range(13)
    ]
    fake_model.respond(PURPOSE, {**FOUND, "image": 1})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        STOP,
    )
    start_scene(fake_model, say)
    run(fake_model, steps)

    [vision] = handoffs(PURPOSE)
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert picture.sent["logo"]["status"] == "picture_limit"
    assert len(draw["pictures"]) == 16
    assert draw["pictures"] == [photo["file"] for photo in picture.sent["photos"]]
    assert draw["prompt"] == vision["prompt"].replace(
        PRODUCT_DESIGN_LINE, f"{SENTENCE} {PRODUCT_DESIGN_LINE}"
    )
    assert any("already had 16 pictures" in text for _, text in recorded(api, session_id))


@pytest.mark.parametrize("interrupted_at", ["draw_scene_picture", "check_scene_picture"])
def test_a_retried_tool_call_reuses_the_logo_answer_and_records_what_was_sent(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], interrupted_at: str
) -> None:
    fake_model.respond(PURPOSE, FOUND)
    fake_model.respond(interrupted_at, WorkerStopped())
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say)
    with pytest.raises(WorkerStopped):
        steps.run_held()
    tasks.carry_on_after_the_worker_starts()
    run(fake_model, steps)

    assert len(handoffs(PURPOSE)) == 1
    [draw] = handoffs("draw_scene_picture")
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert picture.sent["logo"]["status"] == "found"
    assert picture.sent["logo"]["crop_file"] == draw["pictures"][-1]
    assert picture.text == draw["prompt"]


def test_a_retried_draw_does_not_pay_again_for_a_logo_refused_after_billing(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        PURPOSE,
        BlockedBySafetyFilter("Refused", of_what_was_made=True, input_tokens=100, output_tokens=20),
        FOUND,
    )
    fake_model.respond("draw_scene_picture", WorkerStopped())
    fake_model.respond("scene_agent", DRAW, STOP)
    start_scene(fake_model, say)
    with pytest.raises(WorkerStopped):
        steps.run_held()
    tasks.carry_on_after_the_worker_starts()
    run(fake_model, steps)

    called = ModelCall.objects.get(purpose=PURPOSE)
    assert called.cost_usd and called.cost_usd > 0
    [draw] = handoffs("draw_scene_picture")
    assert draw["prompt"] == called.handoff["prompt"]
    picture = ProducedItem.objects.get(kind="starting_picture")
    assert picture.sent is not None
    assert picture.sent["logo"] == {"status": "refused"}
