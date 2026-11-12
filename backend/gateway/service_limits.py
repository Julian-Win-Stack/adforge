"""A shared line for outside calls, across worker processes and ads.

Postgres holds the pace and arbitrates admission. Concurrency slots are session advisory
locks: closing a worker's connection, including on process death, returns its slots.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Literal

from django.conf import settings
from django.db import connection

from adforge import monitoring
from chat.models import Message
from jobs import notices
from jobs.models import Job

from .models import ServicePace

type Service = Literal["openai_pictures", "firecrawl", "elevenlabs"]

# Two-key advisory locks occupy a namespace separate from existing single-key locks.
_KEYS: dict[Service, int] = {"openai_pictures": 61901, "firecrawl": 61902, "elevenlabs": 61903}
_job: ContextVar[Job | None] = ContextVar("outside_call_job", default=None)


@contextmanager
def for_job(job: Job) -> Iterator[None]:
    """Carry a page read's job into the threads making its outside calls."""
    token = _job.set(job)
    try:
        yield
    finally:
        _job.reset(token)


@contextmanager
def slot(service: Service, *, job: Job | None = None) -> Iterator[None]:
    """Wait for this account's pace and a free slot; release the slot after the call.

    Each retry must enter anew. Waiting raises no service error and spends no retry.
    """
    rate: float | None = None
    concurrency: int | None = None
    if service == "openai_pictures":
        rate = settings.OPENAI_PICTURES_PER_MINUTE
    elif service == "firecrawl":
        rate = settings.FIRECRAWL_REQUESTS_PER_MINUTE
        concurrency = settings.FIRECRAWL_CONCURRENCY
    else:
        concurrency = settings.ELEVENLABS_TRANSCRIPTION_CONCURRENCY
    if (rate is not None and rate <= 0) or (concurrency is not None and concurrency <= 0):
        raise ValueError(f"{service} limits must be positive")

    started = time.monotonic()
    notified = False
    while True:
        if not notified and time.monotonic() - started >= settings.SERVICE_LIMIT_NOTICE_SECONDS:
            _say_waiting(service, job or _job.get(), time.monotonic() - started)
            notified = True
        held = _admit(service, rate, concurrency)
        if held is not None:
            break
        time.sleep(0.05)
    try:
        yield
    finally:
        if held:
            _unlock(_KEYS[service], held)


def _admit(service: Service, rate: float | None, concurrency: int | None) -> int | None:
    """Atomically check the pace and take a concurrency slot; None means wait."""
    key = _KEYS[service]
    if not _lock(key, 0):
        return None
    held = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            now = cursor.fetchone()[0]
        pace = None
        if rate is not None:
            pace, _ = ServicePace.objects.get_or_create(service=service)
            if pace.next_start is not None and pace.next_start > now:
                return None
        if concurrency is not None:
            held = next((n for n in range(1, concurrency + 1) if _lock(key, n)), 0)
            if not held:
                return None
        try:
            if pace is not None and rate is not None:
                pace.next_start = now + timedelta(seconds=60 / rate)
                pace.save(update_fields=["next_start"])
        except BaseException:
            if held:
                _unlock(key, held)
            raise
        return held
    finally:
        _unlock(key, 0)


def _lock(key: int, number: int) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [key, number])
        return bool(cursor.fetchone()[0])


def _unlock(key: int, number: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [key, number])


def _say_waiting(service: Service, job: Job | None, seconds: float) -> None:
    text = f"{service} has waited {seconds:.0f}s for room under its account limit; still waiting."
    if job is not None:
        notices.post_notice(job, notices.SERVICE_LIMIT_WAIT, text, Message.Level.PROBLEM)
    else:
        monitoring.failed(notices.SERVICE_LIMIT_WAIT.name, text)
