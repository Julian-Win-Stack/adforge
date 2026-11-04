"""A checkpointed scene conversation, with a separate money window for each run."""

from typing import Any

from django.db import transaction
from django.utils import timezone

from agents import loop
from agents.models import ToolCall
from agents.owner_quotes import check_session_quotes
from chat import messages
from chat.models import Message
from gateway.gateway import charged_to
from gateway.types import Happened, Said, Shown, ToolUse
from jobs import cut_and_send, notices, owner_checks, work
from jobs.checks import FIXED_WORDS_PER_SECOND, h3_clip_seconds
from jobs.models import AgentFix, ProducedItem, SceneAgentRun, SceneStep

from . import lifecycle, media
from .ask_owner import AskOwner
from .change_scene import SNAPSHOT, ChangeScene
from .draw_starting_picture import DrawStartingPicture
from .edit_starting_picture import EditStartingPicture
from .film import Film
from .give_up import GiveUp
from .instructions import INSTRUCTIONS
from .look_at_library import LookAtLibrary
from .look_at_try import LookAtTry

PENDING_NOTE_DELIVERED = "Handed to the next scene step."
AGENT = "scene_agent"
PURPOSE = "scene_agent"
TOOLS: list[type[loop.Tool]] = [
    LookAtLibrary,
    LookAtTry,
    ChangeScene,
    DrawStartingPicture,
    EditStartingPicture,
    Film,
    AskOwner,
    GiveUp,
]
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
                "scene_before": {field: getattr(step.scene, field) for field in SNAPSHOT},
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
        lifecycle.give_up(fixing, reason)
        lifecycle._put_back(fixing.step.scene, run.scene_before)
        run.reason = reason
        run.finished_at = timezone.now()
        run.save(update_fields=["reason", "finished_at"])
        tries = work.clip_tries(fixing.step)
        if tries:
            fixing = work._ended(fixing.step)
        elif fixing.first_clip_id is not None:
            fixing.kept = fixing.first_clip
            fixing.save(update_fields=["kept"])
        if fixing.ended_by_cap:
            work.say_the_agent_gave_up(fixing.step)
            return
        kept = fixing.kept_id is not None
        number = fixing.step.scene.number
        notices.post_notice(
            fixing.step.scene.job,
            notices.SCENE_AGENT_GAVE_UP if kept else notices.SCENE_AGENT_NEEDS_CHOICE,
            f"I couldn't make scene {number}: {reason} "
            + (
                "Its last clip stays in the ad. You can pick any try on its card. "
                if kept
                else "This scene waits for your choice. "
            )
            + work.scene_way_out(fixing.step.scene),
            Message.Level.INFO,
            step=messages.StepSignal(
                number,
                Message.StepKind.CLIP,
                Message.StepState.MADE if kept else Message.StepState.NOT_MADE,
            ),
            tries_of=fixing.step_id if tries else None,
        )


def _conversation(fixing: AgentFix) -> list[Happened]:
    step = fixing.step
    step.scene.refresh_from_db()
    job = step.scene.job
    audio = step.made_from
    seconds = audio.seconds if audio is not None else 0
    seconds = len(step.scene.line.split()) / FIXED_WORDS_PER_SECOND
    clip_seconds = h3_clip_seconds(len(step.scene.line.split()))
    plan = "\n".join(media._scene_facts(each) for each in job.scenes.order_by("number"))
    intro = "\n\n".join(
        [
            f"The ad is for this product page: {job.product_url}",
            f"You own scene {step.scene.number}.",
            media._library_words(job),
            f"How this product behaves, as the planner wrote it: {job.product_behaviour}",
            f"The whole plan:\n{plan}",
            f"The line takes {seconds or 0:g} s; the clip is {clip_seconds} s.",
            f"The page's words:\n{job.page_text}",
            *(
                [
                    "Draw the product in the page video's colour: "
                    f"{cut_and_send.planned_colour(step.scene)}."
                ]
                if step.scene.piece is not None
                else []
            ),
        ]
    )
    happened: list[Happened] = [Said(by="user", text=intro)]
    drawn, shared = work.scene_place(step.scene, step.scene.person_shown)
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
        windows = list(record.scene_runs.exclude(reason=PENDING_NOTE_DELIVERED).order_by("number"))

        def add_window(window: SceneAgentRun) -> None:
            happened.append(Said(by="user", text=f"Producer's instruction: {window.instruction}"))
            owner_check = owner_checks.context_for(step.scene, window.quotes)
            if owner_check:
                happened.append(Said(by="user", text=owner_check))
            if window.quotes:
                happened.append(
                    Said(
                        by="user",
                        text="Owner's words (checked word for word):\n"
                        + "\n".join(quote["text"] for quote in window.quotes),
                    )
                )
            from agents.note_photos import photos_handed_with

            photos = photos_handed_with(window)
            if photos:
                happened.append(
                    Said(
                        by="user",
                        text="From the app, photos handed with this scene's note:\n"
                        + "\n".join(
                            f"Owner photo {number}, sent with this scene's note: "
                            f"library item {item.number}."
                            for number, item in photos
                        ),
                    )
                )

        said: list[dict[str, Any]] = list(record.said)
        calls = lifecycle._calls(record).filter(finished_at__isnull=False)
        for number, call in enumerate(calls):
            while windows and windows[0].created_at <= call.created_at:
                add_window(windows.pop(0))
            happened.extend(media._said(said, number))
            happened.append(
                ToolUse(
                    call_id=call.call_id,
                    tool=call.tool,
                    arguments=call.arguments,
                    result=call.result,
                    pictures=[Shown(**picture) for picture in call.pictures],
                )
            )
        happened.extend(media._said(said, None))
        for window in windows:
            add_window(window)
    # Trim tool pictures while preserving the shared picture at the beginning.
    shared_part = happened[:2] if shared is not None else happened[:1]
    return [
        *shared_part,
        *media._fit(happened[len(shared_part) :], reserved=1 if shared is not None else 0),
    ]


