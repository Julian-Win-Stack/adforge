"""Each scene's clip: its starting picture animated to speak its line's audio, driven through
the chat. The producer's model and the video model are faked at the gateway. The clip is
made in the background: here the work is held until a test runs it, so a test can see what
the tool handed back before the clip is made, and what the producer was told once it was."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job, Scene, SceneStep

from .conftest import (
    HeldSteps,
    WorkerStopped,
    given_to_the_producer,
    paid_for,
    producer_turns,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

CLIP_STARTED = "Started scene 1's clip. It isn't made yet: you'll be told when it's ready."
# The fake voice says scene 1's 8 words at 2 a second, so its clip lasts as long.
CLIP_READY = (
    "Background step finished: scene 1's clip is ready (version 1, 4 seconds), made from "
    "starting picture version 1 and audio version 1. It cost $0.20. Scene 1 is finished. Tell "
    "the shop owner."
)

CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits a line that names it.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}


def calling(fake_model: FakeModel, tool: str, arguments: dict[str, Any] | None = None) -> None:
    """Script the producer to call `tool` for scene 1, then reply."""
    fake_model.respond(
        "produce", turn(calls=[(tool, arguments or {"scene": 1})]), turn(says="On it.")
    )


def made(kind: str) -> list[tuple[int, int]]:
    """Each item of `kind` made, as (scene, version)."""
    return [
        (item.scene.number, item.version)
        for item in Job.objects.get().produced.filter(kind=kind).order_by("id")
        if item.scene is not None
    ]


def told() -> str:
    """The last step result the producer was given, as of its last turn."""
    results: list[str] = [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ]
    return results[-1]


def refused(why: str) -> str:
    """What a refused make_clip hands back, once nothing was started or paid for."""
    assert not SceneStep.objects.filter(kind="clip").exists()
    assert "make_talking_clip" not in paid_for()
    return f"Refused: {why} Nothing was done."


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once, when the last of them finishes."""
    if steps.held:
        fake_model.respond("produce", turn(says="Done."))
    steps.run_held()


def clip_signals(api: APIClient, session_id: str) -> list[tuple[str, str, str, Any]]:
    """Each message in the chat that says a scene's clip started or ended, as (role, level,
    text, step)."""
    return [
        (message["role"], message["level"], message["text"], message["step"])
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["step"] is not None and message["step"]["kind"] == "clip"
    ]


@pytest.fixture
def pictured(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 has its starting picture."""
    calling(fake_model, "make_starting_picture", {"scene": 1, "note": None})
    say("Make scene 1's picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)


@pytest.fixture
def spoken(
    pictured: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 has its starting picture and its audio, not yet transcribed."""
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio")
    run(fake_model, steps)


@pytest.fixture
def ready(spoken: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 1 has its starting picture, and its audio, transcribed."""
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    run(fake_model, steps)


@pytest.fixture
def clipped(ready: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 1 has its clip."""
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    run(fake_model, steps)


# --- The whole scene, and the clip in the background -----------------------------------------


# --- What the tool refuses -------------------------------------------------------------------
# A line that hasn't passed the fact check is refused by every scene tool alike: see
# test_producer_line_audio.py.


# --- Versions, and nothing paid for twice ---------------------------------------------------


def the_worker_stops(*_: object, **__: object) -> None:
    raise WorkerStopped


# --- When the video model fails ---------------------------------------------------------------


def clip_step_failed() -> SceneStep:
    step = SceneStep.objects.get(kind="clip")
    assert step.status == "failed"
    assert made("clip") == []
    assert Scene.objects.get(number=1).status == "planned"
    assert told() == (
        "Background step failed: scene 1's clip couldn't be made: "
        f"{step.reason} Take a way on; tell the shop owner only if they can help."
    )
    return step


# --- A line that changes once its clip is made ---------------------------------------------
