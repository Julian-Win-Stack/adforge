"""The one place that talks to Langfuse, where each message's work is sent to be looked at
as a tree: the producer's turns, the tools each asked for, and the model calls each made.

Langfuse is for looking at what the agents did, not for counting it: the model-call records
stay the only source of cost and time numbers. Tracing is off unless both Langfuse keys are
set, and a tracing failure is logged, never raised into the work it was tracing."""

import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import PurePath
from typing import Any

from django.conf import settings
from langfuse import Langfuse, LangfuseMedia, propagate_attributes
from opentelemetry import trace as otel_trace

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


@dataclass(frozen=True)
class Ad:
    """The ad's known names, independent of the records the caller gets them from."""

    name: str = ""
    product_name: str = ""
    product_site: str = ""

    def trace_name(self, session_id: str) -> str:
        parts = dict.fromkeys(
            part.strip()
            for part in (self.name, self.product_name.strip() or self.product_site)
            if part.strip()
        )
        return (" · ".join(parts) or f"ad {session_id}")[:200]

    def tags(self) -> list[str]:
        name = self.name.strip()
        return list(
            dict.fromkeys(
                tag
                for tag in (
                    name if not re.search(r"https?://|www\.", name, re.IGNORECASE) else "",
                    self.product_site.strip(),
                )
                if tag
            )
        )


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
        settings.LANGFUSE_PUBLIC_KEY,
        settings.LANGFUSE_SECRET_KEY,
        settings.LANGFUSE_BASE_URL,
        settings.LANGFUSE_ENVIRONMENT,
    )


# Made the first time it is needed, in the process that uses it: Celery forks its workers,
# and the thread that sends traces doesn't survive a fork.
@cache
def _from_settings(public_key: str, secret_key: str, base_url: str, environment: str) -> Langfuse:
    return new_client(
        # Named even when off, or the client looks for keys in the environment itself.
        public_key=public_key or "off",
        secret_key=secret_key or "off",
        base_url=base_url,
        environment=environment,
        tracing_enabled=bool(public_key and secret_key),
    )


def new_client(**options: Any) -> Langfuse:
    """A Langfuse client with `options`, sending everything in the settings' environment
    unless they name another. Tests make theirs with it, as the app does."""
    return Langfuse(**{"environment": settings.LANGFUSE_ENVIRONMENT, **options})


