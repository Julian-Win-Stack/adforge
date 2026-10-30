"""Making each scene, driven through the chat. The producer's model is faked at the gateway to
call the scene tools on an ad whose script has passed its checks. Scene tools start their
work in the background: here it is held until a test runs it, so a test can see what the
tool handed back before the work is done, and what the producer was told once it was."""

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from adforge.retry import OutsideServiceDown
from agents import tasks
from chat import messages
from chat.models import Message
from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs.models import Job, Scene, SceneStep
from jobs.scenes import pose_for

from .conftest import (
    HeldSteps,
    WorkerStopped,
    a_plan_with,
    chat,
    given_to_the_producer,
    handoffs,
    paid_for,
    producer_turns,
    recorded,
    results_of,
)
from .test_producer_messages import HEARTBEAT_SECONDS, beat_after

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# What the model that chooses scene 1's starting picture answers: photo 1 is the only one
# showing the mug in the plan's colour.
CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits a line that names it.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}

STARTED = "Started scene 1's starting picture. It isn't made yet: you'll be told when it's ready."


def starting_pictures() -> list[tuple[int, int]]:
    """Each starting picture made, as (scene, version)."""
    return [
        (item.scene.number, item.version)
        for item in Job.objects.get().produced.filter(kind="starting_picture").order_by("id")
        if item.scene is not None
    ]


def test_a_starting_picture_is_made_in_the_background_and_the_producer_tells_the_shop_owner(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )

    say("Make scene 1's starting picture")

    # The tool handed back before the picture was made, and the producer replied.
    assert results_of("make_starting_picture") == [STARTED]
    assert starting_pictures() == []
    assert SceneStep.objects.get().status == "running"
    assert chat(api, session_id)[-1] == ("agent", "I've started scene 1's picture.")

    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))
    steps.run_held()

    assert starting_pictures() == [(1, 1)]
    assert SceneStep.objects.get().status == "finished"
    # The producer was told by the system, not by the shop owner, and told them.
    assert given_to_the_producer(producer_turns())[-1] == {
        "kind": "step_finished",
        "text": "Background step finished: scene 1's starting picture is ready (version 1), "
        f"and is shown to the shop owner in the chat. Photo 1 was used: {CHOICE['photo_reason']} "
        "It cost $0.0125. Tell the shop owner.",
    }
    shown, told = api.get(f"/api/sessions/{session_id}/messages/").json()[-2:]
    # Shown with no words, carrying its scene number.
    assert shown["text"] == ""
    assert [(attached["kind"], attached["scene"]) for attached in shown["attachments"]] == [
        ("picture", 1)
    ]
    assert (told["role"], told["text"]) == ("agent", "Scene 1's starting picture is ready!")


def step_messages(api: APIClient, session_id: str) -> list[tuple[str, str, str, Any]]:
    """Each message in the chat that says a scene's step started or ended, as (role,
    level, text, step)."""
    return [
        (message["role"], message["level"], message["text"], message["step"])
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["step"] is not None
    ]


def picture_step(state: str, scene: int = 1) -> dict[str, Any]:
    return {"scene": scene, "kind": "starting_picture", "state": state}


def test_the_chat_can_tell_a_picture_is_being_made_until_shown_and_the_producer_isnt_given_it(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None), reply="I've started scene 1's picture.")

    say("Make scene 1's starting picture")

    assert step_messages(api, session_id) == [
        ("notice", "info", "Making scene 1's starting picture.", picture_step("started"))
    ]

    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))
    steps.run_held()

    # The picture's own message says the step that started has ended with it.
    assert step_messages(api, session_id)[1:] == [("agent", "", "", picture_step("made"))]
    # That message came between the producer's reply and the step: it is left out of what
    # the producer is given, as a producer given it as "[Attached 1 picture]" copied that as
    # a reply.
    assert given_to_the_producer(producer_turns())[-2] == {
        "kind": "said",
        "by": "agent",
        "text": "I've started scene 1's picture.",
    }


