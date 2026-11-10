"""The side picker and true crops (#524): a scene agent's pick that isn't the product alone
goes to the image model and both checks as a crop of the page photo at the picker's box, cut
in code from the photo's real pixels, never as a redrawn cut-out. Outside services faked."""

import io
import json
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter
from jobs import library_sheets, side_photos
from jobs.cut_and_send import ad_colour
from jobs.library import OwnerPhoto, PagePhoto, add_to_library
from jobs.models import Job, LibraryItem, ProducedItem, ProductPhoto

from .conftest import HeldSteps, handoffs, paid_for, photo, recorded
from .library_photos import label
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_face_swap import FACE_PASSES
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# The held photo is this big, so a crop of part of it is big enough to send.
WIDE, HIGH = 1000, 1250
# The picker's box around the product in the held photo, and the crop it makes: the box
# widened by 8% of its size on each side (0.032 of the photo, more than the 2% floor).
BOX = {"left": 0.2, "top": 0.3, "right": 0.6, "bottom": 0.7}
CROP = (168, 335, 632, 915)


def held_photo(colour: str | None = None, *, face: bool = False) -> int:
    """The job's next page photo, the product held in a hand (kind side, not alone), big, in
    `colour` (the ad's when None), showing a stranger's `face` or not: gives its library
    number."""
    job = Job.objects.get()
    colour = colour or ad_colour(job)
    position = job.photos.count() + 1
    file = file_store.save(
        f"jobs/{job.pk}/photo-{position}.png", photo(position, width=WIDE, height=HIGH)
    )
    ProductPhoto.objects.create(
        job=job,
        position=position,
        file=file,
        source_url=f"https://shop.example/{position}.png",
        has_face=face,
    )
    held = label("side", "front", colour, "The mug held in a hand from the front.", alone="no")
    return add_to_library(job, file, held, PagePhoto(photo=position)).number


def picker_answer(number: int, box: dict[str, float] | None = BOX) -> dict[str, Any]:
    """The picker's answer ranking photo `number` for the front, boxed at `box` (no box when
    None)."""
    return {
        "sides": [{"side": "front", "ranked": [{"photo": number, "shows": "a hand holds it"}]}],
        "boxes": [] if box is None else [{"photo": number, "box": box}],
        "left_out": [],
    }


def draw(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    number: int,
    *then: Any,
) -> None:
    """Scene 2's agent draws a starting picture from product photo 1 and photo `number`,
    takes the turns `then`, then gives up."""
    picks = [{"item": 1, "take": "Copy the shape."}, {"item": number, "take": "Copy the grip."}]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        *then,
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)


def asked(fake_model: FakeModel, purpose: str) -> list[Any]:
    return [request for request in fake_model.asked if request.purpose == purpose]


def crop_of(number: int) -> LibraryItem:
    return LibraryItem.objects.get(source={"came_from": "true_crop", "of": number})


def sent_files() -> list[str]:
    made = ProducedItem.objects.filter(kind="starting_picture").latest("pk")
    assert made.sent is not None
    return [each["file"] for each in made.sent["photos"]]


def test_a_held_photo_goes_to_the_image_model_and_the_check_as_its_true_crop(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo()
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number)
    crop = crop_of(number)
    original = LibraryItem.objects.get(number=number)
    sent = handoffs("draw_scene_picture")[0]["pictures"]
    assert crop.file in sent and original.file not in sent
    with PIL.Image.open(io.BytesIO(file_store.read(crop.file))) as made:
        cut = made.convert("RGB")
    with PIL.Image.open(io.BytesIO(file_store.read(original.file))) as whole:
        expected = whole.convert("RGB").crop(CROP)
    assert cut.size == (464, 580)
    assert list(cut.getdata()) == list(expected.getdata())
    assert "make_product_only" not in paid_for()
    assert "pick_cut_out_helpers" not in paid_for()
    (check,) = asked(fake_model, "check_scene_picture")
    assert crop.file in [image.key for image in check.images]
    assert sent_files() == sent
    # The picker is shown every page photo one by one, each named, with the ad's colour.
    (picker,) = asked(fake_model, "pick_side_photos")
    page = [item.file for item in LibraryItem.objects.order_by("number")[:number]]
    assert [image.key for image in picker.images] == page
    assert all(image.label.startswith("Photo ") for image in picker.images)
    assert ad_colour(Job.objects.get()) in picker.handoff.model_dump_json()


