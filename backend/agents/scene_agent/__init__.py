"""A checkpointed scene conversation, with a separate money window for each run."""

from typing import Any

from django.db import transaction
from django.utils import timezone

from agents import fixing_agent, loop
from agents.models import ToolCall
from agents.owner_quotes import check_session_quotes
from chat.models import Message
from gateway.gateway import charged_to
from gateway.types import Happened, Said, Shown, ToolUse
from jobs import notices, work
from jobs.models import AgentFix, ProducedItem, SceneAgentRun, SceneStep

from .ask_owner import AskOwner
from .give_up import GiveUp
from .instructions import INSTRUCTIONS
from .look_at_library import LookAtLibrary
from .look_at_try import LookAtTry

AGENT = "scene_agent"
PURPOSE = "scene_agent"
TOOLS: list[type[loop.Tool]] = [LookAtLibrary, LookAtTry, AskOwner, GiveUp]
AGENT_SPEC = loop.Agent(name=AGENT, purpose=PURPOSE, instructions=INSTRUCTIONS, tools=TOOLS)


def current_run(fixing: AgentFix) -> SceneAgentRun:
    return fixing.scene_runs.latest("created_at", "pk")


def started(step: SceneStep) -> AgentFix:
    with transaction.atomic():
        fixing, _ = AgentFix.objects.get_or_create(
            step=step,
            defaults={
                "is_scene_agent": True,
                "door": AgentFix.Door.OWNER,
                "goal": step.note,
                "first_clip": step.shown_before(ProducedItem.Kind.CLIP),
                "scene_before": {field: getattr(step.scene, field) for field in fixing_agent.KEPT},
            },
        )
        SceneAgentRun.objects.get_or_create(
            agent_fix=fixing,
            number=1,
            defaults={
                "instruction": step.note,
                "scene_before": fixing.scene_before,
                "quotes": [
                    {"text": quote.text, "message_id": quote.message.pk}
                    for quote in check_session_quotes(
                        step.tool_call.session, step.tool_call.arguments.get("quotes", [])
                    )
                ],
            },
        )
        return fixing


def finish(fixing: AgentFix, reason: str) -> None:
    with transaction.atomic():
        run = current_run(fixing)
        if run.finished_at is not None:
            return
        fixing_agent.give_up(fixing, reason)
        run.reason = reason
        run.finished_at = timezone.now()
        run.save(update_fields=["reason", "finished_at"])
        notices.post_notice(
            fixing.step.scene.job,
            notices.SCENE_AGENT_GAVE_UP,
            f"Scene {fixing.step.scene.number}'s agent gave up: {reason}",
            Message.Level.INFO,
        )


