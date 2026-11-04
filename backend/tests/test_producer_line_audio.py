"""Each scene's line spoken, and what was heard written down, driven through the chat. The
producer's model is faked at the gateway to call the scene tools on an ad whose script has
passed its checks. Both tools work in the background: here the work is held until a test
runs it, so a test can see what the tool handed back before the work is done, and what the
producer was told once it was."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import (
    HeldSteps,
    given_to_the_producer,
    producer_turns,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

LINE = "Meet the Stoneware Mug from Kiln & Co."
# The fake voice says the line's 8 words, "&" among them, at 2 a second.
SECONDS = 4.0

AUDIO_STARTED = "Started scene 1's audio. It isn't made yet: you'll be told when it's ready."
TRANSCRIBING = "Started transcribing scene 1's audio. It isn't done yet: you'll be told when it is."
AUDIO_READY = (
    "Background step finished: scene 1's line's audio is ready (version 1, 4 seconds). It "
    "cost $0.00095. It isn't shown to the shop owner. Transcribe it next. Tell the shop owner."
)
TRANSCRIBED = (
    "Background step finished: scene 1's audio (version 1) was transcribed (version 1). It "
    f'was heard as: "{LINE}" It cost $0.000244. Tell the shop owner.'
)

# Once CHOICE's picture is chosen, the starting picture of scene 1 can be made.
CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits a line that names it.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}


def calling(fake_model: FakeModel, tool: str, reply: str = "On it.", scene: int = 1) -> None:
    """Script the producer to call `tool` for the scene, then reply."""
    fake_model.respond("produce", turn(calls=[(tool, {"scene": scene})]), turn(says=reply))


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


@pytest.fixture
def spoken(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 line has its audio."""
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio")
    fake_model.respond("produce", turn(says="Scene 1's audio is ready."))
    steps.run_held()


# --- In the background, through the chat ----------------------------------------------------


# --- What the tools refuse ------------------------------------------------------------------


@pytest.fixture
def unchecked(fake_model: FakeModel, planned: None, say: Callable[..., None]) -> None:
    """A chat whose ad has its person, but whose script hasn't been checked."""
    fake_model.respond("produce", turn(calls=[("create_person", {})]), turn(says="Meet her!"))
    say("Make the person")


# --- Versions -------------------------------------------------------------------------------


# --- Nothing is paid for twice --------------------------------------------------------------