def test_the_picker_is_asked_once_per_ad(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo()
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number)
    # A second run, as after a restart or by another scene: nothing is asked or cut again.
    draw(fake_model, steps, say, number)
    assert len(asked(fake_model, "pick_side_photos")) == 1
    assert paid_for().count("pick_side_photos") == 1
    assert LibraryItem.objects.filter(source__came_from="true_crop").count() == 1
    drawn = [each["pictures"] for each in handoffs("draw_scene_picture")]
    assert len(drawn) == 2 and all(crop_of(number).file in pictures for pictures in drawn)


def test_a_box_covering_most_of_the_photo_sends_it_whole(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo()
    most = {"left": 0.05, "top": 0.05, "right": 0.95, "bottom": 0.9}
    fake_model.respond("pick_side_photos", picker_answer(number, most))
    draw(fake_model, steps, say, number)
    original = LibraryItem.objects.get(number=number)
    assert original.file in handoffs("draw_scene_picture")[0]["pictures"]
    assert not LibraryItem.objects.filter(source__came_from="true_crop").exists()
    assert "make_product_only" not in paid_for()


@pytest.mark.parametrize(
    ("box", "why"),
    [
        ({"left": 0.4, "top": 0.4, "right": 0.5, "bottom": 0.5}, "too small"),
        (None, "no box"),
        ({"left": 1.2, "top": 0.3, "right": 1.5, "bottom": 0.6}, "off the photo"),
    ],
)
def test_a_pick_with_no_usable_crop_goes_whole_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    box: dict[str, float] | None,
    why: str,
) -> None:
    number = held_photo()
    fake_model.respond("pick_side_photos", picker_answer(number, box))
    draw(fake_model, steps, say, number)
    original = LibraryItem.objects.get(number=number)
    assert original.file in handoffs("draw_scene_picture")[0]["pictures"]
    assert "make_product_only" not in paid_for()
    said = [text for _, text in recorded(api, session_id) if f"library item {number}" in text]
    told = [text for text in said if why in text and "scene 2" in text.lower()]
    assert len(told) == 1, said


@pytest.mark.parametrize(
    "failure",
    [OutsideServiceDown("the service is down"), BlockedBySafetyFilter("refused")],
    ids=["fails", "refused"],
)
def test_a_failed_picker_keeps_the_redraw_and_is_not_asked_again(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    failure: Exception,
) -> None:
    number = held_photo()
    # The gateway asks a service that is down again after each retry delay before it gives up.
    down = isinstance(failure, OutsideServiceDown)
    tries = len(settings.RETRY_DELAYS_SECONDS) + 1 if down else 1
    fake_model.respond("pick_side_photos", *[failure] * tries)
    edit = {
        "keep": ["the product"],
        "change": "Show the grip more clearly",
        "anything_else": "not needed here",
        "picks": [{"item": number, "take": "Copy the grip."}],
    }
    draw(fake_model, steps, say, number, turn(calls=[("edit_starting_picture", edit)]))
    # The edit's pick is never asked again: what is asked is only what was scripted.
    assert len(asked(fake_model, "pick_side_photos")) == tries
    assert paid_for().count("make_product_only") == 1
    cut_out = LibraryItem.objects.get(source={"came_from": "product_only", "of": number})
    # The cut-out is drawn from, or first recoloured to the ad's colour as today.
    recoloured = [each["pictures"][0] for each in handoffs("recolour_picture")]
    assert cut_out.file in handoffs("draw_scene_picture")[0]["pictures"] + recoloured
    assert not LibraryItem.objects.filter(source__came_from="true_crop").exists()
    assert any("side picker" in text.lower() for _, text in recorded(api, session_id))


