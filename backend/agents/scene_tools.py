"""The producer's doors into the scene agent; expanded beside today's B-roll path."""

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from agents import asked_changes
from agents.loop import Refused, Tool
from agents.models import AskedChangeFiling, SystemNote, ToolCall
from agents.owner_quotes import QuoteNotFound, check_session_quotes
from chat.models import Message
from jobs import notices, work
from jobs.models import Job, OwnerQuestion, ProducedItem, Scene, SceneAgentRun, SceneStep
from jobs.pace import h3_clip_seconds

logger = logging.getLogger(__name__)


class StartSceneAgent(Tool):
    """Start one scene's agent in the background, with the producer's instruction.
    Both scene kinds need a checked line and a presenter."""

    name = "start_scene_agent"
    scene: int
    instruction: str = Field(description="What this scene must do, from the producer.")

    quotes: list[str] = Field(
        min_length=1, description="Exact chunks of the owner's own messages for this scene."
    )

    def run(self, call: ToolCall) -> str:
        from . import producer

        try:
            check_session_quotes(call.session, self.quotes)
        except QuoteNotFound as error:
            raise Refused(str(error)) from error
        scene = producer._a_checked_scene(call, self.scene)
        try:
            h3_clip_seconds(len(scene.line.split()))
        except ValueError as error:
            raise Refused(
                f"{error} Run the planning checks and get a shorter line before filming."
            ) from error
        if not self.instruction.strip():
            raise Refused("say what this scene must do in the producer's instruction.")
        if work.latest(scene.job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused("the presenter hasn't been made yet. Create the person first.")
        for kind in (
            SceneStep.Kind.STARTING_PICTURE,
            SceneStep.Kind.LINE_AUDIO,
            SceneStep.Kind.TRANSCRIPT,
            SceneStep.Kind.CLIP,
        ):
            if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
                raise Refused(
                    f"scene {scene.number}'s {producer.STEP_NAMES[kind]} is still being made."
                )
        with transaction.atomic():
            if call.tool != "pass_note":
                from .scene_recovery import last_fix, same_owner_message

                Job.objects.select_for_update().get(pk=scene.job_id)
                asked_changes.require_producer_start(call, scene)
                previous = last_fix(scene)
                if previous is not None and previous.gave_up and same_owner_message(call, previous):
                    raise Refused(
                        "after a give-up, use pass_note for the producer's one extra idea."
                    )
            producer._start_step(
                call,
                scene,
                SceneStep.Kind.SCENE_AGENT,
                busy=f"scene {scene.number}'s agent is already working.",
                note=self.instruction.strip(),
            )
        return (
            f"Started scene {scene.number}'s agent in the background; "
            "you will be told how it ended and what it cost."
        )


class SceneNote(BaseModel):
    scene: int
    instruction: str = Field(min_length=1)
    quotes: list[str] = Field(min_length=1)
    asked_change: int | None = Field(
        default=None, description="Number of the asked change on file, if it is the same result."
    )
    new_asked_change: str | None = Field(
        default=None, description="One plain sentence saying what new result the owner asked for."
    )
    owner_asked: bool = Field(
        default=False,
        description="True only when the shop owner asked for this change. Leave it false "
        "for your own re-plan while making the first ad or when the owner says to finish "
        "with what there is.",
    )


class PassNote(Tool):
    """Pass a separate instruction and checked owner quotes to each scene, even while busy.
    A note opens a fresh run in the scene's existing conversation."""

    name = "pass_note"
    scenes: list[SceneNote] = Field(min_length=1)

    def run(self, call: ToolCall) -> str:
        from . import producer

        if len({note.scene for note in self.scenes}) != len(self.scenes):
            raise Refused("name each scene once, with all its instruction and quotes.")
        said = []
        with transaction.atomic():
            # Serialize every scene's window against the worker's finished-step handling.
            job = producer._the_job(call)
            if job is not None:
                Job.objects.select_for_update().get(pk=job.pk)
            for note in self.scenes:
                try:
                    # A refused scene rolls back only its own handoff.
                    with transaction.atomic():
                        if not note.instruction.strip():
                            raise Refused("say what this scene must do in the instruction.")
                        scene = producer._a_checked_scene(call, note.scene)
                        earlier = (
                            asked_changes.filed_already(call, scene) if note.owner_asked else ""
                        )
                        if earlier:
                            said.append(earlier)
                            continue
                        if SceneAgentRun.objects.filter(
                            agent_fix__step__scene=scene,
                            quotes__contains=[{"handed_by": call.pk}],
                        ).exists():
                            said.append(f"Scene {scene.number}'s note was handed on already.")
                            continue
                        try:
                            verified = check_session_quotes(call.session, note.quotes)
                        except QuoteNotFound as error:
                            raise Refused(
                                f"the quote {error.quote!r} wasn't found in the owner's "
                                "messages; quote it exactly."
                            ) from error
                        quotes = [
                            {
                                "text": quote.text,
                                "message_id": quote.message.pk,
                                "handed_by": call.pk,
                                "owner_asked": note.owner_asked,
                            }
                            for quote in verified
                        ]
                        filing = None
                        if note.owner_asked:
                            filing = asked_changes.file_note(
                                call,
                                scene,
                                asked_change=note.asked_change,
                                new_asked_change=note.new_asked_change,
                                instruction=note.instruction.strip(),
                            )
                            if filing.refusal:
                                said.append(filing.refusal)
                                continue
                        else:
                            asked_changes.require_producer_start(call, scene)
                        from .scene_recovery import retry_quotes

                        quotes = retry_quotes(call, scene, quotes, owner_asked=note.owner_asked)
                        question = OwnerQuestion.objects.filter(
                            scene=scene, answered_at__isnull=True, answer__isnull=True
                        ).first()
                        if question is not None and producer._sent_files_since(call, question):
                            answer = producer._hand_the_answer_back(
                                call,
                                scene,
                                question,
                                note.instruction,
                                checked_quotes=quotes,
                                asked_filing=filing,
                            )
                            answer_run = filing.runs.order_by("pk").last() if filing else None
                            said.append(answer + asked_changes.filed_line(filing, answer_run))
                            continue
                        opened = open_note(call, note, scene, quotes, asked_filing=filing)
                        if isinstance(opened, str):
                            said.append(asked_changes.note_could_not_start(filing, opened))
                            continue
                        answer = (
                            f"Scene {scene.number}'s note opens a new run in its same chat; "
                            "you will be told how it ends."
                        )
                        previous = opened.agent_fix.scene_runs.filter(
                            number=opened.number - 1, reason=SceneAgentRun.SUPERSEDED
                        ).first()
                        said.append(
                            answer
                            + asked_changes.filed_line(filing, opened)
                            + asked_changes.run_ended(opened.agent_fix.step, previous)
                        )
                except Refused as error:
                    answer = f"Scene {note.scene}'s note was refused: {error}"
                    if isinstance(error, asked_changes.BriefBeforeRepeat):
                        asked_changes.record_repeat_brief(error, answer)
                    said.append(answer)
            said.extend(asked_changes.linked_filing_lines(call))
        return "\n".join(said)


def open_note(
    call: ToolCall,
    note: SceneNote,
    scene: Scene,
    quotes: list[dict[str, Any]],
    *,
    asked_filing: AskedChangeFiling | None = None,
) -> SceneAgentRun | str:
    """Open the normal note window; historical question answers use the same entry."""
    from . import scene_agent
    from .scene_agent.change_scene import SNAPSHOT

    step = scene.steps.filter(
        kind=SceneStep.Kind.SCENE_AGENT, status=SceneStep.Status.RUNNING
    ).first()
    if step is None:
        return _start_idle_note(call, note, quotes, asked_filing=asked_filing)
    fixing = scene_agent.started(step)
    window = scene_agent.current_run(fixing)
    if window.finished_at is None:
        window.finished_at = timezone.now()
        window.reason = SceneAgentRun.SUPERSEDED
        window.save(update_fields=["finished_at", "reason"])
    snapshot = {field: getattr(scene, field) for field in SNAPSHOT}
    opened = SceneAgentRun.objects.create(
        agent_fix=fixing,
        number=window.number + 1,
        instruction=note.instruction.strip(),
        quotes=quotes,
        scene_before=snapshot,
        asked_filing=asked_filing,
    )
    fixing.scene_before = snapshot
    fixing.save(update_fields=["scene_before"])
    return opened


def _start_idle_note(
    call: ToolCall,
    note: SceneNote,
    quotes: list[dict[str, Any]],
    *,
    asked_filing: AskedChangeFiling | None = None,
) -> SceneAgentRun | str:
    from . import scene_agent

    try:
        answer = StartSceneAgent(
            scene=note.scene, instruction=note.instruction, quotes=note.quotes
        ).run(call)
    except Refused as error:
        return f"Scene {note.scene}'s note couldn't start: {error}"
    step = call.scene_steps.filter(
        scene__number=note.scene,
        kind=SceneStep.Kind.SCENE_AGENT,
        status=SceneStep.Status.RUNNING,
    ).first()
    if step is None:
        return answer
    fixing = scene_agent.started(step)
    window = scene_agent.current_run(fixing)
    window.quotes = quotes
    window.asked_filing = asked_filing
    window.save(update_fields=["quotes", "asked_filing"])
    return window


def start_pending_note(step: SceneStep) -> bool:
    """After its finish commits, start the late note or tell the producer to pass it again."""
    from . import scene_agent

    if step.kind != SceneStep.Kind.SCENE_AGENT:
        return False
    with transaction.atomic():
        Job.objects.select_for_update().get(pk=step.scene.job_id)
        fixing = step.agent_fix
        pending = scene_agent.current_run(fixing)
        if (
            fixing.finished_at is None
            or pending.finished_at is not None
            or pending.created_at <= fixing.finished_at
        ):
            return False
        try:
            with transaction.atomic():
                call = ToolCall.objects.get(pk=pending.quotes[0]["handed_by"])
                opened = _start_idle_note(
                    call,
                    SceneNote(
                        scene=step.scene.number,
                        instruction=pending.instruction,
                        quotes=[quote["text"] for quote in pending.quotes],
                    ),
                    pending.quotes,
                    asked_filing=pending.asked_filing,
                )
        except Exception as error:
            logger.exception("Pending note for scene %s couldn't start", step.scene.number)
            opened = f"Scene {step.scene.number}'s note couldn't start: {error}"
            notices.post_notice(
                step.scene.job, notices.PENDING_NOTE_FAILED, opened, Message.Level.PROBLEM
            )
        if isinstance(opened, str):
            # The completed clip is already committed. Keep this note's exact words
            # in its run and a separate producer note, including how to retry. A
            # producer turn may already have read the completed step's result.
            pending.reason = (
                f"{opened} "
                f"Instruction: {pending.instruction} "
                f"Owner quotes: {[quote['text'] for quote in pending.quotes]}. "
                "Resolve the reason or choose a workable scene, then pass it again with "
                "pass_note yourself. Respect the owner’s pause and paid-change consent; "
                "otherwise continue without waiting for another owner message."
            )
            pending.step_failed = True
            SystemNote.objects.create(
                session=step.tool_call.session,
                kind=SystemNote.Kind.NOTE_WAITING,
                text=pending.reason + asked_changes.run_ended(step, pending),
            )
        else:
            pending.reason = scene_agent.PENDING_NOTE_DELIVERED
        pending.finished_at = timezone.now()
        pending.save(update_fields=["finished_at", "reason", "step_failed"])
        return True


def forward_change(call: ToolCall, scene: Scene, instruction: str) -> str:
    """Hand a producer change to the busy scene through its normal pending-note window."""
    from chat.models import Message

    with transaction.atomic():
        Job.objects.select_for_update().get(pk=scene.job_id)
        if not scene.steps.filter(
            kind=SceneStep.Kind.SCENE_AGENT, status=SceneStep.Status.RUNNING
        ).exists():
            return ""
        if SceneAgentRun.objects.filter(
            agent_fix__step__scene=scene, quotes__contains=[{"handed_by": call.pk}]
        ).exists():
            return ""
        source = (
            call.session.messages.filter(role=Message.Role.USER, pasted_page=False)
            .exclude(text="")
            .order_by("-seq")
            .first()
        )
        assert source is not None, "producer changes follow an owner message"
        scene.refresh_from_db()
        opened = open_note(
            call,
            SceneNote(scene=scene.number, instruction=instruction, quotes=[source.text]),
            scene,
            [
                {
                    "text": source.text,
                    "message_id": source.pk,
                    "handed_by": call.pk,
                    "owner_asked": False,
                }
            ],
        )
        if isinstance(opened, str):
            return opened
        return f"The {instruction} change was passed to scene {scene.number}'s busy agent."