def _settle_checkpoint(fixing: AgentFix, call: ToolCall, *, recovering: bool = False) -> None:
    """Recover paid work in flight; a paused scene starts no untouched checkpoint."""
    recovering_paid = recovering and call.model_calls.exists()
    if not recovering_paid:
        lifecycle._parked_by_the_pause(fixing)
    if lifecycle._ended(fixing):
        loop._refuse_unrun(call, loop.PAUSED if fixing.paused_at else "the run ended.")
    else:
        loop.settle(AGENT_SPEC, call, counted=False)


def run(step: SceneStep) -> ProducedItem | None:
    fixing = started(step)
    pending = list(lifecycle._calls(fixing).filter(finished_at__isnull=True))
    if loop.paused(step.tool_call.session) and not any(
        call.model_calls.exists() for call in pending
    ):
        lifecycle._parked_by_the_pause(fixing)
        for call in pending:
            _settle_checkpoint(fixing, call)
        return fixing.kept
    try:
        person_shown = step.person_shown
        person_shown = work.person_shown_for(step.scene.kind)
        work.person_and_room(step, person_shown)
    except work.PlaceNotDrawn as error:
        work.say_the_place_wasnt_drawn(step, error)
        raise
    for call in pending:
        _settle_checkpoint(fixing, call, recovering=True)
    from jobs import owner_answers

    owner_answers.take_the_answer(step)
    fixing.refresh_from_db()
    while not lifecycle._ended(fixing):
        owner_answers.take_the_answer(step)
        fixing.refresh_from_db()
        if loop.paused(step.tool_call.session):
            lifecycle._parked_by_the_pause(fixing)
            break
        turn_run_id = current_run(fixing).pk
        with charged_to(lifecycle._turns(fixing)):
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
                        "at": lifecycle._calls(fixing).count(),
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
            if current_run(fixing).pk != turn_run_id:
                loop._refuse_unrun(
                    call, "a new note arrived; read it before choosing your next tool."
                )
            else:
                _settle_checkpoint(fixing, call)
    fixing.refresh_from_db()
    return fixing.passed or fixing.kept


def finished(step: SceneStep, made: ProducedItem | None) -> str:
    from agents.tasks import _cost

    fixing = AgentFix.objects.get(step=step)
    end = "parked for the owner's pause" if fixing.paused_at else f"gave up: {fixing.gave_up}"
    if fixing.gave_up and not fixing.paused_at:
        end += (
            " The last clip stays in the ad; the owner can pick any try."
            if fixing.kept_id is not None
            else " Wait for the owner's choice. " + work.scene_way_out(step.scene)
        )
    if fixing.passed_id is not None:
        end = "passed"
        messages.add(
            step.tool_call.session,
            role=Message.Role.NOTICE,
            step=messages.StepSignal(
                step.scene.number, Message.StepKind.CLIP, Message.StepState.MADE
            ),
        )
    unchecked = (
        " The clip judge couldn't check this clip, so it is kept unchecked."
        if made is not None and (made.ruling or {}).get("verdict") == "not_checked"
        else ""
    )
    return (
        f"Background step finished: scene {step.scene.number}'s agent {end}."
        f"{_cost(step)}{unchecked}"
    )
