"""Making each scene, driven through the chat. The producer's model is faked at the gateway to
call the scene tools on an ad whose script has passed its checks. Scene tools start their
work in the background: here it is held until a test runs it, so a test can see what the
tool handed back before the work is done, and what the producer was told once it was."""

from datetime import datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job, SceneStep

from .conftest import (
    given_to_the_producer,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("legacy_path")]

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


# --- A step that fails ----------------------------------------------------------------------


# --- What the picture is made from ----------------------------------------------------------


LARGE_POSE = (
    "The product stands on the floor at its real size, and the person stands beside it with "
    "one hand resting on it, framed so both fit, with space above the head and below the "
    "product, so the top and bottom of the frame stay clear."
)
HANDHELD_POSE = (
    "The person holds the product at chest height, beside their face, with any label facing "
    "the camera, so the top and bottom of the frame stay clear."
)


# --- Nothing is paid for twice --------------------------------------------------------------


def seen_at() -> datetime:
    """When the one step was last seen alive."""
    return SceneStep.objects.get().seen_at


# --- Several scenes at the same time --------------------------------------------------------


def steps_told(turn_number: int) -> list[str]:
    """Which scenes' steps the producer had been told had finished, on its `turn_number`th
    turn, by the start of what it was told."""
    return [
        each["text"].split("'s starting picture")[0].removeprefix("Background step finished: ")
        for each in given_to_the_producer(turn_number)
        if each["kind"] == "step_finished"
    ]