def test_the_chat_can_tell_a_scenes_starting_picture_is_being_made_again(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Ready!"))
    steps.run_held()
    making(fake_model, (1, "Smiling more."))

    say("Make it again, smiling more")

    assert step_messages(api, session_id)[-1] == (
        "notice",
        "info",
        "Remaking scene 1's starting picture.",
        picture_step("started"),
    )


def making(fake_model: FakeModel, *scenes: tuple[int, str | None], reply: str = "On it.") -> None:
    """Script the producer to start the starting picture for each of `scenes`, given as
    (scene, note), one turn each, then reply."""
    fake_model.respond(
        "produce",
        *(
            turn(calls=[("make_starting_picture", {"scene": scene, "note": note})])
            for scene, note in scenes
        ),
        turn(says=reply),
    )


# --- What the tool refuses ------------------------------------------------------------------
# A line that hasn't passed the fact check is refused by every scene tool alike: see
# test_producer_line_audio.py.


def test_a_starting_picture_already_being_made_isnt_started_again(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    making(fake_model, (1, None), (1, "Outdoors, please."))

    say("Make scene 1's starting picture")

    assert results_of("make_starting_picture") == [
        STARTED,
        "Refused: scene 1's starting picture is already being made. You'll be told when it's "
        "ready. Nothing was done.",
    ]
    assert SceneStep.objects.count() == 1


def test_a_scene_gets_no_starting_picture_before_the_person_is_made(
    fake_model: FakeModel, planned: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Checked before the checks needed the person's voice, as a job from before #66 was.
    Scene.objects.update(fact_checked=True)
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I need to make the person first."),
    )

    say("Make scene 1's starting picture")

    assert results_of("make_starting_picture") == [
        "Refused: the person hasn't been made yet, and the picture shows them. Create the "
        "person first. Nothing was done."
    ]
    assert not SceneStep.objects.exists()


def test_a_scene_the_ad_doesnt_have_gets_no_starting_picture(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    making(fake_model, (4, None))

    say("Make scene 4's starting picture")

    assert results_of("make_starting_picture") == [
        "Refused: the ad has no scene 4: its scenes are 1 to 3. Nothing was done."
    ]
    assert not SceneStep.objects.exists()


# --- A step that fails ----------------------------------------------------------------------


def test_a_starting_picture_that_cant_be_made_fails_its_step_and_the_producer_is_told(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    sentry_events: list[dict[str, Any]],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", *[OutsideServiceDown("503 from OpenAI")] * 3)
    fake_model.respond("produce", turn(says="Sorry, the picture service is down."))

    steps.run_held()

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert step.reason.startswith("an outside service stayed down after several tries")
    assert starting_pictures() == []
    assert given_to_the_producer(producer_turns())[-1] == {
        "kind": "step_finished",
        "text": "Background step failed: scene 1's starting picture couldn't be made: "
        f"{step.reason} Take a way on; tell the shop owner only if they can help.",
    }
    assert chat(api, session_id)[-1] == ("agent", "Sorry, the picture service is down.")
    # The chat can tell the picture is no longer being made, from a notice with no words; why,
    # in plain words, is recorded for the developer (spec #272). The error, worded for a
    # model, isn't shown to the shop owner.
    assert step_messages(api, session_id)[-1] == (
        "notice",
        "info",
        "",
        picture_step("not_made"),
    )
    assert (
        "problem",
        "Scene 1's starting picture couldn't be made: an outside service stayed down.",
    ) in recorded(api, session_id)
    assert "503" not in str(chat(api, session_id))
    # Sent to Sentry as its kind, tagged with the scene and the step it happened in.
    [sent] = [e for e in sentry_events if e["fingerprint"] == ["notice", "step_not_made"]]
    assert (sent["tags"]["scene"], sent["tags"]["step"]) == ("1", "starting_picture")
    assert sent["tags"]["session"] == session_id


def test_a_starting_picture_the_safety_filter_refuses_tells_the_shop_owner_so(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", BlockedBySafetyFilter("content_filter"))
    fake_model.respond("produce", turn(says="The picture was blocked."))

    steps.run_held()

    assert SceneStep.objects.get().status == "failed"
    assert step_messages(api, session_id)[-1][3] == picture_step("not_made")
    assert recorded(api, session_id)[-1] == (
        "problem",
        "Scene 1's starting picture couldn't be made: the AI service's safety filter blocked it.",
    )


def test_a_step_that_breaks_while_showing_its_picture_isnt_left_running(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Sorry, something went wrong."))
    adding = messages.add
    broken: list[bool] = []

    def breaks_once(*args: Any, **kwargs: Any) -> Message:
        if not broken:
            broken.append(True)
            raise RuntimeError("the database went away")
        return adding(*args, **kwargs)

    monkeypatch.setattr(messages, "add", breaks_once)

    steps.run_held()

    step = SceneStep.objects.get()
    assert (step.status, step.reason) == (
        "failed",
        "an unexpected error stopped the step. The details are in the server log.",
    )
    assert given_to_the_producer(producer_turns())[-1]["text"].startswith(
        "Background step failed: scene 1's starting picture couldn't be made"
    )
    assert chat(api, session_id)[-1] == ("agent", "Sorry, something went wrong.")
    assert step_messages(api, session_id)[-1][3] == picture_step("not_made")
    assert recorded(api, session_id)[-1] == (
        "problem",
        "Scene 1's starting picture couldn't be made: something went wrong on our side.",
    )


def test_a_photo_the_job_doesnt_have_is_never_used(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    # The job has photos 1 and 2: there is no Photo 3.
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", {**CHOICE, "photo": 3})
    fake_model.respond("produce", turn(says="Something went wrong with the picture."))

    steps.run_held()

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert "Photo 3 isn't one of the product photos: those are 1, 2." in step.reason
    assert "make_starting_picture" not in paid_for()
    assert step_messages(api, session_id)[-1][3] == picture_step("not_made")
    assert recorded(api, session_id)[-1] == (
        "problem",
        "Scene 1's starting picture couldn't be made: a model gave an answer we couldn't use.",
    )


def test_with_one_product_photo_the_model_isnt_asked_which_and_it_is_used(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Job.objects.get().photos.filter(position=2).delete()
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    # With only Photo 1 to pick: asked for the photo anyway, a model once answered with
    # another number, and nothing was made (case C12b).
    asked: list[dict[str, Any]] = []

    def answer(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request.output.model_json_schema())
        return {key: value for key, value in CHOICE.items() if key != "photo"}

    fake_model.answer_unscripted("choose_starting_picture", answer)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    steps.run_held()

    assert "photo" not in asked[0]["properties"]
    step = SceneStep.objects.get()
    assert step.status == "finished"
    assert step.photo is not None and step.photo.position == 1


# --- What the picture is made from ----------------------------------------------------------


def test_the_photo_and_prompt_are_chosen_from_the_line_and_the_picture_made_from_them(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (2, "  She is outdoors,\n in the garden. "))
    say("Make scene 2's starting picture in the garden")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 2's picture is ready!"))

    steps.run_held()

    job = Job.objects.get()
    portrait = job.produced.get(kind="portrait")
    front = job.photos.get(position=1)
    (chose,) = handoffs("choose_starting_picture")
    assert chose == {
        "scene": 2,
        "line": "Hand-thrown, holds 350 ml, and dishwasher safe.",
        "script": [
            "Meet the Stoneware Mug from Kiln & Co.",
            "Hand-thrown, holds 350 ml, and dishwasher safe.",
            "Yours for $24.00.",
        ],
        "product_colour": "sage green",
        "colour_photos": [1],
        "person_looks": "A potter in her thirties in a linen apron, in a sunny workshop.",
        # The mug is handheld, so the pose is the one for a product held in the hands.
        "pose": (
            "The person holds the product at chest height, beside their face, with any "
            "label facing the camera, so the top and bottom of the frame stay clear."
        ),
        "note": "She is outdoors, in the garden.",
        "conversation": chose["conversation"],
    }
    # The conversation as it was when the step started, so a step run again is given the same.
    assert chose["conversation"][-1] == {
        "by": "user",
        "text": "Make scene 2's starting picture in the garden",
    }
    # Shown the portrait and every product photo, the cream photo 2 too: "colour_photos" says
    # which show the ad's colour.
    assert ModelCall.objects.get(purpose="choose_starting_picture").images == [
        {"label": "The portrait", "key": portrait.file},
        {"label": "Photo 1", "key": front.file},
        {"label": "Photo 2", "key": job.photos.get(position=2).file},
    ]
    assert handoffs("make_starting_picture") == [
        {"prompt": CHOICE["prompt"], "pictures": [portrait.file, front.file]}
    ]
    made = ModelCall.objects.get(purpose="make_starting_picture")
    picture = job.produced.get(kind="starting_picture")
    assert (picture.scene, picture.version, picture.file) == (
        job.scenes.get(number=2),
        1,
        (made.output or {})["file"],
    )
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()[-2]
    assert [attached["url"] for attached in shown["attachments"]] == [f"/media/{picture.file}"]


LARGE_POSE = (
    "The product stands on the floor at its real size, and the person stands beside it with "
    "one hand resting on it, framed so both fit, with space above the head and below the "
    "product, so the top and bottom of the frame stay clear."
)
HANDHELD_POSE = (
    "The person holds the product at chest height, beside their face, with any label facing "
    "the camera, so the top and bottom of the frame stay clear."
)


@pytest.mark.parametrize(
    ("product_size", "pose"),
    [
        pytest.param("large", LARGE_POSE, id="large"),
        pytest.param(
            "tiny",
            "The person holds the product up close to the camera between finger and thumb, "
            "so it fills a good part of the frame, with any label facing the camera, and "
            "the top and bottom of the frame stay clear.",
            id="tiny",
        ),
        pytest.param("handheld", HANDHELD_POSE, id="handheld"),
        # A job planned before the plan said the size: posed as handheld, as every job was.
        pytest.param("", HANDHELD_POSE, id="no size stored"),
    ],
)
def test_the_pose_the_picture_is_planned_around_comes_from_the_products_size(
    product_size: str, pose: str
) -> None:
    """The plan says how big the product is; code, not the model, turns that into the pose
    the picture is planned around, so a chair is never shrunk to fit a chest."""
    assert pose_for(product_size) == pose


@pytest.mark.parametrize(
    "the_plan", [a_plan_with(product_size="large")], indirect=True, ids=["large"]
)
def test_the_picture_is_planned_around_the_pose_for_the_plans_product_size(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's picture is ready!"))

    steps.run_held()

    (chose,) = handoffs("choose_starting_picture")
    assert chose["pose"] == LARGE_POSE


def test_the_chosen_photo_and_prompt_are_kept_with_why_and_the_photos_are_never_changed(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    job = Job.objects.get()
    photos_before = [(photo.position, photo.file, read(photo.file)) for photo in job.photos.all()]
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Ready!"))

    steps.run_held()

    step = SceneStep.objects.get()
    assert (step.photo, step.photo_reason, step.prompt, step.prompt_reason) == (
        job.photos.get(position=1),
        CHOICE["photo_reason"],
        CHOICE["prompt"],
        CHOICE["prompt_reason"],
    )
    assert step.produced.get() == job.produced.get(kind="starting_picture")
    photos_after = [(photo.position, photo.file, read(photo.file)) for photo in job.photos.all()]
    assert photos_after == photos_before


def test_a_starting_picture_made_again_is_a_new_version_and_the_old_one_is_kept(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Ready!"))
    steps.run_held()
    first = Job.objects.get().produced.get(kind="starting_picture")
    first_picture = read(first.file)

    making(fake_model, (1, "Outdoors, please."))
    say("Make it again, outdoors")
    fake_model.respond("choose_starting_picture", {**CHOICE, "prompt": "She is outdoors."})
    fake_model.respond("produce", turn(says="Here's the new one!"))
    steps.run_held()

    assert starting_pictures() == [(1, 1), (1, 2)]
    assert read(first.file) == first_picture
    assert given_to_the_producer(producer_turns())[-1]["text"].startswith(
        "Background step finished: scene 1's starting picture is ready (version 2)"
    )


# --- Nothing is paid for twice --------------------------------------------------------------


def test_a_starting_picture_already_made_is_handed_back_and_charges_nothing(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Ready!"))
    steps.run_held()
    paid = paid_for()

    making(fake_model, (1, None))
    say("Make scene 1's starting picture")

    assert paid_for() == paid
    assert steps.held == []
    assert SceneStep.objects.count() == 1
    # The choice, 1,000 tokens in at $2/M and 100 out at $10/M, and the picture, 500 words in
    # at $5/M, 500 pictures in at $8/M and 100 out at $30/M.
    assert results_of("make_starting_picture")[-1] == (
        "Scene 1's starting picture was already made for this line with no note (version 1), "
        "and the shop owner has seen it, so nothing was made or paid for again. Making it cost "
        f"$0.0125. Photo 1 was used: {CHOICE['photo_reason']}"
    )


def test_a_step_run_again_after_its_worker_stopped_pays_for_nothing_twice(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    (step_id,) = steps.held
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", WorkerStopped())

    with pytest.raises(WorkerStopped):
        steps.run_next()
    # Run again, the choice paid for is answered from its record.
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert paid_for().count("choose_starting_picture") == 1
    assert paid_for().count("make_starting_picture") == 1
    assert starting_pictures() == [(1, 1)]
    # Run twice, it was told of once: started, then made.
    assert [step for _, _, _, step in step_messages(api, session_id)] == [
        picture_step("started"),
        picture_step("made"),
    ]


def test_a_step_working_when_the_worker_stopped_fails_as_the_worker_starts_and_the_producer_is_told(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    (step_id,) = steps.held
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("make_starting_picture", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()

    fake_model.respond("produce", turn(says="Scene 1's picture was interrupted. Shall I retry?"))
    tasks.carry_on_after_the_worker_starts()

    step = SceneStep.objects.get()
    assert step.status == "failed"
    assert step.reason == "The worker restarted while it was being made."
    # Not run again, so it can't run twice at once if Celery hands it out too.
    assert steps.held == []
    tasks.run_scene_step(step_id)
    assert paid_for().count("make_starting_picture") == 0
    assert chat(api, session_id)[-1] == (
        "agent",
        "Scene 1's picture was interrupted. Shall I retry?",
    )
    # Told once that it started and once, with no words, that it ended with nothing made;
    # why is recorded for the developer.
    assert [(text, step) for _, _, text, step in step_messages(api, session_id)] == [
        ("Making scene 1's starting picture.", picture_step("started")),
        ("", picture_step("not_made")),
    ]
    assert recorded(api, session_id)[-1] == (
        "problem",
        "Scene 1's starting picture couldn't be made: it was stopped partway.",
    )


def test_a_step_handed_out_again_once_it_has_finished_is_left_alone(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    (step_id,) = steps.held
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Ready!"))
    steps.run_next()
    conversation = chat(api, session_id)

    # Celery hands the same step out a second time, as it may after a crash.
    tasks.run_scene_step(step_id)

    assert paid_for().count("make_starting_picture") == 1
    assert starting_pictures() == [(1, 1)]
    assert chat(api, session_id) == conversation


def test_a_picture_paid_for_before_the_worker_stopped_is_kept_rather_than_made_again(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    (step_id,) = steps.held
    fake_model.respond("choose_starting_picture", CHOICE)

    def the_worker_stops(*_: object, **__: object) -> None:
        raise WorkerStopped

    # The worker stops once the picture is made and kept, before it is shown.
    with monkeypatch.context() as stopping:
        stopping.setattr("chat.messages.add", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert paid_for().count("make_starting_picture") == 1
    assert starting_pictures() == [(1, 1)]
    shown = [
        message
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["attachments"] and message["role"] == "agent"
    ]
    assert len(shown) == 2  # The person, then the picture, once.


def seen_at() -> datetime:
    """When the one step was last seen alive."""
    return SceneStep.objects.get().seen_at


def test_a_step_beats_while_it_works(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Settings,
) -> None:
    settings.STEP_HEARTBEAT_SECONDS = HEARTBEAT_SECONDS
    making(fake_model, (1, None))
    say("Make scene 1's starting picture")
    beats: list[datetime] = []

    def choosing_takes_a_while() -> dict[str, Any]:
        beats.append(seen_at())
        beats.append(beat_after(beats[0], seen_at))
        return CHOICE

    fake_model.respond("choose_starting_picture", choosing_takes_a_while)
    fake_model.respond("produce", turn(says="Ready!"))
    steps.run_held()
    ended = seen_at()

    assert beats[1] > beats[0]
    # It stops beating once the step is done: three heartbeats later, none has landed.
    waited_until = time.monotonic() + 3 * HEARTBEAT_SECONDS
    while time.monotonic() < waited_until:
        assert seen_at() == ended
        time.sleep(HEARTBEAT_SECONDS / 5)


# --- Several scenes at the same time --------------------------------------------------------


def steps_told(turn_number: int) -> list[str]:
    """Which scenes' steps the producer had been told had finished, on its `turn_number`th
    turn, by the start of what it was told."""
    return [
        each["text"].split("'s starting picture")[0].removeprefix("Background step finished: ")
        for each in given_to_the_producer(turn_number)
        if each["kind"] == "step_finished"
    ]


def test_scenes_are_made_at_the_same_time_and_a_result_arriving_mid_turn_isnt_lost(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("make_starting_picture", {"scene": 1, "note": None}),
                ("make_starting_picture", {"scene": 2, "note": None}),
                ("make_starting_picture", {"scene": 3, "note": None}),
            ]
        ),
        turn(says="I've started all three scenes' pictures."),
    )
    say("Make every scene's starting picture")
    # Each scene's step is its own background task, all started before any finished.
    assert len(steps.held) == 3
    assert SceneStep.objects.filter(status="running").count() == 3
    before = producer_turns()

    fake_model.respond("choose_starting_picture", CHOICE, CHOICE, CHOICE)
    fake_model.respond(
        "produce",
        # Scene 2's picture is made while the producer works out what to say about scene 1's,
        # and scene 3's while it works on scene 2's.
        meanwhile(steps.run_next, turn(says="Scene 1's picture is ready!")),
        meanwhile(steps.run_next, turn(says="Scene 2's picture is ready!")),
        turn(says="Scene 3's picture is ready!"),
    )
    steps.run_next()

    assert starting_pictures() == [(1, 1), (2, 1), (3, 1)]
    # Each result reached the producer on the turn after it arrived, and none was lost.
    assert steps_told(before + 1) == ["scene 1"]
    assert steps_told(before + 2) == ["scene 1", "scene 2"]
    assert steps_told(before + 3) == ["scene 1", "scene 2", "scene 3"]
    assert producer_turns() == before + 3
    # Scene 2's result comes after the reply that hadn't seen it, not where it finished.
    second = given_to_the_producer(before + 2)
    assert second[-2] == {"kind": "said", "by": "agent", "text": "Scene 1's picture is ready!"}
    assert second[-1]["text"].startswith("Background step finished: scene 2's")
    assert not SceneStep.objects.filter(producer_read_at=None).exists()
    said = [text for role, text in chat(api, session_id) if role == "agent" and text]
    assert said[-3:] == [
        "Scene 1's picture is ready!",
        "Scene 2's picture is ready!",
        "Scene 3's picture is ready!",
    ]
