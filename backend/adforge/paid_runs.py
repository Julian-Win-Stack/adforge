"""What a paid run checks before it starts and watches while it runs: every key its stage
needs, nothing else already making ads, and model calls refused for a setup reason (no key,
a wrong key, no money), which fail the same way on every later case."""

import re
from datetime import datetime, timedelta
from typing import Literal

from django.conf import settings

type KeyStage = Literal["plans", "pictures", "full", "judge"]

# (setting, what to set), in the order a run first needs them.
_ALWAYS = [
    ("OPENAI_API_KEY", "AZUREE_OPENAI_KEY or OPENAI_API_KEY (text and vision)"),
    ("GEMINI_API_KEY", "GEMINI_API_KEY"),
]
_PICTURES = [("OPENAI_PICTURES_KEY", "OPENAI_API_KEY (pictures are drawn on OpenAI's own API)")]
_CLIPS = [
    ("CREATIFY_API_ID", "CREATIFY_API_ID"),
    ("CREATIFY_API_KEY", "CREATIFY_API_KEY"),
    ("FAL_KEY", "FAL_KEY"),
    ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
]
_KEYS: dict[KeyStage, list[tuple[str, str]]] = {
    "plans": _ALWAYS,
    "pictures": _ALWAYS + _PICTURES,
    "full": _ALWAYS + _PICTURES + _CLIPS,
    "judge": [],
}

# A failed ModelCall's error ("<exception name>: <text>") when the service refused us for
# who we are rather than what we asked: asking again on any case gets the same answer.
_SETUP_REFUSAL = re.compile(
    r"^(NotSetUp|RefusedForMoney|AuthenticationError|PermissionDeniedError):"
    r"|^HTTPStatusError: Client error '40[123] "
)


def missing_keys(stage: KeyStage) -> list[str]:
    """One line per key `stage` needs that is unset. A faked gateway needs none."""
    from gateway import gateway

    if gateway.faked():
        return []
    missing = [f"{name} is not set" for key, name in _KEYS[stage] if not getattr(settings, key)]
    if stage == "judge" and not settings.OPENAI_TEXT_ON_AZURE:
        missing.append(
            "AZUREE_OPENAI_KEY and AZUREE_OPENAI_BASE_URL are not set (the judge runs on Azure)"
        )
    return missing


def refused_for_setup(error: str | None, *, model: str = "", provider: str = "") -> bool:
    """Whether a failed call's `error` fails the same way on every case. OpenAI refusing a
    picture for money isn't, when Azure pictures are set up: the gateway draws it there."""
    from gateway import catalog
    from gateway.gateway import AZURE_PICTURES

    if not error or not _SETUP_REFUSAL.search(error):
        return False
    moves_to_azure = (
        error.startswith("RefusedForMoney:")
        and model in catalog.PRICE_PER_MILLION_PICTURE_TOKENS
        and provider != AZURE_PICTURES
        and bool(settings.AZURE_PICTURES_KEY)
    )
    return not moves_to_azure


def ad_run_problems(*, idle: bool) -> list[str]:
    """Why a batch of ads can't start in this app: unset keys, and (`idle`) ads already being
    made, by heartbeats in the last five minutes as deploy.sh counts them."""
    from django.utils import timezone

    from chat.models import Session
    from jobs.models import SceneStep

    problems = missing_keys("full")
    if idle:
        alive = timezone.now() - timedelta(minutes=5)
        producers = Session.objects.filter(
            producer_running=True, producer_seen_at__gt=alive
        ).count()
        steps = SceneStep.objects.filter(status=SceneStep.Status.RUNNING, seen_at__gt=alive).count()
        if producers or steps:
            problems.append(f"{producers} producers and {steps} steps are already making ads")
    return problems


def setup_refusals_since(since: datetime | str) -> list[tuple[str, str]]:
    """(session id, error) of each call refused for a setup reason since `since`."""
    from gateway.models import ModelCall

    calls = ModelCall.objects.filter(
        created_at__gte=since, outcome=ModelCall.Outcome.FAILED, session__isnull=False
    ).values_list("session_id", "error", "model", "provider")
    return [
        (str(session), error)
        for session, error, model, provider in calls
        if refused_for_setup(error, model=model, provider=provider)
    ]
