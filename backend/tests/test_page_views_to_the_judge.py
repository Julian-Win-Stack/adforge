"""The clip judge gets the same page views the clip's starting picture was drawn and checked
with, read from the picture's sent record, each labelled from the page, and the agent's
picks with the page label and the agent's words (spec #514 root cause 1, ticket #520).
Through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import clip_judge, cut_and_send
from jobs.library import PAGE_VIEW_TIER
from jobs.models import Job, ProducedItem, Scene

from .conftest import HeldSteps, recorded
from .library_photos import label, page_photo
from .test_producer_broll import calling, checked, instructed, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

# The tier of the agent's pick in a clip's sent list, beside PAGE_VIEW_TIER for a view.
PICK = 2
BACK_WORDS = "The mug from the back, a maker's stamp under the handle."
SIDE_WORDS = "The mug from its side, the handle on the right."


def _scene(fake_model: FakeModel, say: Callable[..., None], *calls: Any, number: int = 2) -> None:
    fake_model.respond("scene_agent", *(turn(calls=[call]) for call in calls))
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": number, "instruction": "Show the pour.", "quotes": ["go on"]},
        ),
    )


def _judged() -> ModelCall:
    judge = ModelCall.objects.filter(purpose="check_clip_product").order_by("created_at").first()
    assert judge is not None
    return judge


@pytest.mark.parametrize("number", [2, 3], ids=["broll", "talking"])
def test_the_judge_gets_the_views_the_picture_was_drawn_with_and_the_colour_rule(
    number: int,
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "red", BACK_WORDS))
    _scene(
        fake_model,
        say,
        ("draw_starting_picture", PICTURE_FORM),
        ("give_up", {"reason": "Wait for the owner."}),
        number=number,
    )
    run(fake_model, steps)
    # A view the page gains after the picture was drawn isn't one it was drawn with.
    side = page_photo(job, label("side", "side", "sage-green", SIDE_WORDS))
    _scene(
        fake_model,
        say,
        ("film", FILM),
        ("give_up", {"reason": "Enough for this test."}),
        number=number,
    )
    run(fake_model, steps)

    ad = cut_and_send.planned_colour(Scene.objects.get(number=number))
    first = job.library.get(number=1)
    back_file = job.library.get(number=back).file
    shown = {image["key"]: image["label"] for image in _judged().images}
    assert job.library.get(number=side).file not in shown
    assert shown[back_file].endswith(
        "Real product photo 2, an extra view from the page, sent to the picture model, not "
        "the video model. It shows: the product alone. This "
        "photo shows the "
        f"product in red; it shows the shape only. The ad's product is {ad}."
    )
    assert shown[first.file].endswith(
        "Real product photo 1, sent to the picture model, not the video model: The page photo "
        f"shows: {first.label.rstrip('.')} (side: {first.side}). What the agent asked for: "
        "Copy the shape and glaze."
    )
    [told] = instructed["check_clip_product"]
    assert clip_judge.JUDGE_COLOUR_RULE in told
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=number)
    clip = ProducedItem.objects.get(kind="clip", scene__number=number)
    assert picture.sent is not None and clip.sent is not None
    drawn_with = [p["library_number"] for p in picture.sent["photos"] if p.get("page_view")]
    assert back in drawn_with
    # The clip's sent list tells the agent's pick (tier 2) from the views code added (tier 4).
    assert [(p["library_number"], p["tier"]) for p in clip.sent["picture_photos"]] == [
        (1, PICK),
        *((number, PAGE_VIEW_TIER) for number in drawn_with),
    ]


EDIT = {
    "keep": ["The mug, room and hand"],
    "change": "Turn the mug towards the camera",
    "anything_else": "not needed here",
    "picks": [{"item": 1, "take": "Copy the round rim."}],
}


def test_a_clip_from_an_edited_picture_gets_the_views_the_edit_was_drawn_with_once(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    back = page_photo(job, label("side", "back", "sage-green", BACK_WORDS))
    _scene(
        fake_model,
        say,
        ("draw_starting_picture", PICTURE_FORM),
        ("edit_starting_picture", EDIT),
        ("film", FILM),
        ("give_up", {"reason": "Enough for this test."}),
    )
    run(fake_model, steps)

    edit = ProducedItem.objects.filter(kind="starting_picture", scene__number=2).latest("pk")
    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert clip.picture == edit and edit.sent is not None
    keys = [image["key"] for image in _judged().images]
    assert keys.count(job.library.get(number=back).file) == 1
    assert keys.count(job.library.get(number=1).file) == 1
    views = [p["file"] for p in edit.sent["photos"] if p.get("page_view")]
    assert set(views) <= set(keys)


def test_without_views_the_judges_instructions_are_unchanged(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    job = Job.objects.get()
    every_side = [
        number
        for number in job.library.values_list("number", flat=True)
        if job.library.get(number=number).alone == "yes"
    ]
    picks = [{"item": number, "take": "Copy the shape."} for number in every_side]
    _scene(
        fake_model,
        say,
        ("draw_starting_picture", {**PICTURE_FORM, "picks": picks}),
        ("film", FILM),
        ("give_up", {"reason": "Enough for this test."}),
    )
    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert clip.sent is not None
    assert {p["tier"] for p in clip.sent["picture_photos"]} == {PICK}
    [told] = instructed["check_clip_product"]
    assert told == clip_judge.check_words(clip_judge.PRODUCT_CHECK, scene=True)
    assert "an extra view from the page" not in str(_judged().images)


def test_a_view_the_library_no_longer_holds_is_left_out_of_the_judge_with_a_notice(
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
    back_file = job.library.get(number=back).file
    _scene(
        fake_model,
        say,
        ("draw_starting_picture", PICTURE_FORM),
        ("give_up", {"reason": "Wait for the owner."}),
    )
    run(fake_model, steps)
    job.library.filter(number=back).delete()
    _scene(fake_model, say, ("film", FILM), ("give_up", {"reason": "Enough for this test."}))
    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert clip.sent is not None
    assert back not in [p["library_number"] for p in clip.sent["picture_photos"]]
    assert back_file not in [image["key"] for image in _judged().images]
    assert (
        "problem",
        f"Scene 2's clip: the page's view {back} was left out of the clip judge, as the "
        "library no longer holds it.",
    ) in recorded(api, session_id)
