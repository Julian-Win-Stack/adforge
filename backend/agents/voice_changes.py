"""A voice-change quote and its consent, kept in the normal tool checkpoints.

A quote describes one proposal and the current ad. It authorizes no work. Only a later
whole owner reply confirming that quote allows the producer to replace the baked-in voice.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

from chat import messages
from chat.models import Message
from gateway.catalog import PRICE_PER_SECOND_OF_VIDEO
from jobs.models import Job
from jobs.pace import h3_clip_seconds

from .loop import Refused
from .models import ToolCall
from .money import dollars

_QUOTE = "voice-change-quote:v1 "
_APPLIED = "voice-change-applied:v1 "


def _fingerprint(job: Job, proposal: dict[str, Any]) -> str:
    state = {
        "proposal": proposal,
        "person": [job.person_gender, job.person_looks, job.person_voice],
        "scenes": list(job.scenes.order_by("pk").values("pk", "line", "kind")),
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def _quote(job: Job, call: ToolCall, fingerprint: str, *, reason: str = "") -> str:
    try:
        seconds = sum(h3_clip_seconds(len(scene.line.split())) for scene in job.scenes.all())
    except ValueError as error:
        raise Refused(
            f"{error} Run the planning checks for shorter lines before quoting a voice change."
        ) from error
    estimate = dollars(PRICE_PER_SECOND_OF_VIDEO["creatify/boreal-h3"] * seconds)
    question = (
        reason + f"Changing the voice means filming every scene again. The estimated clip cost is "
        f"{estimate} for one try per scene; pictures, checks and retries can add to it. "
        "Do you agree to that cost?"
    )
    if not call.session.messages.filter(
        role=Message.Role.AGENT, text=question, created_at__gte=call.created_at
    ).exists():
        messages.add(call.session, role=Message.Role.AGENT, text=question)
    return (
        _QUOTE
        + json.dumps({"fingerprint": fingerprint, "question": question, "quote": call.pk})
        + "\nNothing changed. Ask the owner and wait; after consent call remake_person "
        "with this cost_quote and their full confirmation to refilm every scene."
    )


def consent(
    job: Job,
    call: ToolCall,
    proposal: dict[str, Any],
    *,
    quote_id: int | None,
    confirmation: str | None,
) -> str | None:
    """Return a new quote, or allow precisely the quoted change after full-message consent.

    There is no new approval model: the quote's immutable result and arguments are its
    checkpoint, and the later call keeps the owner's full confirmation in its arguments.
    """
    fingerprint = _fingerprint(job, proposal)
    if quote_id is None:
        return _quote(job, call, fingerprint)
    quoted = ToolCall.objects.filter(
        pk=quote_id,
        session=call.session,
        job=job,
        tool="remake_person",
        finished_at__lt=call.created_at,
    ).first()
    if quoted is None or not quoted.result.startswith(_QUOTE):
        raise Refused(
            "that voice-change cost quote does not exist in this ad. Request a quote first."
        )
    record = json.loads(quoted.result.removeprefix(_QUOTE).split("\n", 1)[0])
    if record["fingerprint"] != fingerprint:
        return _quote(
            job,
            call,
            fingerprint,
            reason="The voice change or the scenes being filmed changed since that quote. "
            "Here is the updated price. ",
        )
    reply = (
        call.session.messages.filter(
            role=Message.Role.USER,
            pasted_page=False,
            created_at__gt=quoted.finished_at,
            created_at__lt=call.created_at,
        )
        .order_by("seq")
        .last()
    )
    if reply is None or confirmation is None or reply.text != confirmation:
        raise Refused(
            "the owner has not confirmed this cost quote. Ask for agreement to the cost "
            "and wait for their reply; pass their full reply exactly as written."
        )
    return None


def completed(call: ToolCall) -> str | None:
    """A committed result can finish a recovered tool without repeating its changes."""
    return call.result if call.result.startswith(_APPLIED) else None


def checkpoint(call: ToolCall, *, voice_changed: bool, text: str) -> str:
    """Keep structured recovery state in the existing checkpoint, with its explanation.

    The producer commits this alongside the description and attachments. Its loop later
    keeps the same result when it marks the tool finished.
    """
    call.result = _APPLIED + json.dumps({"voice_changed": voice_changed}) + "\n" + text
    call.save(update_fields=["result"])
    return call.result


def changed_after(job: Job, started: datetime) -> bool:
    """Whether an approved voice change supersedes work that started before it."""
    return ToolCall.objects.filter(
        job=job,
        tool="remake_person",
        arguments__remake__in=["voice", "both"],
        result__startswith=_APPLIED,
        created_at__gt=started,
    ).exists()
