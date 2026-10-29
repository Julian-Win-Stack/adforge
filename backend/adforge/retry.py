"""Automatic retries for calls to outside services: the product's shop, model providers."""

import time
from collections.abc import Callable

from django.conf import settings


class OutsideServiceDown(Exception):
    """An outside service is down or errored in a way that may pass if we try again. When the
    service says how long to wait first, `retry_after` holds its seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def with_retries[T](attempt: Callable[[], T]) -> T:
    """Run `attempt`, trying again after each delay in RETRY_DELAYS_SECONDS while it
    raises OutsideServiceDown, or after as long as the service said to wait, when longer.
    Any other error is not worth retrying and goes straight up."""
    delays: list[float] = settings.RETRY_DELAYS_SECONDS
    for delay in delays:
        try:
            return attempt()
        except OutsideServiceDown as down:
            _sleep(max(delay, down.retry_after or 0))
    return attempt()
