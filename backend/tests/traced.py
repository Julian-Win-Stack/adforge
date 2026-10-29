"""What is sent to Langfuse, kept in memory instead of being sent, and read back as the
tree Langfuse would show. The `traces` fixture (in conftest) gives it to a test."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from adforge import tracing


@dataclass
class Seen:
    """One part of a trace as Langfuse would show it, with the parts inside it."""

    name: str
    kind: str
    trace_id: str
    span_id: str
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

    def metadata(self) -> dict[str, Any]:
        """The part's metadata, each value as it was given."""
        prefix = "langfuse.observation.metadata."
        found = {}
        for name, sent in self.attributes.items():
            if name.startswith(prefix):
                try:
                    found[name.removeprefix(prefix)] = json.loads(sent)
                except json.JSONDecodeError, TypeError:
                    found[name.removeprefix(prefix)] = sent
        return found

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


@dataclass(frozen=True)
class Score:
    """A score sent to Langfuse, as it shows on the part it scores."""

    name: str
    value: Any
    comment: str | None


class Traces:
    """Everything sent to Langfuse, kept in memory."""

    def __init__(
        self, exporter: InMemorySpanExporter, client: Langfuse, ingested: list[dict[str, Any]]
    ) -> None:
        self._exporter = exporter
        self._client = client
        self._ingested = ingested

    def scores(self, part: Seen) -> list[Score]:
        """Every score sent for `part`, in the order sent."""
        self._client.flush()
        return [
            Score(body["name"], body["value"], body.get("comment"))
            for event in self._ingested
            if event["type"] == "score-create"
            for body in [event["body"]]
            if (body.get("traceId"), body.get("observationId")) == (part.trace_id, part.span_id)
        ]

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
        format(span.context.span_id, "016x"),
        attributes,
    )


@pytest.fixture(scope="session")
def _langfuse() -> tuple[InMemorySpanExporter, Langfuse, list[dict[str, Any]]]:
    exporter = InMemorySpanExporter()
    ingested: list[dict[str, Any]] = []

    # Scores are sent apart from the trace: they are kept here. Pictures, audio and clips
    # are uploaded apart from it too: here, Langfuse answers that it has each one already,
    # so nothing is uploaded.
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ingestion"):
            ingested.extend(json.loads(request.content)["batch"])
            return httpx.Response(207, json={"successes": [], "errors": []})
        return httpx.Response(200, json={"mediaId": "", "uploadUrl": None})

    uploaded = httpx.MockTransport(answer)
    # Made as the app makes its own, with the settings' environment.
    client = tracing.new_client(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="http://langfuse.test",
        span_exporter=exporter,
        httpx_client=httpx.Client(transport=uploaded),
    )
    return exporter, client, ingested


@pytest.fixture
def traces(
    _langfuse: tuple[InMemorySpanExporter, Langfuse, list[dict[str, Any]]],
) -> Iterator[Traces]:
    exporter, client, ingested = _langfuse
    exporter.clear()
    ingested.clear()
    with tracing.use_client(client):
        yield Traces(exporter, client, ingested)
