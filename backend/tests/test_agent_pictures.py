"""An agent on the shared loop can call a tool whose result is pictures, each labelled by
code, and the model is shown them, with their labels, on its next turn (fix redesign,
decisions 3 and 17). A text-only tool's result is sent exactly as before."""

import base64
import io
from collections.abc import Callable
from decimal import Decimal
from typing import Any, ClassVar

import PIL.Image
import pytest
from django.db.models.signals import post_save
from pytest_httpserver import HTTPServer

from adforge import file_store
from agents import loop
from agents.loop import Agent, Tool, ToolResult
from agents.models import ToolCall
from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from gateway.gateway import use_model
from gateway.models import ModelCall
from gateway.types import Image, ToolUse
from tests.conftest import OpenAIText, openai_turn

pytestmark = pytest.mark.django_db(transaction=True)


def _picture(colour: tuple[int, int, int]) -> bytes:
    made = io.BytesIO()
    PIL.Image.new("RGB", (40, 60), colour).save(made, format="PNG")
    return made.getvalue()


class LookAtScene(Tool):
    """Show the scene's pictures."""

    name: ClassVar[str] = "look_at_scene"
    scene: int

    def run(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            text=f"Scene {self.scene}: its current picture, then version 1.",
            pictures=[
                Image(label=f"Scene {self.scene}, starting picture, v2 black toilet", key=CURRENT),
                Image(label=f"Scene {self.scene}, starting picture, v1 first version", key=FIRST),
            ],
        )


class JotDown(Tool):
    """Jot something down."""

    name: ClassVar[str] = "jot_down"
    text: str

    def run(self, call: ToolCall) -> str:
        return f"Noted: {self.text}"


LOOKER = Agent(
    name="looker",
    purpose="produce",
    instructions="Look before you decide.",
    tools=[LookAtScene, JotDown],
)

CURRENT = "pictures/current.png"
FIRST = "pictures/first.png"
LABELS = [
    "Scene 2, starting picture, v2 black toilet",
    "Scene 2, starting picture, v1 first version",
]


def _last_turn() -> ModelCall:
    last = ModelCall.objects.filter(purpose="produce").order_by("id").last()
    assert last is not None
    return last


@pytest.fixture
def session(session_id: str) -> Session:
    file_store.save(CURRENT, _picture((10, 10, 10)))
    file_store.save(FIRST, _picture((250, 250, 250)))
    started = Session.objects.get(pk=session_id)
    messages.add(started, role=Message.Role.USER, text="Make the toilet white again.")
    return started


def test_an_agent_is_shown_the_pictures_a_tool_gave_with_their_labels_in_order(
    fake_model: FakeModel, session: Session
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2})]),
        turn("Version 1 has the white toilet."),
    )

    loop.run(LOOKER, session)

    # The checkpoint keeps which pictures the tool gave, by label and key.
    call = ToolCall.objects.get(tool="look_at_scene")
    assert call.result == "Scene 2: its current picture, then version 1."
    assert call.pictures == [
        {"label": LABELS[0], "key": CURRENT},
        {"label": LABELS[1], "key": FIRST},
    ]
    first, second = fake_model.turns_taken
    assert first.images == ()
    # The next turn is sent the pictures themselves, labelled, in the order the tool gave.
    assert [(image.label, image.key) for image in second.images] == list(
        zip(LABELS, [CURRENT, FIRST], strict=True)
    )
    assert all(image.media_type == "image/png" and image.data for image in second.images)
    [used] = [each for each in second.handoff.conversation if isinstance(each, ToolUse)]
    assert [(shown.label, shown.key) for shown in used.pictures] == list(
        zip(LABELS, [CURRENT, FIRST], strict=True)
    )
    # Each picture sent is counted in the turn's record, as call_model's pictures are.
    turns = ModelCall.objects.filter(purpose="produce").order_by("id")
    assert [record.images for record in turns] == [
        [],
        [{"label": LABELS[0], "key": CURRENT}, {"label": LABELS[1], "key": FIRST}],
    ]
    assert session.messages.filter(role=Message.Role.AGENT).values_list(
        "text", flat=True
    ).last() == ("Version 1 has the white toilet.")


def test_a_restart_shows_the_pictures_again_the_same_way(
    fake_model: FakeModel, session: Session, settings: Any
) -> None:
    settings.RETRY_DELAYS_SECONDS = []
    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2})]),
        RuntimeError("the worker stopped"),
    )
    with pytest.raises(RuntimeError):
        loop.run(LOOKER, session)
    fake_model.respond("produce", turn("Version 1 has the white toilet."))

    # The new worker rebuilds the conversation from the checkpoints.
    loop.run(LOOKER, session)

    stopped, restarted = fake_model.turns_taken[1:]
    assert restarted.handoff == stopped.handoff
    assert [(image.label, image.key, image.data) for image in restarted.images] == [
        (image.label, image.key, image.data) for image in stopped.images
    ]
    assert [image.label for image in restarted.images] == LABELS
    assert ToolCall.objects.filter(tool="look_at_scene").count() == 1


class WorkerStopped(BaseException):
    """The worker running the agent was killed."""


