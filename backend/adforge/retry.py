"""Automatic retries for calls to outside services: the product's shop, model providers."""

import random
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

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
    """Run `attempt`, trying again while it raises OutsideServiceDown, as a polite client
    does: after each delay in RETRY_DELAYS_SECONDS in turn, each with up to
    RETRY_JITTER_SHARE of it added at random, so calls turned away together don't all come
    back at once; or after as long as the service said to wait, when that is longer, though
    never longer than LONGEST_RETRY_WAIT_SECONDS. Any other error is not worth retrying and goes
    straight up."""
    delays: list[float] = settings.RETRY_DELAYS_SECONDS
    for delay in delays:
        try:
            return attempt()
        except OutsideServiceDown as down:
            if down.retry_after is not None:
                _sleep(min(max(delay, down.retry_after), settings.LONGEST_RETRY_WAIT_SECONDS))
            else:
                _sleep(delay * (1 + random.uniform(0, settings.RETRY_JITTER_SHARE)))
    return attempt()


def retry_after(headers: Mapping[str, str]) -> float | None:
    """How many seconds a service's answer says to wait before asking again, by its
    Retry-After header, in seconds or as a date; None when it doesn't say."""
    said = headers.get("retry-after")
    if not said:
        return None
    try:
        return max(float(said), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(said)
    except TypeError, ValueError:
        return None
    # A date with no zone ("-0000") is in GMT, as every HTTP date is.
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
