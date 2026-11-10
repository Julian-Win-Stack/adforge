"""Every page view of the product reaches the image model and the picture check, beside the
scene agent's picks, each photo labelled from the page (spec #514 root cause 1, ticket
#516). Through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.scene_agent import page_views, picture_check
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import cut_and_send
from jobs.library import LibraryEntry
from jobs.models import Job, ProducedItem, Scene

from .conftest import HeldSteps, handoffs, paid_for, recorded
from .library_photos import label, page_photo
from .test_producer_broll import calling, checked, instructed, run  # noqa: F401
from .test_recolour import RECOLOUR_OK
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

BACK_WORDS = "The mug from the back, a maker's stamp under the handle."


def _start_scene_two(fake_model: FakeModel, say: Callable[..., None]) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )


def _checks() -> list[ModelCall]:
    return list(
        ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")
    )


def test_a_back_view_the_agent_never_picked_reaches_the_image_model_and_the_check(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    back_file = job.library.get(number=back).file
    [draw] = handoffs("draw_scene_picture")
    assert back_file in draw["pictures"]
    assert (
        f"an extra view of the product from its page: {BACK_WORDS[:-1]} (side: back). Take only "
        "its shape, parts and sides from it, never its colour."
    ) in draw["prompt"]
    [check] = _checks()
    assert back_file in [image["key"] for image in check.images]
    assert (
        f"Extra view, page photo {back}: {BACK_WORDS[:-1]} (side: back)."
        in (check.handoff["photos"])
    )


def test_a_view_in_another_colour_goes_as_shape_only_and_the_pick_gets_the_page_label(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "red", BACK_WORDS))
    ad = cut_and_send.planned_colour(Scene.objects.get(number=2))
    shape_only = (
        f"This photo shows the product in red; it shows the shape only. The ad's product is {ad}."
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert job.library.get(number=back).file in draw["pictures"]
    assert (
        "(side: back). Take only its shape, parts and sides from it, never its colour. "
        f"{shape_only}" in draw["prompt"]
    )
    assert "recolour_picture" not in paid_for()
    [check] = _checks()
    pick, *rest = check.handoff["photos"]
    assert f"Extra view, page photo {back}: {BACK_WORDS[:-1]} (side: back). {shape_only}" in rest
    first = job.library.get(number=1)
    assert pick == (
        f"Image 1: Copy the shape and glaze. (The page photo shows: {first.label.rstrip('.')}, "
        f"side: {first.side}.)"
    )
    [told] = instructed["check_scene_picture"]
    assert page_views.CHECK_COLOUR_RULE in told


EDIT = {
    "keep": ["The mug, room and hand"],
    "change": "Turn the mug towards the camera",
    "anything_else": "not needed here",
    "picks": [{"item": 1, "take": "Copy the round rim."}],
}


def test_an_edit_and_the_end_picture_each_get_the_views_once(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    back_file = job.library.get(number=back).file
    view_line = f"Extra view, page photo {back}: {BACK_WORDS[:-1]} (side: back)."
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("edit_starting_picture", EDIT)]),
        turn(calls=[("edit_starting_picture", EDIT)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    _draw, first_edit, second_edit = handoffs("draw_scene_picture")
    for edit in (first_edit, second_edit):
        assert edit["pictures"].count(back_file) == 1
        assert edit["prompt"].count("an extra view of the product from its page") == 2
    [end] = handoffs("make_end_picture")
    assert end["pictures"].count(back_file) == 1
    assert "an extra view of the product from its page: " + BACK_WORDS[:-1] in end["prompt"]
    checks = _checks()
    assert len(checks) == 4
    for check in checks:
        assert [image["key"] for image in check.images].count(back_file) == 1
        assert check.handoff["photos"].count(view_line) == 1
    assert all(page_views.CHECK_COLOUR_RULE in told for told in instructed["check_scene_picture"])


def test_a_view_the_owner_excludes_never_goes_again_even_through_an_edit(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    back_file = job.library.get(number=back).file
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "The owner should say."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)
    assert back_file in handoffs("draw_scene_picture")[0]["pictures"]

    fake_model.respond(
        "scene_agent",
        turn(calls=[("edit_starting_picture", EDIT)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    calling(
        fake_model,
        say,
        ("set_references", {"scene": 2, "exclude": [back]}),
        ("start_scene_agent", {"scene": 2, "instruction": "Not its back.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    _draw, edit = handoffs("draw_scene_picture")
    [end] = handoffs("make_end_picture")
    assert back_file not in edit["pictures"] + end["pictures"]
    assert all(back_file not in [i["key"] for i in check.images] for check in _checks()[1:])


def test_a_view_that_changes_on_the_page_never_makes_the_picture_out_of_date(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Wait for the owner."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    assert back in [p.get("library_number") for p in picture.sent["photos"]]
    # The page's back photo is taken again: the view no longer matches the picture's copy.
    job.photos.filter(position=job.library.get(number=back).source["photo"]).update(
        file="jobs/retaken-back.png"
    )

    fake_model.respond(
        "scene_agent",
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    assert "out of date" not in str(handoffs("scene_agent")[-1])
    assert ProducedItem.objects.filter(kind="clip", picture=picture).exists()


IN_USE = label("in-use", "none", "sage-green", "A hand holding the mug.", alone="no")


def test_views_past_the_image_models_room_are_left_out_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    job = Job.objects.get()
    in_use = [page_photo(job, IN_USE) for _ in range(13)]
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    side = page_photo(job, label("side", "side", "sage-green", "The mug from its side."))
    picks = [{"item": 1, "take": "Copy the glaze."}] + [
        {"item": number, "take": "Copy the grip."} for number in in_use
    ]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    # 14 picks and the room leave room for one view of the three (photo 2, back, side).
    [draw] = handoffs("draw_scene_picture")
    assert len(draw["pictures"]) == 16
    assert draw["pictures"][-2] == job.library.get(number=back).file
    left_out = [text for _, text in recorded(api, session_id) if "left out" in text]
    assert left_out == [
        f"Scene 2's starting picture: the page's views 2, {side} were left out, as the image "
        "model takes 16 pictures at most."
    ]


def test_a_view_whose_file_is_gone_is_left_out_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    job.library.filter(number=back).update(file="jobs/never-saved.png")
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    [draw] = handoffs("draw_scene_picture")
    assert "jobs/never-saved.png" not in draw["pictures"]
    assert (
        f"Scene 2's starting picture: the page's view {back} was left out, as its file could "
        "not be found."
    ) in [text for _, text in recorded(api, session_id)]


def test_a_view_with_no_side_and_a_pick_gone_from_the_library_are_told_in_notices(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    job = Job.objects.get()
    sideless = page_photo(job, label("side", "none", "sage-green", "The mug, its side unclear."))
    grip = page_photo(job, IN_USE)
    picks = [{"item": 1, "take": "Copy the glaze."}, {"item": grip, "take": "Copy the grip."}]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Wait."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)
    [check] = _checks()
    assert (
        f"Extra view, page photo {sideless}: The mug, its side unclear (side: not given)."
        in check.handoff["photos"]
    )
    job.library.filter(number=grip).delete()

    fake_model.respond(
        "scene_agent",
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    # The end picture's check is told only the agent's words for the gone pick.
    assert "Image 2: Copy the grip." in _checks()[1].handoff["photos"]
    texts = [text for _, text in recorded(api, session_id)]
    assert (
        f"Scene 2's starting picture: the page's view {sideless} was sent with no side, as the "
        "library gives none."
    ) in texts
    assert (
        f"Scene 2's end picture: library item {grip} is no longer in the library, so the "
        "picture check was told only the agent's words for it, not what the page photo shows."
    ) in texts


def test_a_pick_recoloured_to_the_ads_colour_says_so_in_its_label(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
    page_photo(
        job, label("side", "front", "sage-green", "The mug in its colour."), colour_photo=True
    )
    red = page_photo(job, label("side", "back", "red", BACK_WORDS))
    picks = [{"item": 1, "take": "Copy the glaze."}, {"item": red, "take": "Copy the stamp."}]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    assert "recolour_picture" in paid_for()
    ad = cut_and_send.planned_colour(Scene.objects.get(number=2))
    [check] = _checks()
    assert (
        f"Image 2: Copy the stamp. (The page photo shows: {BACK_WORDS[:-1]}, side: back; sent "
        f"recoloured to {ad}.)"
    ) in check.handoff["photos"]


def test_without_views_the_checks_instructions_stay_as_they_were(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    picks = [{"item": 1, "take": "Copy the glaze."}, {"item": 2, "take": "Copy the handle."}]
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    _start_scene_two(fake_model, say)
    run(fake_model, steps)

    [told] = instructed["check_scene_picture"]
    assert told == picture_check.instructions(True)
    assert not any("Extra view" in line for line in _checks()[0].handoff["photos"])


def _view(number: int, side: str, colour: str) -> LibraryEntry:
    return LibraryEntry.model_validate(
        {
            "number": number,
            "file": f"photo-{number}.png",
            "source": {"came_from": "page_photo", "photo": number},
            "kind": "side",
            "side": side,
            "alone": "yes",
            "colour": colour,
            "label": f"Photo {number}.",
        }
    )


def test_short_of_room_views_are_kept_by_the_sides_the_picks_dont_show() -> None:
    views = [
        _view(2, "front", "green"),
        _view(3, "back", "red"),
        _view(4, "side", "red"),
        _view(5, "back", "green"),
        _view(6, "top", "red"),
    ]
    kept = page_views.in_kept_order(views, shown={"front"}, colour="green")
    # The back in the picture's colour, then any colour for the side and top still unshown,
    # then the rest in library order.
    assert [view.number for view in kept] == [5, 4, 6, 2, 3]


def test_short_of_room_a_view_with_no_side_never_takes_an_unshown_sides_place() -> None:
    views = [_view(2, "none", "green"), _view(3, "back", "red")]
    kept = page_views.in_kept_order(views, shown={"front"}, colour="green")
    assert [view.number for view in kept] == [3, 2]
