"""The side picker's one photo per side replaces every alone view (#525): code adds, beside
the scene agent's picks, each side's chosen photo as its true crop, labelled from the page
with the picker's side and its line on what the crop shows, to the image model, the picture
check and the clip judge. Through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.cut_and_send import ad_colour
from jobs.library import PagePhoto, PictureLabel, add_to_library
from jobs.models import Job, LibraryItem, ProducedItem, ProductPhoto

from .conftest import HeldSteps, handoffs, photo, recorded
from .library_photos import label, page_photo
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_face_swap import FACE_PASSES
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# A big photo, so a crop of part of it is big enough to send.
WIDE, HIGH = 1000, 1250
BOX = {"left": 0.2, "top": 0.3, "right": 0.6, "bottom": 0.7}
# A box whose crop is under 320 px on its longest side, even in a big photo.
TINY = {"left": 0.4, "top": 0.4, "right": 0.5, "bottom": 0.5}
BACK_WORDS = "A person holds the mug, its back to the camera."
SHOWS = "a hand covers the handle"


def big_photo(
    words: str, *, side: str = "back", alone: str = "no", colour: str = "", face: bool = False
) -> int:
    """The job's next page photo, big, labelled `words`, showing a stranger's `face` or not:
    gives its library number."""
    job = Job.objects.get()
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
    picture: PictureLabel = label("side", side, colour or ad_colour(job), words, alone=alone)
    return add_to_library(job, file, picture, PagePhoto(photo=position)).number


def answer(*sides: tuple[str, list[int]], boxes: dict[int, dict[str, float]]) -> dict[str, Any]:
    """The picker's answer: each side with its ranked photos, best first, and the boxes."""
    return {
        "sides": [
            {"side": side, "ranked": [{"photo": n, "shows": SHOWS} for n in ranked]}
            for side, ranked in sides
        ],
        "boxes": [{"photo": n, "box": box} for n, box in boxes.items()],
        "left_out": [],
    }


def scene_two(
    fake_model: FakeModel, say: Callable[..., None], *calls: Any, first: Any = ()
) -> None:
    """Scene 2's agent makes `calls`, started by the producer after its calls `first`."""
    fake_model.respond("scene_agent", *(turn(calls=[call]) for call in calls))
    calling(
        fake_model,
        say,
        *first,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )


def give_up() -> tuple[str, dict[str, str]]:
    return ("give_up", {"reason": "Enough for this test."})


def crop_of(number: int) -> str:
    return LibraryItem.objects.get(source={"came_from": "true_crop", "of": number}).file


def file_of(number: int) -> str:
    return LibraryItem.objects.get(number=number).file


def check_images() -> list[str]:
    check = ModelCall.objects.filter(purpose="check_scene_picture").latest("created_at")
    return [image["key"] for image in check.images]


def developer_notes(api: APIClient, session_id: str) -> list[str]:
    return [text for _, text in recorded(api, session_id)]


def test_a_back_only_shown_with_a_person_reaches_all_three_models_as_its_crop(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    back = big_photo(BACK_WORDS)
    # A view alone the picker didn't choose (photo 2) never goes.
    fake_model.respond(
        "pick_side_photos",
        answer(("front", [1]), ("back", [back]), boxes={1: BOX, back: BOX}),
    )
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), ("film", FILM), give_up())
    run(fake_model, steps)

    crop = crop_of(back)
    [draw] = handoffs("draw_scene_picture")
    assert crop in draw["pictures"] and file_of(back) not in draw["pictures"]
    assert file_of(2) not in draw["pictures"]
    assert (
        f"an extra view of the product from its page: {BACK_WORDS[:-1]} (side: back; {SHOWS}). "
        "Take only its shape, parts and sides from it, never its colour."
    ) in draw["prompt"]
    assert crop in check_images() and file_of(2) not in check_images()
    check = ModelCall.objects.get(purpose="check_scene_picture")
    assert (
        f"Extra view, page photo {back}: {BACK_WORDS[:-1]} (side: back; {SHOWS})."
        in check.handoff["photos"]
    )
    judge = ModelCall.objects.filter(purpose="check_clip_product").earliest("created_at")
    shown = {image["key"]: image["label"] for image in judge.images}
    assert file_of(2) not in shown
    assert shown[crop].endswith(f"It shows: {BACK_WORDS[:-1]} (side: back; {SHOWS}).")


