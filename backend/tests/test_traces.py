"""What is sent to Langfuse: each message's work as a tree of the producer's turns, the
tools each asked for, and the model calls each made. A test keeps what would be sent in
memory instead of sending it, and reads it back as the tree Langfuse would show."""

import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any, cast

import httpx
import pytest
from django.db.models.signals import post_save, pre_save
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import tracing
from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import SceneStep

from .conftest import READABLE, HeldSteps, chat, results_of

# The fixture for a chat with a clip made, and those it is built on, for pytest to find.
from .test_producer_clip import clipped, pictured, ready, spoken  # noqa: F401
from .test_producer_music import make_music
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops
from .test_producer_scenes import CHOICE

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class Seen:
    """One part of a trace as Langfuse would show it, with the parts inside it."""

    name: str
    kind: str
    trace_id: str
    attributes: dict[str, Any]
    inside: list[Seen] = field(default_factory=list)

    def value(self, name: str) -> Any:
        """One of the part's own values, such as "input" or "usage_details". Text is sent
        as it is, anything else as JSON."""
        sent = self.attributes[f"langfuse.observation.{name}"]
        try:
            return json.loads(sent)
        except json.JSONDecodeError:
            return sent

    def find(self, *, name: str | None = None, kind: str | None = None) -> list[Seen]:
        """Every part of this one, itself included, with this name, of this kind, or both,
        in the order they began."""
        found = [self] if name in (None, self.name) and kind in (None, self.kind) else []
        for each in self.inside:
            found += each.find(name=name, kind=kind)
        return found

    def outline(self, depth: int = 0) -> str:
        """The part and everything inside it, one per line, indented by how deep it is."""
        lines = [f"{'  ' * depth}{self.name} ({self.kind})"]
        lines += [each.outline(depth + 1) for each in self.inside]
        return "\n".join(lines)


class Traces:
    """Everything sent to Langfuse, kept in memory."""

    def __init__(self, exporter: InMemorySpanExporter, client: Langfuse) -> None:
        self._exporter = exporter
        self._client = client

    def all(self) -> list[Seen]:
        """Every part sent at the top of a trace, in the order they began. A trace can have
        more than one: its message's work picked up again later."""
        self._client.flush()
        spans = sorted(self._exporter.get_finished_spans(), key=lambda span: span.start_time or 0)
        seen = {span.context.span_id: _seen(span) for span in spans if span.context}
        traces = []
        for span in spans:
            assert span.context is not None
            parent = span.parent.span_id if span.parent else None
            if parent in seen:
                seen[parent].inside.append(seen[span.context.span_id])
            else:
                traces.append(seen[span.context.span_id])
        return traces


def _seen(span: ReadableSpan) -> Seen:
    attributes = dict(span.attributes or {})
    assert span.context is not None
    return Seen(
        span.name,
        str(attributes.get("langfuse.observation.type")),
        format(span.context.trace_id, "032x"),
        attributes,
    )


@pytest.fixture(scope="session")
def _langfuse() -> tuple[InMemorySpanExporter, Langfuse]:
    exporter = InMemorySpanExporter()
    # Pictures, audio and clips are uploaded apart from the trace. Here, Langfuse answers
    # that it has each one already, so nothing is uploaded.
    uploaded = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"mediaId": "", "uploadUrl": None})
    )
    client = Langfuse(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="http://langfuse.test",
        span_exporter=exporter,
        httpx_client=httpx.Client(transport=uploaded),
    )
    return exporter, client


@pytest.fixture
def traces(_langfuse: tuple[InMemorySpanExporter, Langfuse]) -> Iterator[Traces]:
    exporter, client = _langfuse
    exporter.clear()
    with tracing.use_client(client):
        yield Traces(exporter, client)


def test_a_messages_work_is_one_trace_of_turns_the_tools_they_asked_for_and_model_calls(
    traces: Traces, page_read: str, session_id: str
) -> None:
    (trace,) = traces.all()

    assert trace.outline() == dedent(
        """\
        producer (span)
          producer turn (agent)
            produce (generation)
            read_page (tool)
              check_page (generation)
              copy_page_text (generation)
              note_face (generation)
              note_face (generation)
          producer turn (agent)
            produce (generation)"""
    )
    assert trace.attributes["session.id"] == session_id
    assert trace.value("input") == {"message": f"Make an ad for {page_read}"}
    assert trace.value("output") == {"said": ["I read your mug's page."]}
    (read_page,) = trace.find(name="read_page")
    # Each side is named, so Langfuse doesn't show them as a chat's "User" and "Assistant".
    assert read_page.value("input") == {"arguments": {"link": page_read, "target_seconds": None}}
    assert read_page.value("output") == {"result": results_of("read_page")[0]}
    (check_page,) = trace.find(name="check_page")
    recorded = ModelCall.objects.get(purpose="check_page")
    assert check_page.attributes["langfuse.observation.model.name"] == "gpt-5-mini"
    # With no files shown or made, neither is listed.
    assert check_page.value("input") == {"handoff": recorded.handoff}
    assert check_page.value("output") == {
        "output": {
            "decision": "readable",
            "reason": "The page names the mug, its price and its size.",
        }
    }
    # The fake bills 1,000 tokens in and 100 out for every call.
    assert check_page.value("usage_details") == {"input": 1_000, "output": 100}
    assert recorded.cost_usd is not None
    assert check_page.value("cost_details") == {"total": float(recorded.cost_usd)}


