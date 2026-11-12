"""Restore baseline outputs in disposable eval databases, before new paid work."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .scenarios import Scenario
from .store import Store, read_json

if TYPE_CHECKING:
    from agents.loop import Agent
    from agents.models import ToolCall
    from jobs.models import Job

type Stage = Literal["plan", "pictures", "clips", "notes"]


class MissingPrefix(ValueError):
    """A saved baseline cannot supply the requested earlier outputs."""


@dataclass(frozen=True)
class ReplaySource:
    run: str
    stage: Stage
    folder: Path
    dump: Path
    media: Path
    session_id: str

    @property
    def provenance(self) -> dict[str, str]:
        return {"run": self.run, "stage": self.stage}


def source_for(store: Store, scenario: Scenario, stage: Stage) -> ReplaySource:
    baseline = store.read_baselines().get(scenario.key)
    if baseline is None:
        raise MissingPrefix("no baseline; run it from the start first")
    folder = store.scenario_dir(baseline, scenario.key)
    if not store.scenario_done(baseline, scenario.key):
        raise MissingPrefix(f"baseline {baseline} is unfinished")
    saved = folder / "checkpoints" / "page" if stage == "plan" else folder
    metadata = read_json(saved / "scenario.json")
    session_id = metadata.get("session_id")
    if not session_id or not (saved / "db.dump").is_file() or not (saved / "media").is_dir():
        raise MissingPrefix(f"baseline {baseline} has no complete {stage} prefix")
    if stage == "plan" and not (saved / "done").is_file():
        raise MissingPrefix(f"baseline {baseline} has no complete page checkpoint")
    return ReplaySource(baseline, stage, folder, saved / "db.dump", saved / "media", session_id)


def prepare(source: ReplaySource, destination: Path) -> Job:
    """Trim only the disposable restored database; leave saved baselines untouched."""
    from django.db import transaction

    from agents.models import ToolCall
    from chat.models import Session
    from gateway.models import ModelCall
    from jobs.models import Job

    with transaction.atomic():
        job = Job.objects.get(session_id=source.session_id)
        if source.stage == "clips":
            _keep_pictures(job, read_json(source.folder / "kept-pictures.json", []))
        else:
            # A saved page can contain unfinished later tools from the same producer turn.
            pending = ToolCall.objects.filter(
                session_id=source.session_id, finished_at__isnull=True
            )
            ModelCall.objects.filter(tool_call__in=pending).delete()
            pending.delete()
        ModelCall.objects.filter(session_id=source.session_id, purpose="produce").delete()
        Session.objects.filter(pk=source.session_id).update(
            producer_running=False,
            producer_copy=None,
            producer_beat_at=None,
            producer_seen_at=None,
            paused_at=None,
        )
    # Keep the reusable page receipt with this new run too, so it can be promoted.
    for name in ("page", "plan"):
        checkpoint = source.folder / "checkpoints" / name
        if checkpoint.is_dir():
            shutil.copytree(checkpoint, destination / "checkpoints" / name, dirs_exist_ok=True)
    return job


def _picture_dependencies(job: Job, selected: set[int]) -> set[int]:
    """An edited picture keeps the ancestor whose photos/place it inherited."""
    from jobs.models import ProducedItem

    found: set[int] = set()
    waiting = list(selected)
    while waiting:
        pk = waiting.pop()
        if pk in found:
            continue
        picture = job.produced.get(pk=pk)
        if picture.kind not in (ProducedItem.Kind.STARTING_PICTURE, ProducedItem.Kind.END_PICTURE):
            raise MissingPrefix(f"baseline picture {pk} has the wrong kind")
        found.add(pk)
        if previous := (picture.sent or {}).get("edited_from"):
            waiting.append(int(previous))
        if picture.kind == ProducedItem.Kind.END_PICTURE and picture.made_from_id:
            waiting.append(picture.made_from_id)
    return found


def _session(job: Job) -> str:
    if job.session_id is None:
        raise MissingPrefix("baseline job has no chat session")
    return str(job.session_id)


def _later_calls(job: Job, keep: set[int]) -> set[int]:
    from django.db.models import Q

    from agents.models import ToolCall

    return set(
        ToolCall.objects.filter(session_id=_session(job))
        .filter(
            Q(agent="scene_agent")
            | Q(tool__in=("start_scene_agent", "pass_note", "assemble_ad", "look_at_clip"))
        )
        .exclude(pk__in=keep)
        .values_list("pk", flat=True)
    )


def _remove_calls(job: Job, calls: set[int]) -> None:
    from agents.models import ToolCall
    from gateway.models import ModelCall

    # Shared images may have been charged to a scene's startup checkpoint. Keep their
    # actual image-call inputs even when that execution is replaced.
    shared = job.produced.filter(scene__isnull=True).exclude(kind="finished_ad")
    ModelCall.objects.filter(
        tool_call_id__in=calls, output__file__in=list(shared.values_list("file", flat=True))
    ).update(tool_call=None)
    ModelCall.objects.filter(tool_call_id__in=calls).delete()
    ModelCall.objects.filter(session_id=_session(job), purpose="scene_agent").delete()
    ToolCall.objects.filter(pk__in=calls).delete()


def _remove_items(job: Job, ids: set[int]) -> None:
    # PROTECT self-links among the rows being removed must be cleared first; retained
    # picture/room/portrait dependencies are left exactly as saved.
    rows = job.produced.filter(pk__in=ids)
    rows.update(made_from=None, picture=None, person=None)
    rows.delete()


def _keep_pictures(job: Job, selections: list[dict[str, Any]]) -> None:
    from django.utils import timezone

    from agents.models import ToolCall
    from chat.models import Message
    from jobs.models import (
        AgentFix,
        AgentFixTry,
        Job,
        Scene,
        SceneAgentRun,
        SceneStep,
    )

    by_scene = {int(row["scene_id"]): row for row in selections}
    if set(by_scene) != set(job.scenes.values_list("pk", flat=True)):
        raise MissingPrefix("baseline has no kept starting picture for every scene")
    selected = {int(row["picture"]) for row in selections}
    selected.update(int(row["end_picture"]) for row in selections if row.get("end_picture"))
    kept = _picture_dependencies(job, selected)
    retained_steps = {int(row["step"]) for row in selections}
    retained_fixes = {int(row["fixing"]) for row in selections}
    retained_calls = set(
        SceneStep.objects.filter(pk__in=retained_steps).values_list("tool_call_id", flat=True)
    )
    for picture in job.produced.filter(pk__in=kept):
        assert picture.scene_id is not None
        row = by_scene[picture.scene_id]
        if draw_call := (picture.sent or {}).get("call"):
            retained_calls.add(int(draw_call))
            ToolCall.objects.filter(pk=int(draw_call)).update(agent_fix_id=row["fixing"])
        # A selected older picture can have belonged to an earlier scene window. Its
        # replayed execution now owns the same immutable output, as session copying does.
        picture.step_id = row["step"]
        picture.sent = {**(picture.sent or {}), "run": row["window"]["id"]}
        picture.save(update_fields=["step", "sent"])

    calls = _later_calls(job, retained_calls)
    AgentFixTry.objects.filter(agent_fix__step__scene__job=job).delete()
    AgentFix.objects.filter(step__scene__job=job).update(
        passed=None, kept=None, first_clip=None, finished_at=None, gave_up="", ended_by_cap=False
    )
    # Preserve picture receipts before cascading old execution windows away.
    _remove_calls(job, calls)
    AgentFix.objects.filter(step__scene__job=job).exclude(pk__in=retained_fixes).delete()
    unwanted_steps = SceneStep.objects.filter(scene__job=job).exclude(pk__in=retained_steps)
    unwanted_steps.update(picture_step=None, picture=None, made_from=None)
    unwanted_steps.delete()
    discarded = set(
        job.produced.filter(
            kind__in=("clip", "transcript", "line_audio", "finished_ad")
        ).values_list("pk", flat=True)
    )
    discarded.update(
        job.produced.filter(kind__in=("starting_picture", "end_picture"))
        .exclude(pk__in=kept)
        .values_list("pk", flat=True)
    )
    _remove_items(job, discarded)

    for row in selections:
        scene_id, step_id, fix_id = int(row["scene_id"]), int(row["step"]), int(row["fixing"])
        scene = {
            key: value
            for key, value in row["scene_row"].items()
            if key not in ("id", "job_id", "status")
        }
        Scene.objects.filter(pk=scene_id, job=job).update(**scene, status=Scene.Status.PLANNED)
        step = {
            key: value
            for key, value in row["step_row"].items()
            if key
            not in (
                "id",
                "scene_id",
                "tool_call_id",
                "status",
                "seen_at",
                "taken_at",
                "finished_at",
                "producer_read_at",
                "result",
                "reason",
            )
        }
        SceneStep.objects.filter(pk=step_id).update(
            **step,
            status=SceneStep.Status.RUNNING,
            seen_at=timezone.now(),
            taken_at=None,
            finished_at=None,
            producer_read_at=None,
            result="",
            reason="",
        )
        window = {
            key: value
            for key, value in row["window"].items()
            if key
            not in (
                "id",
                "agent_fix_id",
                "clips",
                "finished_at",
                "reason",
                "asked",
                "clips_at_talking_switch",
            )
        }
        SceneAgentRun.objects.filter(agent_fix_id=fix_id).exclude(pk=row["window"]["id"]).delete()
        SceneAgentRun.objects.filter(pk=row["window"]["id"]).update(
            **window,
            clips=0,
            finished_at=None,
            reason="",
            asked=False,
            clips_at_talking_switch=None,
        )
        AgentFix.objects.filter(pk=fix_id).update(said=[], scene_before=scene)
    job.status = Job.Status.READY_TO_RENDER
    job.save(update_fields=["status"])
    first = min(row["step_row"]["started_at"] for row in selections)
    Message.objects.filter(
        session_id=_session(job), role=Message.Role.AGENT, created_at__gte=first
    ).delete()


def before_tool(stage: Stage | None, agent: Agent, call: ToolCall) -> str | None:
    if stage is None:
        return None
    if agent.name == "producer" and call.tool == "read_page":
        return "This run already replayed the saved page; continue from its saved outputs."
    if (
        stage != "clips"
        or call.agent_fix is None
        or call.tool not in ("draw_starting_picture", "edit_starting_picture")
    ):
        return None
    from agents.scene_agent.draw_starting_picture import current_picture
    from jobs import scene_pictures

    picture = current_picture(call)
    fixing = call.agent_fix
    failed = fixing.tries.filter(clip__isnull=False, passed=False).exists()
    move_failed = fixing.tool_calls.filter(
        model_calls__purpose="check_move", model_calls__output__blocked=True
    ).exists()
    if (
        picture is not None
        and (picture.ruling or {}).get("status") in ("passed", "unchecked")
        and scene_pictures.matches(picture, fixing.step.scene)
        and not failed
        and not move_failed
    ):
        return (
            "The kept starting picture was replayed and is still current; film it before redrawing."
        )
    return None