def test_other_sizes_the_picker_left_out_never_reach_the_models(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    bigger = page_photo(job, label("side", "back", "sage-green", "The large mug from the back."))
    back = page_photo(job, label("side", "back", "sage-green", "The mug from the back."))
    whole = {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}
    picked = answer(("back", [back]), boxes={back: whole, bigger: whole})
    picked["left_out"] = [{"photo": bigger, "why": "another size"}]
    fake_model.respond("pick_side_photos", picked)
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert file_of(back) in draw["pictures"]
    assert file_of(bigger) not in draw["pictures"] + check_images()
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    # Nor the clip judge, which gets the views the picture's sent record keeps.
    assert bigger not in [p.get("library_number") for p in picture.sent["photos"]]


def test_the_agents_pick_of_a_sides_chosen_photo_is_not_added_again(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    back = big_photo(BACK_WORDS)
    fake_model.respond("pick_side_photos", answer(("back", [back]), boxes={back: BOX}))
    picks = [{"item": 1, "take": "Copy the glaze."}, {"item": back, "take": "Copy the back."}]
    scene_two(
        fake_model, say, ("draw_starting_picture", {**PICTURE_FORM, "picks": picks}), give_up()
    )
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert draw["pictures"].count(crop_of(back)) == 1
    assert "an extra view of the product from its page" not in draw["prompt"]


def test_an_owner_excluded_chosen_photo_gives_way_to_the_sides_next_ranked(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    back = big_photo(BACK_WORDS)
    second = big_photo("A person sets the mug down, its back showing.")
    fake_model.respond(
        "pick_side_photos", answer(("back", [back, second]), boxes={back: BOX, second: BOX})
    )
    scene_two(
        fake_model,
        say,
        ("draw_starting_picture", PICTURE_FORM),
        give_up(),
        first=[("set_references", {"scene": 2, "exclude": [back]})],
    )
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert crop_of(second) in draw["pictures"]
    assert file_of(back) not in draw["pictures"]
    assert not LibraryItem.objects.filter(source={"came_from": "true_crop", "of": back}).exists()


def test_a_too_small_crop_moves_on_and_a_side_with_none_usable_goes_without(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    far = big_photo("A person far away holds the mug, its back showing.")
    back = big_photo(BACK_WORDS)
    top = big_photo("A person lifts the lid of the mug.", side="top")
    fake_model.respond(
        "pick_side_photos",
        answer(("back", [far, back]), ("top", [top]), boxes={far: TINY, back: BOX}),
    )
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert crop_of(back) in draw["pictures"]
    assert file_of(far) not in draw["pictures"] and file_of(top) not in draw["pictures"]
    notes = developer_notes(api, session_id)
    assert any(f"photo {far}" in note and "too small" in note for note in notes), notes
    assert any("top" in note and "no photo" in note for note in notes), notes


def test_a_failed_picker_sends_every_alone_view_with_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    def refused(_: object) -> dict[str, Any]:
        raise BlockedBySafetyFilter("the side picker refused")

    fake_model.answer_unscripted("pick_side_photos", refused)
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", "The mug from the back."))
    held = big_photo(BACK_WORDS)
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert file_of(2) in draw["pictures"] and file_of(back) in draw["pictures"]
    assert file_of(held) not in draw["pictures"]
    assert not LibraryItem.objects.filter(source__came_from="true_crop").exists()
    notes = developer_notes(api, session_id)
    assert any("every page photo of the product alone" in note for note in notes), notes


def test_a_page_photo_whose_file_is_gone_is_never_shown_to_the_picker_or_sent(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", "The mug from the back."))
    job.library.filter(number=back).update(file="jobs/never-saved.png")
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    (picker,) = [request for request in fake_model.asked if request.purpose == "pick_side_photos"]
    assert "jobs/never-saved.png" not in [image.key for image in picker.images]
    [draw] = handoffs("draw_scene_picture")
    assert file_of(2) in draw["pictures"] and "jobs/never-saved.png" not in draw["pictures"]
    notes = developer_notes(api, session_id)
    assert any("side picker" in note and f": {back}." in note for note in notes), notes


def test_a_strangers_face_in_a_views_crop_is_swapped_for_the_presenter(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_face_swap", lambda _: FACE_PASSES)
    back = big_photo(BACK_WORDS, face=True)
    fake_model.respond("pick_side_photos", answer(("back", [back]), boxes={back: BOX}))
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    crop = LibraryItem.objects.get(source={"came_from": "true_crop", "of": back})
    (swap,) = handoffs("swap_face")
    assert swap["pictures"][0] == crop.file
    swapped = LibraryItem.objects.get(source__came_from="face_swapped", source__of=crop.number)
    [draw] = handoffs("draw_scene_picture")
    assert swapped.file in draw["pictures"] and crop.file not in draw["pictures"]
    assert swapped.file in check_images()


def test_one_photo_ranked_for_two_sides_goes_once_and_bad_ranks_are_told(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    back = big_photo(BACK_WORDS)
    excluded = big_photo("A person holds the mug up, its back showing.")
    fake_model.respond(
        "pick_side_photos",
        answer(
            ("back", [99, excluded, back]),
            ("handle", [back]),
            ("bottom", []),
            boxes={back: BOX, excluded: BOX},
        ),
    )
    scene_two(
        fake_model,
        say,
        ("draw_starting_picture", PICTURE_FORM),
        give_up(),
        first=[("set_references", {"scene": 2, "exclude": [excluded]})],
    )
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert draw["pictures"].count(crop_of(back)) == 1
    notes = developer_notes(api, session_id)
    assert (
        f"Scene 2's starting picture: the side picker's side \"back\" went as photo {back}, "
        "past the photos ranked above it: photo 99 isn't a page photo in the library; the "
        f"owner excluded photo {excluded} for this scene."
    ) in notes
    assert any('side "bottom" got no photo' in note for note in notes), notes


def test_a_picker_that_names_no_side_sends_every_alone_view_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("pick_side_photos", answer(boxes={}))
    scene_two(fake_model, say, ("draw_starting_picture", PICTURE_FORM), give_up())
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert file_of(2) in draw["pictures"]
    notes = developer_notes(api, session_id)
    assert any("named no side" in note for note in notes), notes
