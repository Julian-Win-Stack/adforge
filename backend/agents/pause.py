"""The shop owner's pause button (spec #247 point 14). Pausing is a flag on the session: code
cancels every scene step still waiting for the worker (it cost nothing), lets the ones the
worker took finish and keeps them, and leaves the producer one system note naming each. While
the flag holds, every producer tool that spends answers paused (loop.settle, by
`Tool.runs_while_paused`), so starting nothing is the tools' rule; the shop owner's next
message clears it (`clear`)."""

import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message, Session
from jobs.models import AgentFix, SceneStep

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


def pause(session: Session, *, wake: bool = True) -> Paused:
    """Pause `session`: cancel its producer's scene steps the worker hasn't taken, keep those
    it has, and tell the producer of both in one system note, then wake it to read it unless
    not to `wake` it (another session is working: it reads the note when it next works). A
    session paused already is left as it is.

    Under the session's lock, which a scene tool takes to start a step too, so a step is
    either started before the pause and cancelled by it, or refused. Each step is cancelled
    by one statement that only matches it while no worker has taken it, as the worker takes
    a step by one that only matches it while it runs: whichever comes first wins, and a step
    taken is kept."""
    from .tasks import wake_producer

    with transaction.atomic():
        locked = Session.objects.select_for_update().get(pk=session.pk)
        if locked.paused_at is not None:
            return Paused([], [])
        locked.paused_at = timezone.now()
        locked.save(update_fields=["paused_at"])
        running = SceneStep.objects.filter(
            tool_call__session=session,
            tool_call__agent=PRODUCER.name,
            status=SceneStep.Status.RUNNING,
            # A fix plan's steps are code's, carried on by the plan runner, not the
            # producer's to call again.
            plan_scene__isnull=True,
        )
        queued = list(running.filter(taken_at__isnull=True).values_list("pk", flat=True))
        now = timezone.now()
        cancelled_ids = [
            pk
            for pk in queued
            if SceneStep.objects.filter(
                pk=pk, status=SceneStep.Status.RUNNING, taken_at__isnull=True
            ).update(
                status=SceneStep.Status.CANCELLED,
                reason="The shop owner paused before a worker took it.",
                finished_at=now,
            )
        ]
        in_order = ("started_at", "id")
        cancelled = list(
            SceneStep.objects.filter(pk__in=cancelled_ids)
            .select_related("scene", "tool_call")
            .order_by(*in_order)
        )
        kept = list(running.select_related("scene", "tool_call").order_by(*in_order))
        for step in cancelled:
            step_signals.say_cancelled(step)
        messages.add(
            session, role=Message.Role.NOTICE, level=Message.Level.INFO, text=PAUSED_NOTICE
        )
        SystemNote.objects.create(
            session=session, kind=SystemNote.Kind.PAUSED, text=note(cancelled, kept)
        )
        if wake:
            wake_producer(str(session.pk))
    return Paused(cancelled, kept)


def clear(session: Session) -> None:
    """End the pause, as the shop owner's next message does. Call it under the session's
    lock, with the message."""
    Session.objects.filter(pk=session.pk).update(paused_at=None)


def note(cancelled: list[SceneStep], running: list[SceneStep]) -> str:
    """The producer's note of a pause: each step cancelled, its scene and what it was for (the
    call that started it, with its instruction and its why), and each step kept running."""
    parts = [
        "The shop owner pressed pause. Every tool that costs money answers paused until they write."
    ]
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
    # The fixing agent at work on a clip parks between tries (spec #247 point 14a).
    fixing = (
        ", whose fixing agent finishes the try it is filming, then waits for you to carry it "
        f"on with fix_scene for scene {step.scene.number}"
        if step.fixes_a_note
        or AgentFix.objects.filter(step=step, finished_at__isnull=True).exists()
        else ""
    )
    return f"{name}, from {called}{because}{fixing}"
