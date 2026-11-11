"""Talking scenes filmed in two real camera setups, selfie and propped (Julian graded the
talking-angles test 4 of 4 better, Nov 9 2026). Each test runs through the chat with outside
models scripted, except the planner's prompt and plan checks, which are pure."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem, Scene
from jobs.scene_pictures import matches
from jobs.work import latest, scene_place

from .conftest import HeldSteps, handoffs
from .test_planning import DECISION, as_planned, lens_opening, pouring, sipping_close
from .test_producer_broll import calling, run
from .test_producer_person_and_room import (  # noqa: F401 (fixtures)
    five_shown,
    make_scene_clips,
    owner_says,
)
from .test_scene_agent import scene_agent_ready  # noqa: F401 (a fixture)

Say = Callable[..., None]

# The selfie setup and reference jobs wired into a talking scene.
SELFIE = (
    "a vertical phone selfie video frame from her front camera, the phone held by her at "
    "arm's length, close to her, a little above her eye level and angled slightly down; "
    "her phone arm reaches out of the frame toward the camera, her shoulder and upper arm "
    "in view at the frame's edge; a medium close-up taken close to her face through the "
    "phone's wide front lens: her face takes up about a third of the frame's width, the top "
    "of her head near the top edge, her eyes in the upper third, the frame cut at mid-chest, "
    "a little off-centre; with her other hand she holds the product up beside her face at "
    "chest height, close to the lens and large in the lower third of the frame; she talks "
    "to the lens mid-sentence"
)
PORTRAIT_JOB = (
    "the person, the same person with the same face; take only the face and hair from it, "
    "everything else comes from these words."
)
ROOM_JOB = (
    "the room, empty; draw the person in this room with its look and its light, seen from "
    "where these words put the camera."
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_a_plan_with_neighbouring_talking_scenes_in_one_setup_still_goes_ahead() -> None:
    reply = as_planned(lens_opening(), pouring(), sipping_close(talking_style="selfie"))

    assert DECISION.model_validate(reply).plan is not None


@pytest.mark.django_db(transaction=True)
def test_her_talking_picture_says_the_setup_first_and_takes_her_portrait_and_the_room_alone(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    Scene.objects.filter(number=1).update(product_state="beside its box", packaging_photo=2)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    facts = _flat(handoffs("scene_agent")[0]["conversation"][0]["text"])
    assert f"talking style: selfie ({SELFIE})" in facts
    prompt = _flat(handoffs("draw_scene_picture")[0]["prompt"])
    assert prompt.startswith(f"How she is filmed: {SELFIE}. Where the product is: ")
    assert "Image 2: the product's packaging; draw its box as this photo shows it." in prompt
    assert f"Image 3: {PORTRAIT_JOB} Image 4: {ROOM_JOB}" in prompt
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    job = made.job
    portrait, room = (
        latest(job, ProducedItem.Kind.PORTRAIT),
        latest(job, ProducedItem.Kind.ROOM_ALONE),
    )
    assert portrait is not None and room is not None
    drawn_from = [photo["file"] for photo in made.sent["photos"]]
    assert drawn_from[-2:] == [portrait.file, room.file]
    scene = Scene.objects.get(number=1)
    _, shared = scene_place(scene, scene.person_shown)
    assert shared is not None and shared.file not in drawn_from


@pytest.mark.django_db(transaction=True)
def test_a_talking_picture_from_either_inputs_can_be_filmed_but_not_from_the_room_alone(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    made = ProducedItem.objects.get(kind="starting_picture", scene__number=1)
    assert made.sent is not None
    scene = Scene.objects.get(number=1)
    assert matches(made, scene)
    products = [photo for photo in made.sent["photos"] if photo.get("library_number")]
    _, shared = scene_place(scene, scene.person_shown)
    room = latest(made.job, ProducedItem.Kind.ROOM_ALONE)
    assert shared is not None and room is not None
    # Drawn before this change, from the shared picture of her in the room.
    made.sent["photos"] = [*products, {"file": shared.file, "line": "Image 2 shows ..."}]
    assert matches(made, scene)
    made.sent["photos"] = [*products, {"file": room.file, "line": "Image 2 shows ..."}]
    assert not matches(made, scene)


@pytest.mark.django_db(transaction=True)
def test_her_talking_picture_leaves_room_in_the_16_pictures_for_her_portrait_and_the_room(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    from .test_scene_pictures import PICTURE_FORM

    picks: list[dict[str, Any]] = [{"item": 1, "take": "The mug."}] * 15
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Open on it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    refused = _flat(handoffs("scene_agent")[1]["conversation"][-1]["result"])
    assert refused.startswith("Refused: 14 at most for her talking")
    assert not handoffs("draw_scene_picture")


@pytest.mark.django_db(transaction=True)
def test_her_talking_in_an_edited_place_is_drawn_from_the_edited_picture_of_her(
    fake_model: FakeModel,
    five_shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    # No room alone shows an edited place, so the edited picture of her is kept.
    job = Job.objects.get()
    owner_says(
        fake_model,
        say,
        "Make the opening brighter.",
        ("set_place", {"scene": 1, "edit": "Make the light brighter."}),
    )
    edited = Scene.objects.get(number=1).produced.get(kind="scene_person_in_room")

    make_scene_clips(fake_model, steps, say, (1,))

    clip = job.produced.filter(kind="clip", scene__number=1).latest("version")
    assert clip.picture is not None and clip.picture.sent is not None
    drawn_from = [photo["file"] for photo in clip.picture.sent["photos"]]
    assert drawn_from[-1] == edited.file
    assert job.produced.get(kind="portrait").file not in drawn_from
    assert job.produced.get(kind="room_alone").file not in drawn_from
