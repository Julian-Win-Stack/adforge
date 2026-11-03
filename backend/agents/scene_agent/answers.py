"""Deliver the library answer in this scene chat, reopening an ended run window."""

from django.db import transaction
from django.utils import timezone

from jobs.models import AgentFix, OwnerQuestion, SceneAgentRun

from . import lifecycle
from .change_scene import SNAPSHOT


def receive(question: OwnerQuestion, text: str, instruction: str) -> None:
    from . import current_run

    with transaction.atomic():
        fixing = AgentFix.objects.select_for_update().get(pk=question.agent_fix_id)
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
                quotes=(question.answer or {}).get("quotes", []),
                scene_before={field: getattr(fixing.step.scene, field) for field in SNAPSHOT},
            )
            fixing.scene_before = next_run.scene_before
            fixing.goal = instruction
            fixing.finished_at = None
            fixing.gave_up = ""
            fixing.passed = None
            fixing.paused_at = None
            fixing.ended_by_cap = False
        elif (question.answer or {}).get("quotes"):
            window = current_run(fixing)
            window.quotes = (question.answer or {})["quotes"]
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
