"""What would be sent to Sentry, caught by a transport of its own instead of being sent. The
`sentry_events` fixture (in conftest) gives it to a test."""

from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport


class _Caught(Transport):
    """Keeps every event it is given, sending none."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.type == "event" and item.payload.json is not None:
                self.events.append(item.payload.json)


@pytest.fixture
def sentry_events() -> Iterator[list[dict[str, Any]]]:
    """Every event the app sends to Sentry while the test runs, as Sentry would get it.
    Sentry is started as the app starts it, with a made-up DSN, and stopped after."""
    caught = _Caught()
    sentry_sdk.init(
        dsn="https://public@sentry.invalid/1",
        transport=caught,
        traces_sample_rate=0,
        default_integrations=False,
    )
    yield caught.events
    sentry_sdk.get_client().close()
    # Back to no client at all, as with no DSN.
    sentry_sdk.get_global_scope().set_client(None)