def test_a_messages_work_is_grouped_with_the_rest_of_its_chat_session(
    traces: Traces, fake_model: FakeModel, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="Send me your product page."), turn(says="Thanks!"))

    say("Hi")
    say("Here it comes")

    assert [trace.attributes["session.id"] for trace in traces.all()] == [session_id] * 2


def test_a_scene_steps_model_calls_land_under_the_tool_call_that_started_it(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    # Once the producer's work on the message is over, as a step run by another worker is.
    steps.run_held()

    (tool,) = [
        found
        for trace in traces.all()
        for found in trace.find(name="make_starting_picture", kind="tool")
    ]
    assert tool.outline() == dedent(
        """\
        make_starting_picture (tool)
          scene 1's starting picture (span)
            choose_starting_picture (generation)
            make_starting_picture (generation)"""
    )


def test_a_messages_work_woken_again_by_a_scene_step_stays_in_the_messages_trace(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    steps.run_held()

    # After those of the messages that made the plan and the person.
    *_, first, woken = traces.all()
    assert woken.trace_id == first.trace_id
    assert (first.name, woken.name) == ("producer", "producer, woken by scene 1's starting picture")
    assert first.attributes["langfuse.trace.name"] == "Make scene 1's starting picture"
    assert woken.value("input") == {
        "message": "Make scene 1's starting picture",
        "woken_by": ["scene 1's starting picture"],
    }
    assert woken.value("output") == {"said": ["Scene 1's starting picture is ready!"]}
    assert woken.outline() == dedent(
        """\
        producer, woken by scene 1's starting picture (span)
          producer turn (agent)
            produce (generation)"""
    )


def test_a_new_message_starts_a_new_trace_named_after_it(
    traces: Traces, fake_model: FakeModel, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(says="Send me your product page."), turn(says="Thanks!"))

    say("Hi")
    say("Here it comes")

    first, second = traces.all()
    assert first.trace_id != second.trace_id
    assert [each.attributes["langfuse.trace.name"] for each in (first, second)] == [
        "Hi",
        "Here it comes",
    ]


def test_a_scene_step_lands_under_its_tool_call_run_again_after_the_worker_stopped(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("make_starting_picture", {"scene": 1, "note": None})])
    )
    # The step is started, then the worker stops before the tool's checkpoint is finished.
    tool_finishing = the_worker_stops(
        pre_save,
        ToolCall,
        when=lambda call: call.tool == "make_starting_picture" and call.finished_at is not None,
    )
    with tool_finishing, pytest.raises(WorkerStopped):
        say("Make scene 1's starting picture")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I've started scene 1's picture."))
    restart_dead_producers()
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    steps.run_held()

    first, again = [
        found
        for trace in traces.all()
        for found in trace.find(name="make_starting_picture", kind="tool")
    ]
    assert first.inside == []
    assert [step.name for step in again.inside] == ["scene 1's starting picture"]


def kinds_of_file(sent: list[str]) -> list[str]:
    """What kind of file each of the files sent is, from the reference Langfuse shows it by."""
    kinds = []
    for reference in sent:
        found = re.fullmatch(r"@@@langfuseMedia:type=([^|]+)\|id=[^|]+\|source=bytes@@@", reference)
        assert found, f"{reference!r} isn't a file Langfuse shows"
        kinds.append(found.group(1))
    return kinds