def _conversation(fixing: AgentFix) -> list[Happened]:
    step = fixing.step
    job = step.scene.job
    audio = step.made_from
    seconds = audio.seconds if audio is not None else 0
    plan = "\n".join(fixing_agent._scene_facts(each) for each in job.scenes.order_by("number"))
    intro = "\n\n".join(
        [
            f"The ad is for this product page: {job.product_url}",
            f"You own scene {step.scene.number}.",
            fixing_agent._library_words(job),
            f"How this product behaves, as the planner wrote it: {job.product_behaviour}",
            f"The whole plan:\n{plan}",
            f"The line takes {seconds or 0:g} s; "
            f"the clip is {work._broll_clip_seconds(seconds or 0)} s.",
            f"The page's words:\n{job.page_text}",
        ]
    )
    happened: list[Happened] = [Said(by="user", text=intro)]
    drawn, shared = work.scene_place(step.scene, step.person_shown)
    shared_label = "Shared person and room" if drawn == "person_in_room" else "Shared room alone"
    if shared is not None:
        happened.append(
            ToolUse(
                call_id="shared-picture",
                tool="shared_picture",
                arguments={},
                result=f"{shared_label}; keep this look throughout the scene.",
                pictures=[Shown(label=shared_label, key=shared.file)],
            )
        )
    # Each record is a window on this same scene conversation, rebuilt from checkpoints.
    records = AgentFix.objects.filter(step__scene=step.scene, is_scene_agent=True).order_by(
        "created_at", "pk"
    )
    for record in records:
        run = record.scene_runs.order_by("number").first()
        if run is not None:
            happened.append(Said(by="user", text=f"Producer's instruction: {run.instruction}"))
            if run.quotes:
                happened.append(
                    Said(
                        by="user",
                        text="Owner's words (checked word for word):\n"
                        + "\n".join(quote["text"] for quote in run.quotes),
                    )
                )
        said: list[dict[str, Any]] = list(record.said)
        calls = fixing_agent._calls(record).filter(finished_at__isnull=False)
        for number, call in enumerate(calls):
            happened.extend(fixing_agent._said(said, number))
            happened.append(
                ToolUse(
                    call_id=call.call_id,
                    tool=call.tool,
                    arguments=call.arguments,
                    result=call.result,
                    pictures=[Shown(**picture) for picture in call.pictures],
                )
            )
        happened.extend(fixing_agent._said(said, None))
    # Trim tool pictures while preserving the shared picture at the beginning.
    shared_part = happened[:2] if shared is not None else happened[:1]
    rest = fixing_agent._fit(happened[len(shared_part) :])
    room = fixing_agent.MOST_PICTURES - (1 if shared is not None else 0)
    fitted: list[Happened] = []
    for each in reversed(rest):
        if isinstance(each, ToolUse) and each.pictures:
            if len(each.pictures) > room:
                each = each.model_copy(
                    update={"pictures": [], "result": f"{each.result} {fixing_agent.NOT_SHOWN}"}
                )
            else:
                room -= len(each.pictures)
        fitted.append(each)
    return [*shared_part, *reversed(fitted)]


def run(step: SceneStep) -> ProducedItem | None:
    fixing = started(step)
    try:
        work.person_and_room(step, step.person_shown)
    except work.PlaceNotDrawn as error:
        work.say_the_place_wasnt_drawn(step, error)
        raise
    for call in fixing_agent._calls(fixing).filter(finished_at__isnull=True):
        loop.settle(AGENT_SPEC, call, counted=False)
    from jobs import owner_answers

    owner_answers.take_the_answer(step)
    fixing.refresh_from_db()
    while not fixing_agent._ended(fixing):
        owner_answers.take_the_answer(step)
        fixing.refresh_from_db()
        if loop.paused(step.tool_call.session):
            fixing_agent._parked_by_the_pause(fixing)
            break
        with charged_to(fixing_agent._turns(fixing)):
            turn = loop.take_a_turn(
                session=step.tool_call.session,
                job=step.scene.job,
                purpose=PURPOSE,
                instructions=INSTRUCTIONS,
                conversation=_conversation(fixing),
                tools=[tool.spec() for tool in TOOLS],
                since=fixing.created_at,
                done=f"scene {step.scene.number}'s agent carried on",
            )
        with transaction.atomic():
            if turn.says or not turn.calls:
                fixing.said = [
                    *fixing.said,
                    {
                        "at": fixing_agent._calls(fixing).count(),
                        "text": turn.says,
                        "no_call": not turn.calls,
                    },
                ]
                fixing.save(update_fields=["said"])
            calls = [
                ToolCall.objects.create(
                    session=step.tool_call.session,
                    job=step.scene.job,
                    agent=AGENT,
                    agent_fix=fixing,
                    tool=asked.tool,
                    call_id=asked.call_id,
                    arguments=asked.arguments,
                )
                for asked in turn.calls
            ]
        for call in calls:
            fixing.refresh_from_db()
            fixing_agent._parked_by_the_pause(fixing)
            if fixing_agent._ended(fixing):
                fixing_agent._settle_unless_ended(fixing, call)
            else:
                loop.settle(AGENT_SPEC, call, counted=False)
    fixing.refresh_from_db()
    return fixing.passed


def finished(step: SceneStep, made: ProducedItem | None) -> str:
    from agents.tasks import _cost

    fixing = AgentFix.objects.get(step=step)
    end = "parked for the owner's pause" if fixing.paused_at else f"gave up: {fixing.gave_up}"
    if made is not None:
        end = "passed"
    return f"Background step finished: scene {step.scene.number}'s agent {end}.{_cost(step)}"