def say_if_off() -> None:
    """Say, as a warning line, when tracing is off because a Langfuse key is missing: a box
    that should be traced and isn't is never left to find out (no silent fallbacks)."""
    missing = [
        name
        for name, key in [
            ("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY),
            ("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY),
        ]
        if not key
    ]
    if missing:
        logger.warning(
            "Langfuse tracing is off: %s %s not set, so nothing is traced",
            " and ".join(missing),
            "are" if len(missing) > 1 else "is",
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

    def score(self, name: str, value: str, *, comment: str | None = None) -> None:
        """Score the part with a verdict, such as a judge's "pass" or "fail", under `name`."""
        try:
            if self._observation is not None:
                score(
                    name,
                    value,
                    trace_id=self._observation.trace_id,
                    observation_id=self._observation.id,
                    comment=comment,
                )
        except Exception:
            logger.exception("Couldn't score a part of a trace")

    def tag_trace(self, tags: Sequence[str]) -> None:
        """Add tags to the whole trace through this part, without changing its payload."""
        try:
            if self._observation is not None:
                self._observation._otel_span.set_attribute("langfuse.trace.tags", list(tags))
        except Exception:
            logger.exception("Couldn't tag a trace")

    def name_ad(self, ad: Ad, *, session_id: str) -> None:
        """Update the active part once the work has learned the ad's real names."""
        try:
            with propagate_attributes(trace_name=ad.trace_name(session_id), tags=ad.tags()):
                pass
        except Exception:
            logger.exception("Couldn't name an ad's trace")

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
    ad: Ad | None = None,
    parent: tuple[str, str] | None = None,
    new_trace: bool = False,
    **fields: Any,
) -> Iterator[Traced]:
    """Trace the block as a part inside the one running now, or under `parent`, given as
    its trace ID and its own ID: with no ID of its own, at the top of that trace. With
    `session_id`, it and every part inside it belong to that Langfuse session, and a new
    trace is named after `ad`. With `new_trace`, it starts a trace of
    its own, whatever part is running now."""
    stack = ExitStack()
    try:
        client = _client()
        trace_context: Any = None
        if parent is not None and parent[0]:
            trace_context = {"trace_id": parent[0]}
            if parent[1]:
                trace_context["parent_span_id"] = parent[1]
        elif new_trace:
            trace_context = {"trace_id": client.create_trace_id()}
        if session_id is not None:
            known = ad or Ad()
            opened: AbstractContextManager[Any] = propagate_attributes(
                session_id=session_id, trace_name=known.trace_name(session_id), tags=known.tags()
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
    agent: str, *, session_id: str, ad: Ad, message: str, trace_id: str, woken_by: Sequence[str]
) -> AbstractContextManager[Traced]:
    """Trace an agent's work on a message, naming even a resumed trace after its ad."""
    given: dict[str, Any] = {"message": message, **({"woken_by": woken_by} if woken_by else {})}
    if not trace_id:
        return _observed(
            agent,
            "agent",
            session_id=session_id,
            ad=ad,
            new_trace=True,
            input=given,
        )
    why: dict[str, Any] = {"woken_by": list(woken_by)} if woken_by else {"started_again": True}
    return _observed(
        agent,
        "agent",
        session_id=session_id,
        ad=ad,
        parent=(trace_id, ""),
        input=given,
        metadata=why,
    )


def said(traced: Traced, texts: Sequence[str]) -> None:
    """Record what the agent said to the user in its work on a message."""
    traced.update(output={"said": list(texts)})


def agent_turn(agent: str, *, of: str = "") -> AbstractContextManager[Traced]:
    """Trace the block as one of an agent's turns: its model call and the tools it asked for."""
    return _observed(f"{agent.replace('_', ' ')} turn" + (f" · {of}" if of else ""), "agent")


def tool(name: str, arguments: dict[str, Any], *, why: str = "") -> AbstractContextManager[Traced]:
    """Trace the block as one tool running, with the agent's `why` for the call when it gave
    one. Its result is set with `handed_back`.

    Both sides are named: a tool given a dict and handing back text is otherwise shown by
    Langfuse as a chat, its arguments as "User" and its result as "Assistant"."""
    return _observed(name, "tool", input={"arguments": arguments, **({"why": why} if why else {})})


def handed_back(traced: Traced, result: str, **fields: Any) -> None:
    """Record what a tool handed back to the agent."""
    traced.update(output={"result": result}, **fields)


def scene_step(
    *, kind: str, scene: int, session_id: str, ad: Ad, trace_id: str, parent_id: str
) -> AbstractContextManager[Traced]:
    """Trace a scene step inside the tool run that started it, with the ad's known names."""
    return _observed(
        f"scene {scene} · {kind.replace('_', ' ')}",
        "chain",
        session_id=session_id,
        ad=ad,
        parent=(trace_id, parent_id),
        metadata={"scene": scene, "step": kind},
    )


def step_asked(traced: Traced, *, kind: str, scene: int, **asked: str) -> None:
    """Record what a scene step was asked: its kind, its scene, and each of `asked` that
    isn't blank, such as its line and the prompt its model was given."""
    traced.update(
        input={"kind": kind, "scene": scene, **{name: text for name, text in asked.items() if text}}
    )


@contextmanager
def model_call(
    purpose: str,
    *,
    model: str,
    told: dict[str, Any],
    handoff: dict[str, Any],
    shown: Sequence[str] = (),
) -> Iterator[Traced]:
    """Trace the block as one attempt at a model call, `told` its system prompt (its
    instructions and any tools offered), handed `handoff` and shown the files in the file
    store `shown`, by their keys."""
    with _observed(purpose, "generation", model=model) as traced:
        # Files are only read when they will be sent.
        if traced.recording():
            traced.update(input=_given(told, handoff, shown))
        yield traced


def answered_from_record(
    purpose: str,
    *,
    model: str,
    told: dict[str, Any],
    handoff: dict[str, Any],
    shown: Sequence[str] = (),
    output: dict[str, Any] | None,
) -> None:
    """Trace a model call answered from the record of one already paid for, which costs
    nothing."""
    with _observed(f"{purpose} (answered from its record)", "generation", model=model) as traced:
        if traced.recording():
            traced.update(
                input=_given(told, handoff, shown),
                output={"output": output},
                cost_details={"total": 0.0},
            )


def _given(told: dict[str, Any], handoff: dict[str, Any], shown: Sequence[str]) -> dict[str, Any]:
    """What a model call was told and handed, and the files it was shown when there were
    any."""
    return {**told, "handoff": handoff, **_listed("shown", _files(shown))}


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


def score(
    name: str,
    value: str,
    *,
    trace_id: str,
    observation_id: str = "",
    comment: str | None = None,
) -> None:
    """Score a part of a trace, given by its trace ID and its own ID, or the whole trace,
    with a verdict under `name`: a judge's, or a person's grade given later."""
    try:
        _client().create_score(
            name=name,
            value=value,
            trace_id=trace_id,
            observation_id=observation_id or None,
            data_type="CATEGORICAL",
            comment=comment,
        )
    except Exception:
        logger.exception("Couldn't send the score %s", name)


def flush() -> None:
    """Send everything traced so far. Called as the producer's work or a scene step ends, so
    nothing is lost with its worker."""
    try:
        _client().flush()
    except Exception:
        logger.exception("Couldn't send the traces")


def current_trace() -> tuple[str, str]:
    """The trace the work is in now, as its ID and a link to it in Langfuse, for a failure
    sent elsewhere to point back to its detail. Blank when tracing is off, nothing is being
    traced, or the link can't be had (Langfuse is asked once for its project to make it)."""
    # Langfuse logs an error when asked outside any traced work: look first.
    if not otel_trace.get_current_span().get_span_context().is_valid:
        return "", ""
    try:
        client = _client()
        trace_id = client.get_current_trace_id() or ""
    except Exception:
        logger.exception("Couldn't read the current trace")
        return "", ""
    if not trace_id:
        return "", ""
    try:
        return trace_id, client.get_trace_url(trace_id=trace_id) or ""
    except Exception:
        logger.exception("Couldn't make the link to trace %s", trace_id)
        return trace_id, ""