def test_an_other_colour_picks_crop_is_recoloured(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo(colour="black")
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number)
    crop = crop_of(number)
    (recolour,) = handoffs("recolour_picture")
    assert recolour["pictures"][0] == crop.file
    painted = LibraryItem.objects.get(source__came_from="recoloured", source__of=crop.number)
    assert painted.file in handoffs("draw_scene_picture")[0]["pictures"]
    # A version of a crop is the crop's photo's too: never shown as an item to pick.
    assert painted.number not in library_sheets.sheets_of(Job.objects.get(), drawn=False).entries
    assert f"\n{painted.number}. " not in told_to_the_scene_agent(fake_model)


def test_a_strangers_face_is_swapped_on_the_crop(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_face_swap", lambda _: FACE_PASSES)
    number = held_photo(face=True)
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number)
    crop = crop_of(number)
    (swap,) = handoffs("swap_face")
    assert swap["pictures"][0] == crop.file
    swapped = LibraryItem.objects.get(source__came_from="face_swapped", source__of=crop.number)
    assert swapped.file in handoffs("draw_scene_picture")[0]["pictures"]


def test_an_end_pictures_edit_carries_the_crop_of_a_pick(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo()
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number, turn(calls=[("make_end_picture", END_PICTURE)]))
    crop = crop_of(number)
    assert crop.file in handoffs("make_end_picture")[0]["pictures"]
    checks = asked(fake_model, "check_scene_picture")
    assert crop.file in [image.key for image in checks[-1].images]


def test_a_crop_is_never_in_the_library_the_scene_agent_is_shown(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    number = held_photo()
    fake_model.respond("pick_side_photos", picker_answer(number))
    draw(fake_model, steps, say, number)
    crop = crop_of(number)
    told = told_to_the_scene_agent(fake_model)
    assert f"\\n{number}. " in told
    assert f"\\n{crop.number}. " not in told
    sheets = library_sheets.sheets_of(Job.objects.get(), drawn=False)
    assert crop.number not in sheets.entries and number in sheets.entries


def told_to_the_scene_agent(fake_model: FakeModel) -> str:
    """What the scene agent's last turn was told, as JSON text."""
    last = fake_model.turns_taken[-1]
    return json.dumps(last.instructions) + last.handoff.model_dump_json()


def test_a_held_owner_photo_keeps_the_redraw_and_is_never_shown_to_the_picker(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    file = file_store.save(f"jobs/{job.pk}/owner.png", photo(77, width=WIDE, height=HIGH))
    held = label("side", "front", ad_colour(job), "The mug held in a hand.", alone="no")
    number = add_to_library(job, file, held, OwnerPhoto(original=file)).number
    draw(fake_model, steps, say, number)
    # The picker is asked for the page views (#525), shown only the page's photos.
    (picker,) = asked(fake_model, "pick_side_photos")
    assert file not in [image.key for image in picker.images]
    assert paid_for().count("make_product_only") == 1
    assert not LibraryItem.objects.filter(source__came_from="true_crop").exists()


@pytest.mark.parametrize(
    ("box", "size", "cut"),
    [
        # Widened by 8% of the box on each side.
        ((0.2, 0.3, 0.6, 0.7), (1000, 1250), (168, 335, 632, 915)),
        # A small box is widened by at least 2% of the photo, and clamped to its edges.
        ((0.0, 0.5, 0.1, 0.6), (4000, 4000), (0, 1920, 480, 2480)),
        # Corners given the wrong way round are put right.
        ((0.6, 0.7, 0.2, 0.3), (1000, 1250), (168, 335, 632, 915)),
        # A box of 60% of the photo or more: the photo goes whole.
        ((0.1, 0.0, 0.9, 0.9), (1000, 1000), side_photos.WHOLE),
        # A crop under 320 px on its longest side is too small.
        ((0.4, 0.4, 0.5, 0.5), (1000, 1250), side_photos.TOO_SMALL),
        # An empty box, or one off the photo, is no box.
        ((0.3, 0.3, 0.3, 0.6), (1000, 1000), side_photos.NO_BOX),
        ((1.1, 0.2, 1.4, 0.5), (1000, 1000), side_photos.NO_BOX),
    ],
)
def test_the_crop_maths(
    box: tuple[float, float, float, float], size: tuple[int, int], cut: object
) -> None:
    left, top, right, bottom = box
    given = side_photos.Box(left=left, top=top, right=right, bottom=bottom)
    assert side_photos.cut(given, *size) == cut
