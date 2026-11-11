"""Offer the price of restoring an old voice/presenter version before paid filming."""

import json

from chat import messages
from chat.models import Message
from jobs import work
from jobs.models import Scene

from .loop import Refused
from .models import ToolCall

PREFIX = "version-refilm-quote:v1 "


def consent(
    scene: Scene, version: work.Version, call: ToolCall, quote: int | None, confirmation: str | None
) -> str | None:
    assert version.clip is not None
    offer = work.version_refilm_offer(scene, version)
    proposal = {
        "scene": scene.pk,
        "clip": version.clip.pk,
        "offer": offer,
    }
    if quote is None:
        messages.add(call.session, role=Message.Role.AGENT, text=offer)
        return (
            PREFIX
            + json.dumps(proposal)
            + (
                f"\nNothing changed or paid for. Wait for the owner's answer. If they agree, "
                f"call use_version with cost_quote={call.pk} and their full reply in confirmation."
            )
        )
    quoted = ToolCall.objects.filter(pk=quote, session=call.session, tool="use_version").first()
    if quoted is None or not quoted.result.startswith(PREFIX):
        raise Refused("Quote the refilm cost first, then wait for the owner's agreement.")
    saved = json.loads(quoted.result.removeprefix(PREFIX).split("\n", 1)[0])
    if saved != proposal:
        raise Refused("That refilm quote has changed. Quote it again before filming.")
    reply = call.session.messages.filter(
        role=Message.Role.USER, created_at__gt=quoted.created_at
    ).last()
    if reply is None or not confirmation or reply.text != confirmation:
        raise Refused("Wait for the owner's agreement to the quoted refilm cost.")
    return None
