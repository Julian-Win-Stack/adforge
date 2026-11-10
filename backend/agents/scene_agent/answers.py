"""Deliver the library answer in this scene chat, reopening an ended run window."""

from typing import Any

from django.db import transaction
from django.utils import timezone

from jobs.models import AgentFix, OwnerQuestion, SceneAgentRun

from . import lifecycle
from .change_scene import SNAPSHOT


def _keeping_the_owner_mark(
    window: SceneAgentRun, quotes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The answer's quotes, marked as the owner's change when the run they answer in was:
    an answer to the agent's question never takes away a change the owner asked for."""
    if not lifecycle.owner_asked(window):
        return quotes
    return [{**quote, "owner_asked": True} for quote in quotes]


def receive(
    question: OwnerQuestion, text: str, instruction: str, *, target: AgentFix | None = None
) -> None:
    from . import current_run

    with transaction.atomic():
        fixing = AgentFix.objects.select_for_update().get(
            pk=target.pk if target is not None else question.agent_fix_id
        )
        if question.answered_at is not None:
            return
        if fixing.finished_at is not None:
            previous = current_run(fixing)
            if previous.finished_at is None:
                previous.finished_at = timezone.now()
                previous.save(update_fields=["finished_at"])
            next_run = SceneAgentRun.objects.create(
                agent_fix=fixing,
                number=previous.number + 1,
                instruction=instruction,
                quotes=_keeping_the_owner_mark(previous, (question.answer or {}).get("quotes", [])),
                scene_before={field: getattr(fixing.step.scene, field) for field in SNAPSHOT},
            )
            fixing.scene_before = next_run.scene_before
            fixing.goal = instruction
            fixing.finished_at = None
            fixing.gave_up = ""
            fixing.passed = None
            fixing.paused_at = None
            fixing.ended_by_cap = False
        elif target is None and (question.answer or {}).get("quotes"):
            window = current_run(fixing)
            window.quotes = _keeping_the_owner_mark(window, (question.answer or {})["quotes"])
            window.save(update_fields=["quotes"])
        fixing.said = [
            *fixing.said,
            {
                "at": lifecycle._calls(fixing).filter(finished_at__isnull=False).count(),
                "text": text,
                "by": "user",
                "no_call": False,
            },
        ]
        fixing.save()
        OwnerQuestion.objects.filter(pk=question.pk).update(answered_at=timezone.now())
