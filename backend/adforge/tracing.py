"""The one place that talks to Langfuse, where each message's work is sent to be looked at
as a tree: the producer's turns, the tools each asked for, and the model calls each made.

Langfuse is for looking at what the agents did, not for counting it: the model-call records
stay the only source of cost and time numbers. Tracing is off unless both Langfuse keys are
set, and a tracing failure is logged, never raised into the work it was tracing."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from decimal import Decimal
from functools import cache
from pathlib import PurePath
from typing import Any

from django.conf import settings
from langfuse import Langfuse, LangfuseMedia, propagate_attributes

from adforge import file_store

logger = logging.getLogger(__name__)

_override: Langfuse | None = None

# What each kind of file the work makes is, for Langfuse to show it.
_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
}


@contextmanager
def use_client(client: Langfuse) -> Iterator[None]:
    """Send everything traced inside this block to `client`. Tests use it to keep what
    would be sent in memory."""
    global _override
    previous, _override = _override, client
    try:
        yield
    finally:
        _override = previous


def _client() -> Langfuse:
    if _override is not None:
        return _override
    return _from_settings(
        settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY, settings.LANGFUSE_BASE_URL
    )


# Made the first time it is needed, in the process that uses it: Celery forks its workers,
# and the thread that sends traces doesn't survive a fork.
@cache
def _from_settings(public_key: str, secret_key: str, base_url: str) -> Langfuse:
    return Langfuse(
        # Named even when off, or the client looks for keys in the environment itself.
        public_key=public_key or "off",
        secret_key=secret_key or "off",
        base_url=base_url,
        tracing_enabled=bool(public_key and secret_key),
    )


class Traced:
    """One part of a trace: a turn, a tool, a scene step's work or a model call. Everything
    it does is safe: a failure is only logged."""

    def __init__(self, client: Langfuse | None = None, observation: Any = None) -> None:
        self._client = client
        self._observation = observation

    def ids(self) -> tuple[str, str]:
        """The part's trace ID and its own ID, for work started elsewhere to find it by.
        Blank when tracing is off or failed."""
        try:
            if self._client is not None:
                return (
                    self._client.get_current_trace_id() or "",
                    self._client.get_current_observation_id() or "",
                )
        except Exception:
            logger.exception("Couldn't read the trace's IDs")
        return "", ""

    def recording(self) -> bool:
        """Whether the part is being sent to Langfuse: tracing is on, and it started."""
        return bool(self.ids()[0])

    def update(self, **fields: Any) -> None:
        """Set the part's input, output, metadata or any other of Langfuse's fields."""
        try:
            if self._observation is not None:
                self._observation.update(**fields)
        except Exception:
            logger.exception("Couldn't update a part of a trace")

    def made(
        self,
        *,
        output: dict[str, Any] | None,
        files: Sequence[str] = (),
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record what a model call made: its output and the files in the file store it
        made, by their keys, what it was billed, and, when it failed, why."""
        if not self.recording():
            return
        usage = {"input": input_tokens, "output": output_tokens}
        self.update(
            output={"output": output, **_listed("made", _files(files))},
            usage_details={kind: tokens for kind, tokens in usage.items() if tokens is not None},
            cost_details=None if cost_usd is None else {"total": float(cost_usd)},
            metadata=metadata,
            **({"level": "ERROR", "status_message": error} if error else {}),
        )


@contextmanager
def _observed(
    name: str,
    as_type: str,
    *,
    session_id: str | None = None,
    trace_name: str | None = None,
    parent: tuple[str, str] | None = None,
    **fields: Any,
) -> Iterator[Traced]:
    """Trace the block as a part inside the one running now, or under `parent`, given as
    its trace ID and its own ID: with no ID of its own, at the top of that trace. With
    `session_id`, it and every part inside it belong to that Langfuse session, and a new
    trace is named `trace_name`, or after the part."""
    stack = ExitStack()
    try:
        client = _client()
        trace_context: Any = None
        if parent is not None and parent[0]:
            trace_context = {"trace_id": parent[0]}
            if parent[1]:
                trace_context["parent_span_id"] = parent[1]
        if session_id is not None:
            # Only a new trace is named: a part added to one keeps the name it has.
            opened: AbstractContextManager[Any] = propagate_attributes(
                session_id=session_id, trace_name=None if trace_context else trace_name or name
            )
            stack.enter_context(opened)
        observation = stack.enter_context(
            client.start_as_current_observation(
                name=name,
                as_type=as_type,
                trace_context=trace_context,
                **fields,  # type: ignore[call-overload]
            )
        )
        traced = Traced(client, observation)
    except Exception:
        logger.exception("Couldn't start tracing %s", name)
        _close(stack)
        stack, traced = ExitStack(), Traced()
    try:
        yield traced
    except BaseException as error:
        traced.update(level="ERROR", status_message=f"{type(error).__name__}: {error}")
        _close(stack)
        raise
    _close(stack)


