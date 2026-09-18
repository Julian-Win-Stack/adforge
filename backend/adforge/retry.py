"""Automatic retries for calls to outside services: the product's shop, model providers."""

import time
from collections.abc import Callable

from django.conf import settings


class OutsideServiceDown(Exception):
    """An outside service is down or errored in a way that may pass if we try again."""


def with_retries[T](attempt: Callable[[], T]) -> T:
    """Run `attempt`, trying again after each delay in RETRY_DELAYS_SECONDS while it
    raises OutsideServiceDown. Any other error is not worth retrying and goes straight up."""
    delays: list[float] = settings.RETRY_DELAYS_SECONDS
    for delay in delays:
        try:
            return attempt()
        except OutsideServiceDown:
            time.sleep(delay)
    return attempt()
