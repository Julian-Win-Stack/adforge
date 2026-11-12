"""An in-process observer of real paid attempts; absent in the normal app."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

from .types import Handoff

if TYPE_CHECKING:
    from .models import ModelCall


class CallObserver(Protocol):
    def attempt(
        self, purpose: str, model: str, provider: str, handoff: Handoff
    ) -> AbstractContextManager[None]: ...

    def recorded(self, call: ModelCall) -> None: ...


_observer: ContextVar[CallObserver | None] = ContextVar("call_observer", default=None)


@contextmanager
def use_call_observer(observer: CallObserver) -> Iterator[None]:
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


@contextmanager
def attempt(purpose: str, model: str, provider: str, handoff: Handoff) -> Iterator[None]:
    observer = _observer.get()
    if observer is None:
        yield
    else:
        with observer.attempt(purpose, model, provider, handoff):
            yield


def recorded(call: ModelCall) -> None:
    observer = _observer.get()
    if observer is not None:
        observer.recorded(call)
