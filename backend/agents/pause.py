"""The shop owner's pause button (spec #247 point 14). Pausing is a flag on the session: code
cancels every scene step still waiting for the worker (it cost nothing), lets the ones the
worker took finish and keeps them, and leaves the producer one system note naming each. While
the flag holds, every scene tool answers paused (loop.refuse_while_paused), so starting
nothing is the tools' rule; the shop owner's next message clears it (`clear`)."""

import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message, Session
from jobs.models import SceneStep

from . import step_signals
from .models import SystemNote
from .producer import PRODUCER, STEP_NAMES

# What the chat shows as the pause takes hold, before the producer's own line.
PAUSED_NOTICE = "Paused. Nothing new is started until you write."


@dataclass(frozen=True)
class Paused:
    """What one press of pause did: the steps it cancelled and those it left running, oldest
    first. Both empty when the session was paused already, or nothing was waiting or running."""

    cancelled: list[SceneStep]
    running: list[SceneStep]


def pause(session: Session) -> Paused:
    """Pause `session`: cancel its producer's scene steps the worker hasn't taken, keep those
    it has, and tell the producer of both in one system note, then wake it to read it. A
    session paused already is left as it is. Done under the session's lock, as a message sent
    is, and with each step's row locked first, so the worker taking a step at the same moment
    either takes it first, and it is kept, or finds it cancelled, and leaves it."""
    from .tasks import wake_producer

    with transaction.atomic():
        # The steps' rows before the session's, as a step finishing or failed locks them.
        steps = list(
            SceneStep.objects.select_for_update(of=("self",))
            .filter(
                tool_call__session=session,
                tool_call__agent=PRODUCER.name,
                status=SceneStep.Status.RUNNING,
                # A fix plan's steps are code's, carried on by the plan runner, not the
                # producer's to call again.
                plan_scene__isnull=True,
            )
            .select_related("scene", "tool_call")
            .order_by("started_at", "id")
        )
        locked = Session.objects.select_for_update().get(pk=session.pk)
        if locked.paused_at is not None:
            return Paused([], [])
        locked.paused_at = timezone.now()
        locked.save(update_fields=["paused_at"])
        cancelled = [step for step in steps if step.taken_at is None]
        running = [step for step in steps if step.taken_at is not None]
        now = timezone.now()
        for step in cancelled:
            step.status = SceneStep.Status.CANCELLED
            step.reason = "The shop owner paused before a worker took it."
            step.finished_at = now
            step.save(update_fields=["status", "reason", "finished_at"])
            step_signals.say_cancelled(step)
        messages.add(
            session, role=Message.Role.NOTICE, level=Message.Level.INFO, text=PAUSED_NOTICE
        )
        SystemNote.objects.create(
            session=session, kind=SystemNote.Kind.PAUSED, text=note(cancelled, running)
        )
        wake_producer(str(session.pk))
    return Paused(cancelled, running)


def clear(session: Session) -> None:
    """End the pause, as the shop owner's next message does. Call it under the session's
    lock, with the message."""
    Session.objects.filter(pk=session.pk).update(paused_at=None)


def note(cancelled: list[SceneStep], running: list[SceneStep]) -> str:
    """The producer's note of a pause: each step cancelled, its scene and what it was for (the
    call that started it, with its instruction and its why), and each step kept running."""
    parts = ["The shop owner pressed pause. Every scene tool answers paused until they write."]
    if cancelled:
        parts.append(
            "Cancelled before a worker took them, so nothing was made or paid for: "
            + "; ".join(_what_it_was_for(step) for step in cancelled)
            + "."
        )
    else:
        parts.append("No step was waiting, so none was cancelled.")
    if running:
        parts.append(
            "Still running, and kept when they finish (you'll be told): "
            + "; ".join(_what_it_was_for(step) for step in running)
            + "."
        )
    else:
        parts.append("Still running: none.")
    parts.append(
        "If the shop owner hasn't written since, tell them in one line that you paused and "
        "what is still finishing, then wait. After their message, call again the tools still "
        "needed."
    )
    return " ".join(parts)


def _what_it_was_for(step: SceneStep) -> str:
    """Such as `scene 2's clip, from make_clip {"scene": 2, "note": "slower pour"}, because
    "..."`: the step and the call that started it, as the producer made it."""
    name = f"scene {step.scene.number}'s {STEP_NAMES[SceneStep.Kind(step.kind)]}"
    call = step.tool_call
    called = f"{call.tool} {json.dumps(call.arguments, ensure_ascii=False)}"
    because = f', because "{call.why}"' if call.why else ""
    return f"{name}, from {called}{because}"
