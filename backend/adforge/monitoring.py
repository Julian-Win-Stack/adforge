"""The one place that talks to Sentry, where every failure is sent for the developer to look
at: each fallback a notice records (jobs/notices.py) as a warning, grouped by its kind, not
its words, and every crash (a logged exception) as an error.

Sentry is off unless SENTRY_DSN is set, so tests and a fresh checkout send nothing. It sends
no personal data and does no performance tracing: Langfuse holds the detail, and each event
links to its trace there. A failure to send is logged, never raised into the work."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

logger = logging.getLogger(__name__)

# What the work in hand is about, as tags for every event sent while it runs: the session,
# the ad's name, the scene and the step. Set by the producer's work (agents/tasks.py
# run_producer) and each scene step's (run_scene_step).
_working_on: ContextVar[dict[str, str]] = ContextVar("working_on", default={})  # noqa: B039


def start(dsn: str, environment: str) -> None:
    """Start sending to Sentry, when `dsn` is set; nothing otherwise. Called once, as Django's
    settings load, so the web server and every Celery worker do it."""
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # No personal data: send_default_pii is left off, its default (passing it at all is
        # deprecated for data_collection, which, unset, follows it).
        traces_sample_rate=0,
        profiles_sample_rate=0,
    )


@contextmanager
def working_on(**tags: str | int | None) -> Iterator[None]:
    """Tag every event sent inside this block with `tags` (session, ad, scene, step), on top
    of those of any block it is in. A tag given as None or blank is left as it was."""
    token = _working_on.set({**_working_on.get(), **_given(tags)})
    try:
        yield
    finally:
        _working_on.reset(token)


def failed(kind: str, text: str, **tags: str | int | None) -> None:
    """Send a failure or fallback of `kind` (a short name, such as "recolour_failed") to
    Sentry as a warning: grouped with every other of its kind whatever `text` says, tagged
    with what the work is on and `tags`, and linked to the Langfuse trace it happened in."""
    # Imported here: settings start Sentry before Django is ready for tracing's imports.
    from adforge import tracing

    try:
        trace_id, trace_url = tracing.current_trace()
        with sentry_sdk.new_scope() as scope:
            scope.fingerprint = ["notice", kind]
            scope.set_tag("kind", kind)
            for name, value in {**_working_on.get(), **_given(tags)}.items():
                scope.set_tag(name, value)
            if trace_id:
                scope.set_tag("langfuse_trace", trace_id)
                scope.set_context("langfuse", {"trace_id": trace_id, "url": trace_url})
            scope.set_extra("text", text)
            sentry_sdk.capture_message(f"{kind}: {text}", level="warning")
    except Exception:
        logger.exception("Couldn't send the %s failure to Sentry", kind)


def _given(tags: dict[str, Any]) -> dict[str, str]:
    return {name: str(value) for name, value in tags.items() if value not in (None, "")}
