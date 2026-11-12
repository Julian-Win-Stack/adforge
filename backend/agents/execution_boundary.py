"""Optional in-process execution boundaries; the deployed app installs none."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from jobs.models import Job

    from .loop import Agent
    from .models import ToolCall


class ExecutionBoundary(Protocol):
    def before_tool(self, agent: Agent, call: ToolCall) -> str | None: ...

    def after_tool(self, agent: Agent, call: ToolCall) -> None: ...

    def defer_owner_parts(self, job: Job) -> bool: ...


_boundary: ContextVar[ExecutionBoundary | None] = ContextVar("execution_boundary", default=None)


@contextmanager
def use_execution_boundary(boundary: ExecutionBoundary) -> Iterator[None]:
    token = _boundary.set(boundary)
    try:
        yield
    finally:
        _boundary.reset(token)


def before_tool(agent: Agent, call: ToolCall) -> str | None:
    boundary = _boundary.get()
    return boundary.before_tool(agent, call) if boundary is not None else None


def after_tool(agent: Agent, call: ToolCall) -> None:
    boundary = _boundary.get()
    if boundary is not None:
        boundary.after_tool(agent, call)


def defer_owner_parts(job: Job) -> bool:
    boundary = _boundary.get()
    return boundary.defer_owner_parts(job) if boundary is not None else False