def _close(stack: ExitStack) -> None:
    try:
        stack.close()
    except Exception:
        logger.exception("Couldn't finish a part of a trace")


def message_work(
    agent: str, *, session_id: str, message: str, trace_id: str, woken_by: Sequence[str]
) -> AbstractContextManager[Traced]:
    """Trace the block as an agent's work on the user's `message`, grouped in Langfuse with
    the rest of its session. The first time, it is a new trace named after the message.
    Given that trace's ID, it is one more part at the top of it, named for the scene steps
    whose finishing woke the agent, `woken_by`, or as started again when none did. Its
    output is set with `said`."""
    given: dict[str, Any] = {"message": message, **({"woken_by": woken_by} if woken_by else {})}
    if not trace_id:
        return _observed(
            agent, "span", session_id=session_id, trace_name=message or agent, input=given
        )
    why = f"woken by {', '.join(woken_by)}" if woken_by else "started again"
    return _observed(
        f"{agent}, {why}", "span", session_id=session_id, parent=(trace_id, ""), input=given
    )


def said(traced: Traced, texts: Sequence[str]) -> None:
    """Record what the agent said to the user in its work on a message."""
    traced.update(output={"said": list(texts)})


def agent_turn(agent: str) -> AbstractContextManager[Traced]:
    """Trace the block as one of an agent's turns: its model call and the tools it asked for."""
    return _observed(f"{agent} turn", "agent")


def tool(name: str, arguments: dict[str, Any]) -> AbstractContextManager[Traced]:
    """Trace the block as one tool running. Its result is set with `handed_back`.

    Both sides are named: a tool given a dict and handing back text is otherwise shown by
    Langfuse as a chat, its arguments as "User" and its result as "Assistant"."""
    return _observed(name, "tool", input={"arguments": arguments})


def handed_back(traced: Traced, result: str, **fields: Any) -> None:
    """Record what a tool handed back to the agent."""
    traced.update(output={"result": result}, **fields)


def scene_step(
    name: str, *, session_id: str, trace_id: str, parent_id: str
) -> AbstractContextManager[Traced]:
    """Trace the block as a scene step's work, inside the tool run whose trace ID and own ID
    are given: it runs later, in another worker, once that tool has handed back."""
    return _observed(name, "span", session_id=session_id, parent=(trace_id, parent_id))


@contextmanager
def model_call(
    purpose: str, *, model: str, handoff: dict[str, Any], shown: Sequence[str] = ()
) -> Iterator[Traced]:
    """Trace the block as one attempt at a model call, handed `handoff` and shown the files
    in the file store `shown`, by their keys."""
    with _observed(purpose, "generation", model=model) as traced:
        # Files are only read when they will be sent.
        if traced.recording():
            traced.update(input=_given(handoff, shown))
        yield traced


def answered_from_record(
    purpose: str,
    *,
    model: str,
    handoff: dict[str, Any],
    shown: Sequence[str] = (),
    output: dict[str, Any] | None,
) -> None:
    """Trace a model call answered from the record of one already paid for, which costs
    nothing."""
    with _observed(f"{purpose} (answered from its record)", "generation", model=model) as traced:
        if traced.recording():
            traced.update(
                input=_given(handoff, shown),
                output={"output": output},
                cost_details={"total": 0.0},
            )


def _given(handoff: dict[str, Any], shown: Sequence[str]) -> dict[str, Any]:
    """What a model call was handed, and the files it was shown when there were any."""
    return {"handoff": handoff, **_listed("shown", _files(shown))}


def _listed(name: str, files: list[LangfuseMedia]) -> dict[str, list[LangfuseMedia]]:
    """The files under `name`, or nothing when there are none, so no empty list is shown."""
    return {name: files} if files else {}


def _files(keys: Sequence[str]) -> list[LangfuseMedia]:
    """The files in the file store, for Langfuse to show. One that can't be read is left out."""
    files = []
    for key in keys:
        content_type = _CONTENT_TYPES.get(PurePath(key).suffix.lower())
        if content_type is None:
            continue
        try:
            content = file_store.read(key)
            files.append(LangfuseMedia(content_bytes=content, content_type=content_type))  # type: ignore[arg-type]
        except Exception:
            logger.exception("Couldn't read %s to trace it", key)
    return files


def flush() -> None:
    """Send everything traced so far. Called as the producer's work or a scene step ends, so
    nothing is lost with its worker."""
    try:
        _client().flush()
    except Exception:
        logger.exception("Couldn't send the traces")
