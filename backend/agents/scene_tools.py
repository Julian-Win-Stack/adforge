"""The producer's doors into the scene agent; expanded beside today's B-roll path."""

from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from agents import hard_list
from agents.loop import Refused, Tool
from agents.models import ToolCall
from agents.owner_quotes import QuoteNotFound, check_session_quotes
from jobs import owner_parts, work
from jobs.checks import h3_clip_seconds
from jobs.models import Job, OwnerQuestion, ProducedItem, Scene, SceneAgentRun, SceneStep


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
        part = hard_list.waiting_owner_part(scene)
        if part is not None:
            call.asked_about = "the owner part"
            if not owner_parts.picture_is_current(scene):
                return producer._part_drawn(scene, part, "Its scene agent hasn't started")
            hard_list.refuse_clip_before_the_owners_yes(
                scene, part, producer._library_picture(scene)
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


class PassNote(Tool):
    """Pass a separate instruction and checked owner quotes to each scene, even while busy.
    A note opens a fresh run in the scene's existing conversation."""

    name = "pass_note"
    scenes: list[SceneNote] = Field(min_length=1)

    def run(self, call: ToolCall) -> str:
        from . import producer

        if len({note.scene for note in self.scenes}) != len(self.scenes):
            raise Refused("name each scene once, with all its instruction and quotes.")
        checked = []
        for note in self.scenes:
            if not note.instruction.strip():
                raise Refused("say what each scene must do in the producer's instruction.")
            scene = producer._a_checked_scene(call, note.scene)
            try:
                verified = check_session_quotes(call.session, note.quotes)
            except QuoteNotFound as error:
                raise Refused(str(error)) from error
            checked.append(
                (
                    note,
                    scene,
                    [
                        {"text": quote.text, "message_id": quote.message.pk, "handed_by": call.pk}
                        for quote in verified
                    ],
                )
            )
        said = []
        with transaction.atomic():
            # The job lock serializes run windows and the worker's finished-step handling.
            Job.objects.select_for_update().get(pk=checked[0][1].job_id)
            for note, scene, quotes in checked:
                scene.refresh_from_db()
                if SceneAgentRun.objects.filter(
                    agent_fix__step__scene=scene, quotes__contains=[{"handed_by": call.pk}]
                ).exists():
                    said.append(f"Scene {scene.number}'s note was handed on already.")
                    continue

                question = OwnerQuestion.objects.filter(
                    scene=scene, answered_at__isnull=True, answer__isnull=True
                ).first()
                if question is not None and producer._sent_files_since(call, question):
                    said.append(
                        producer._hand_the_answer_back(
                            call, scene, question, note.instruction, checked_quotes=quotes
                        )
                    )
                    continue
                open_note(call, note, scene, quotes)
                said.append(
                    f"Scene {scene.number}'s note opens a new run in its same chat; "
                    "you will be told how it ends."
                )
        return "\n".join(said)


def open_note(
    call: ToolCall, note: SceneNote, scene: Scene, quotes: list[dict[str, Any]]
) -> SceneAgentRun:
    """Open the normal note window; historical question answers use the same entry."""
    from . import scene_agent
    from .scene_agent.change_scene import SNAPSHOT

    step = scene.steps.filter(
        kind=SceneStep.Kind.SCENE_AGENT, status=SceneStep.Status.RUNNING
    ).first()
    if step is None:
        return _start_idle_note(call, note, quotes)
    else:
        fixing = scene_agent.started(step)
        window = scene_agent.current_run(fixing)
        window.finished_at = timezone.now()
        window.reason = "Superseded by a new owner note."
        window.save(update_fields=["finished_at", "reason"])
        snapshot = {field: getattr(scene, field) for field in SNAPSHOT}
        opened = SceneAgentRun.objects.create(
            agent_fix=fixing,
            number=window.number + 1,
            instruction=note.instruction.strip(),
            quotes=quotes,
            scene_before=snapshot,
        )
        fixing.scene_before = snapshot
        fixing.save(update_fields=["scene_before"])
        return opened


def _start_idle_note(
    call: ToolCall, note: SceneNote, quotes: list[dict[str, Any]]
) -> SceneAgentRun:
    from . import scene_agent

    StartSceneAgent(scene=note.scene, instruction=note.instruction, quotes=note.quotes).run(call)
    step = call.scene_steps.get(scene__number=note.scene, kind=SceneStep.Kind.SCENE_AGENT)
    fixing = scene_agent.started(step)
    window = scene_agent.current_run(fixing)
    window.quotes = quotes
    window.save(update_fields=["quotes"])
    return window


def start_pending_note(step: SceneStep) -> bool:
    """Under the finished step's job lock, start a note that arrived just after its agent ended."""
    from . import scene_agent

    if step.kind != SceneStep.Kind.SCENE_AGENT:
        return False
    fixing = step.agent_fix
    pending = scene_agent.current_run(fixing)
    if (
        fixing.finished_at is None
        or pending.finished_at is not None
        or pending.created_at <= fixing.finished_at
    ):
        return False
    pending.finished_at = timezone.now()
    pending.reason = scene_agent.PENDING_NOTE_DELIVERED
    pending.save(update_fields=["finished_at", "reason"])
    call = ToolCall.objects.get(pk=pending.quotes[0]["handed_by"])
    _start_idle_note(
        call,
        SceneNote(
            scene=step.scene.number,
            instruction=pending.instruction,
            quotes=[quote["text"] for quote in pending.quotes],
        ),
        pending.quotes,
    )
    return True
