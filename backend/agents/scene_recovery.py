"""Bounded producer retries, followed by a talking clip or removal, never an owner wait."""

from typing import Any

from django.utils import timezone

from agents.loop import Refused
from agents.models import ToolCall
from chat.models import Message
from jobs import notices, plan_edits, work
from jobs.models import AgentFix, Scene, SceneAgentRun, SceneStep

RETRY = "producer_recovery"
TALKING = "talking_recovery"


def last_fix(scene: Scene) -> AgentFix | None:
    return (
        AgentFix.objects.filter(step__scene=scene, is_scene_agent=True)
        .order_by("created_at", "pk")
        .last()
    )


def same_owner_message(call: ToolCall, previous: AgentFix) -> bool:
    """Compare the messages that triggered calls, even when quoted words repeat."""
    window = previous.scene_runs.latest("created_at", "pk")
    handed_by = next(
        (quote["handed_by"] for quote in window.quotes if isinstance(quote.get("handed_by"), int)),
        previous.step.tool_call_id,
    )
    trigger = ToolCall.objects.get(pk=handed_by)
    owners = call.session.messages.filter(role=Message.Role.USER)
    current = owners.filter(created_at__lte=call.created_at).order_by("seq").last()
    before = owners.filter(created_at__lte=trigger.created_at).order_by("seq").last()
    return current is not None and before is not None and current.pk == before.pk


def retry_quotes(
    call: ToolCall, scene: Scene, quotes: list[dict[str, Any]], *, owner_asked: bool
) -> list[dict[str, Any]]:
    """One producer idea per scene and owner message; owner notes keep their own runs."""
    if owner_asked:
        return quotes
    source = (
        call.session.messages.filter(role=Message.Role.USER, created_at__lte=call.created_at)
        .order_by("seq")
        .last()
    )
    assert source is not None
    if SceneAgentRun.objects.filter(
        agent_fix__step__scene=scene,
        quotes__contains=[{RETRY: source.pk}],
    ).exists():
        raise Refused(
            "the producer's extra idea for this scene and owner message was already tried. "
            "Keep its clip and assemble the ad; no further producer retry is allowed."
        )
    previous = last_fix(scene)
    if (
        previous is None
        or not previous.gave_up
        or previous.paused_at
        or not same_owner_message(call, previous)
    ):
        return quotes
    return [{**quote, RETRY: source.pk} for quote in quotes]


def finish_recovery(step: SceneStep) -> bool:
    """After the producer's extra try, a missing clip gets one talking run, then leaves."""
    from agents import producer, scene_agent

    if step.kind != SceneStep.Kind.SCENE_AGENT:
        return False
    fixing = step.agent_fix
    run = scene_agent.current_run(fixing)
    if (
        run.asked_filing_id is not None
        or not fixing.gave_up
        or fixing.paused_at
        or fixing.passed_id
        or run.finished_at is None
    ):
        return False
    if fixing.kept is not None and not work.stale_clip_reason(step.scene, fixing.kept):
        return False
    if step.tool_call.session.paused_at is not None:
        return False
    retry = any(RETRY in quote for quote in run.quotes)
    talking = any(TALKING in quote for quote in run.quotes)
    if not retry and not talking:
        return False
    scene = step.scene
    number = scene.number
    if talking:
        # Preserve paid work and records while excluding the unfilmable scene from assembly.
        Scene.objects.filter(pk=scene.pk).update(removed_at=timezone.now())
        plan_edits._move_chat_scenes(scene.job, {number: None})
        plan_edits.renumber(
            scene.job,
            list(scene.job.scenes.order_by("number")),
            removing_unfilmable_scene=True,
        )
        notices.post_notice(
            scene.job,
            notices.SCENE_RECOVERY,
            f"Scene {number} couldn't be filmed, so I left it out "
            "and am finishing the rest of the ad.",
            Message.Level.INFO,
        )
        return True
    scene.change_line(scene.line, "")
    scene.kind = Scene.Kind.TALKING
    scene.kind_why = ""
    scene.talking_style = "propped"
    scene.product_state = "out of the box"
    scene.packaging_photo = None
    scene.save()
    notices.post_notice(
        scene.job,
        notices.SCENE_RECOVERY,
        f"I'll say scene {number}'s same line to camera so the ad can finish.",
        Message.Level.INFO,
    )
    producer._start_step(
        step.tool_call,
        scene,
        SceneStep.Kind.SCENE_AGENT,
        busy="the talking recovery is already running.",
        note="Say the scene's unchanged line to camera with the product. Film the talking scene.",
    )
    opened = step.tool_call.scene_steps.filter(
        scene=scene, kind=SceneStep.Kind.SCENE_AGENT, status=SceneStep.Status.RUNNING
    ).latest("pk")
    window = scene_agent.current_run(scene_agent.started(opened))
    window.quotes = [{**quote, TALKING: True} for quote in run.quotes]
    window.save(update_fields=["quotes"])
    return True