def test_pictures_and_audio_given_to_or_made_by_a_model_are_shown_in_the_trace(
    traces: Traces,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": None})]),
        turn(says="I've started scene 1's picture."),
    )
    say("Make scene 1's starting picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))
    steps.run_held()

    calls = {found.name: found for trace in traces.all() for found in trace.find(kind="generation")}
    assert kinds_of_file(calls["draw_person"].value("output")["made"]) == ["image/png"]
    assert kinds_of_file(calls["measure_voice"].value("output")["made"]) == ["audio/wav"]
    # The photos it chooses between, then the portrait and the chosen photo it is made from.
    assert (
        kinds_of_file(calls["choose_starting_picture"].value("input")["shown"]) == ["image/png"] * 2
    )
    made = calls["make_starting_picture"]
    assert kinds_of_file(made.value("input")["shown"]) == ["image/png"] * 2
    assert kinds_of_file(made.value("output")["made"]) == ["image/png"]


def test_a_turn_answered_from_its_record_shows_as_such_with_no_cost(
    traces: Traces,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    read_page = ("read_page", {"link": product_page_url, "target_seconds": None})
    fake_model.respond("produce", turn(says="I'll read your mug's page.", calls=[read_page]))
    turn_recorded = the_worker_stops(
        post_save, ModelCall, when=lambda call: call.purpose == "produce"
    )
    with turn_recorded, pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    the_producer_died(session_id)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("produce", turn(says="I read your mug's page."))

    restart_dead_producers()

    stopped, again = traces.all()
    assert (again.name, again.trace_id) == ("producer, started again", stopped.trace_id)
    assert [call.name for call in stopped.find(kind="generation")] == ["produce"]
    (answered, checked_after, copied_after, *noted, paid) = again.find(kind="generation")
    assert (answered.name, checked_after.name, copied_after.name, paid.name) == (
        "produce (answered from its record)",
        "check_page",
        "copy_page_text",
        "produce",
    )
    assert [call.name for call in noted] == ["note_face", "note_face"]
    assert answered.value("cost_details") == {"total": 0}
    paid_before = ModelCall.objects.filter(purpose="produce").first()
    assert paid_before is not None
    assert answered.value("output") == {"output": paid_before.output}


class Broken:
    """A Langfuse client whose every use fails."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Langfuse is broken")


def test_tracing_that_fails_never_fails_a_tool_a_turn_or_a_scene_step(
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
    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond("produce", turn(says="Scene 1's starting picture is ready!"))

    with tracing.use_client(cast(Langfuse, Broken())):
        say("Make scene 1's starting picture")
        steps.run_held()

    assert results_of("make_starting_picture") == [
        "Started scene 1's starting picture. It isn't made yet: you'll be told when it's ready."
    ]
    assert SceneStep.objects.get().status == "finished"
    assert chat(api, session_id)[-1] == ("agent", "Scene 1's starting picture is ready!")


def a_message_with_a_tool(fake_model: FakeModel, product_page_url: str) -> None:
    """Script the producer to read the page, then reply."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)


def test_langfuse_refusing_the_keys_never_fails_the_work(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    httpserver: HTTPServer,
    settings: Settings,
) -> None:
    settings.LANGFUSE_PUBLIC_KEY = f"pk-lf-wrong-{uuid.uuid4()}"
    settings.LANGFUSE_SECRET_KEY = "sk-lf-wrong"
    settings.LANGFUSE_BASE_URL = httpserver.url_for("/langfuse")
    httpserver.expect_request(re.compile("^/langfuse/")).respond_with_data("Unauthorized", 401)
    a_message_with_a_tool(fake_model, product_page_url)

    say(f"Make an ad for {product_page_url}")

    # It was sent, and refused.
    assert any(request.path.startswith("/langfuse/") for request, _ in httpserver.log)
    assert chat(api, session_id)[-1] == ("agent", "I read your mug's page.")


def test_with_no_langfuse_keys_nothing_is_sent(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    httpserver: HTTPServer,
    settings: Settings,
) -> None:
    settings.LANGFUSE_BASE_URL = httpserver.url_for("/langfuse")
    a_message_with_a_tool(fake_model, product_page_url)

    say(f"Make an ad for {product_page_url}")

    assert not [request for request, _ in httpserver.log if request.path.startswith("/langfuse")]


def test_a_clip_and_the_music_made_are_shown_in_the_trace(
    traces: Traces,
    fake_model: FakeModel,
    clipped: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    make_music(fake_model, say, "Light upbeat lo-fi.")

    calls = {found.name: found for trace in traces.all() for found in trace.find(kind="generation")}
    # The clip is asked for from the starting picture and the line's audio, then collected.
    assert kinds_of_file(calls["make_talking_clip"].value("input")["shown"]) == [
        "image/png",
        "audio/wav",
    ]
    assert kinds_of_file(calls["collect_talking_clip"].value("output")["made"]) == ["video/mp4"]
    assert kinds_of_file(calls["transcribe_line"].value("input")["shown"]) == ["audio/wav"]
    assert kinds_of_file(calls["make_music"].value("output")["made"]) == ["audio/mp4"]