def test_a_picture_turn_paid_for_when_the_worker_stopped_is_taken_again_without_paying(
    fake_model: FakeModel, session: Session
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("look_at_scene", {"scene": 2})]),
        turn("Version 1 has the white toilet."),
    )

    def stop(instance: ModelCall, **_: Any) -> None:
        if instance.images and instance.outcome == ModelCall.Outcome.SUCCEEDED:
            post_save.disconnect(stop, sender=ModelCall)
            raise WorkerStopped

    # The worker stops once the turn shown the pictures is paid for, before its words are kept.
    post_save.connect(stop, sender=ModelCall, weak=False)
    try:
        with pytest.raises(WorkerStopped):
            loop.run(LOOKER, session)
    finally:
        post_save.disconnect(stop, sender=ModelCall)

    # Nothing more is scripted: paying for the stopped turn again would fail the run.
    loop.run(LOOKER, session)

    assert len(fake_model.turns_taken) == 2
    assert ModelCall.objects.filter(purpose="produce").exclude(images=[]).count() == 1
    assert session.messages.filter(role=Message.Role.AGENT).values_list(
        "text", flat=True
    ).last() == ("Version 1 has the white toilet.")


def test_a_text_only_tool_is_kept_and_given_as_before(
    fake_model: FakeModel, session: Session
) -> None:
    fake_model.respond("produce", turn(calls=[("jot_down", {"text": "white"})]), turn("Noted it."))

    loop.run(LOOKER, session)

    assert ToolCall.objects.get().pictures == []
    second = fake_model.turns_taken[1]
    assert second.images == ()
    # The record of what the turn was given has no trace of pictures.
    record = _last_turn()
    assert record.images == []
    assert record.handoff["conversation"][-1] == {
        "kind": "tool_use",
        "call_id": ToolCall.objects.get().call_id,
        "tool": "jot_down",
        "arguments": {"text": "white"},
        "result": "Noted: white",
    }


def test_openai_is_sent_the_pictures_inside_the_tool_result(
    httpserver: HTTPServer,
    openai_server: Callable[..., None],
    session: Session,
    fake_model: FakeModel,
) -> None:
    del fake_model  # Turns go to the real OpenAI code below; nothing else is called.
    openai_server(
        openai_turn("", ("call_look", "look_at_scene", {"scene": 2})),
        openai_turn("", ("call_note", "jot_down", {"text": "white"})),
        openai_turn("Version 1 has the white toilet."),
    )

    with use_model(OpenAIText()):
        loop.run(LOOKER, session)

    sent = [request.get_json() for request, _ in httpserver.log if request.path == "/v1/responses"]
    last = sent[-1]["input"]
    outputs = {item["call_id"]: item for item in last if item.get("type") == "function_call_output"}
    # A text-only tool's result is the plain string it always was.
    assert outputs["call_note"]["output"] == "Noted: white"
    looked = outputs["call_look"]["output"]
    assert [part["type"] for part in looked] == [
        "input_text",
        "input_text",
        "input_image",
        "input_text",
        "input_image",
    ]
    assert looked[0]["text"] == "Scene 2: its current picture, then version 1."
    assert [looked[1]["text"], looked[3]["text"]] == LABELS
    for part, key in ((looked[2], CURRENT), (looked[4], FIRST)):
        assert part["detail"] == "high"
        assert part["image_url"].startswith("data:image/png;base64,")
        shown = PIL.Image.open(io.BytesIO(base64.b64decode(part["image_url"].split(",", 1)[1])))
        stored = PIL.Image.open(io.BytesIO(file_store.read(key)))
        assert shown.getpixel((0, 0)) == stored.getpixel((0, 0))
    # The pictures are counted in that turn's record.
    assert _last_turn().images == [
        {"label": LABELS[0], "key": CURRENT},
        {"label": LABELS[1], "key": FIRST},
    ]
    assert sum((record.cost_usd or Decimal(0)) for record in ModelCall.objects.all()) > Decimal(0)


class LookAtNothing(Tool):
    """Show a picture code forgot to label."""

    name: ClassVar[str] = "look_at_nothing"

    def run(self, call: ToolCall) -> ToolResult:
        return ToolResult(text="Here it is.", pictures=[Image(label="", key=CURRENT)])


def test_a_picture_with_no_label_fails_the_tool_and_the_agent_carries_on(
    fake_model: FakeModel, session: Session
) -> None:
    agent = Agent(name="looker", purpose="produce", instructions="", tools=[LookAtNothing])
    fake_model.respond(
        "produce", turn(calls=[("look_at_nothing", {})]), turn("I couldn't look at it.")
    )

    loop.run(agent, session)
    # A later run rebuilds the conversation from the checkpoint without tripping on it.
    messages.add(session, role=Message.Role.USER, text="Try again.")
    fake_model.respond("produce", turn("Still nothing to see."))
    loop.run(agent, session)

    call = ToolCall.objects.get()
    assert call.result.startswith("Failed:")
    assert call.pictures == []
    assert [len(each.images) for each in fake_model.turns_taken] == [0, 0, 0]


class LookAtText(Tool):
    """Show a file that isn't a picture."""

    name: ClassVar[str] = "look_at_text"

    def run(self, call: ToolCall) -> ToolResult:
        return ToolResult(text="Here it is.", pictures=[Image(label="Notes", key=NOTES)])


NOTES = "notes/scene.txt"


def test_a_picture_no_model_can_read_fails_the_tool_not_the_next_turn(
    fake_model: FakeModel, session: Session
) -> None:
    file_store.save(NOTES, b"not a picture")
    agent = Agent(name="looker", purpose="produce", instructions="", tools=[LookAtText])
    fake_model.respond(
        "produce", turn(calls=[("look_at_text", {})]), turn("I couldn't look at it.")
    )

    loop.run(agent, session)

    call = ToolCall.objects.get()
    assert call.result.startswith("Failed: Notes (notes/scene.txt) can't be opened")
    assert call.pictures == []
    assert fake_model.turns_taken[1].images == ()
