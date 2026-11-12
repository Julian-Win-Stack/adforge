"""Restore baseline outputs in disposable eval databases, before new paid work."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Never

from .scenarios import Scenario
from .store import Store, read_json

if TYPE_CHECKING:
    from agents.loop import Agent
    from agents.models import ToolCall
    from jobs.models import Job

type Stage = Literal["plan", "pictures", "clips", "notes"]


class MissingPrefix(ValueError):
    """A saved baseline cannot supply the requested earlier outputs."""


class ReplayedPageSource:
    """Restored page outputs suffice; any attempt to fetch outside inputs fails closed."""

    def _unavailable(self, *args: Any, **kwargs: Any) -> Never:
        raise MissingPrefix(
            "This stage uses the baseline's saved page outputs; no page fetch is allowed"
        )

    check_where_it_points = _unavailable
    download = _unavailable
    download_to = _unavailable
    read_page = _unavailable
    marked_screenshot = _unavailable
    product_record = _unavailable
    player_file = _unavailable


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


def source_for(
    store: Store, scenario: Scenario, stage: Stage, *, baseline: str | None = ""
) -> ReplaySource:
    if baseline == "":
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
    if stage in ("pictures", "clips"):
        prefix = folder / "checkpoints/plan"
        state = read_json(prefix / "prefix-state.json")
        if not (prefix / "done").is_file() or not state.get("job") or "library" not in state:
            raise MissingPrefix(f"baseline {baseline} has no complete plan prefix")
    if stage in ("clips", "notes"):
        kept = read_json(folder / "kept-pictures.json", [])
        required = {
            "picture",
            "scene_id",
            "scene_row",
            "step",
            "step_row",
            "fixing",
            "fixing_row",
            "window",
        }
        if not kept or any(not required <= row.keys() for row in kept):
            raise MissingPrefix(f"baseline {baseline} has no kept-picture manifest")
        if stage == "notes" and (scenario.kind != "full" or any(not row.get("ad") for row in kept)):
            raise MissingPrefix(f"baseline {baseline} has no finished ad for notes")
    return ReplaySource(baseline, stage, folder, saved / "db.dump", saved / "media", session_id)


def prepare(source: ReplaySource, destination: Path, *, needs_ad: bool = False) -> Job:
    """Trim only the disposable restored database; leave saved baselines untouched."""
    from django.db import transaction

    from agents.models import ToolCall
    from chat.models import Session
    from gateway.models import ModelCall
    from jobs.models import Job

    with transaction.atomic():
        job = Job.objects.get(session_id=source.session_id)
        if source.stage in ("pictures", "clips"):
            prefix = read_json(source.folder / "checkpoints/plan/prefix-state.json")
            if str(prefix["job"]["id"]) != str(job.pk):
                raise MissingPrefix("baseline plan prefix belongs to another job")
            produced = set(job.produced.values_list("pk", flat=True))
            library = set(job.library.values_list("pk", flat=True))
            if (
                set(prefix["produced_ids"]) - produced
                or {row["id"] for row in prefix["library"]} - library
            ):
                raise MissingPrefix("baseline is missing saved plan inputs")
            if needs_ad and not job.produced.filter(kind="music").exists():
                raise MissingPrefix(
                    "baseline has no saved music; run it to full from the start first"
                )
        if source.stage == "notes":
            from chat import session_copy

            if not job.produced.filter(kind="finished_ad").exists():
                raise MissingPrefix("baseline has no finished ad for notes")
            if job.session is None or (why := session_copy.still_working(job.session)):
                raise MissingPrefix(
                    f"baseline chat is not idle: {why if job.session else 'no session'}"
                )
        elif source.stage == "pictures":
            _keep_plan(job, read_json(source.folder / "checkpoints/plan/prefix-state.json"))
        elif source.stage == "clips":
            _keep_pictures(job, read_json(source.folder / "kept-pictures.json", []))
        elif source.stage == "plan":
            # A saved page can contain unfinished later tools from the same producer turn.
            pending = ToolCall.objects.filter(
                session_id=source.session_id, finished_at__isnull=True
            )
            ModelCall.objects.filter(tool_call__in=pending).delete()
            pending.delete()
        if source.stage != "notes":
            ModelCall.objects.filter(session_id=source.session_id, purpose="produce").delete()
            Session.objects.filter(pk=source.session_id).update(
                producer_running=False,
                producer_copy=None,
                producer_beat_at=None,
                producer_seen_at=None,
                paused_at=None,
            )
    _validate_media(job, source.media)
    # Keep the reusable page receipt with this new run too, so it can be promoted.
    for name in ("page", "plan"):
        checkpoint = source.folder / "checkpoints" / name
        if checkpoint.is_dir():
            shutil.copytree(checkpoint, destination / "checkpoints" / name, dirs_exist_ok=True)
    return job


def _validate_media(job: Job, media: Path) -> None:
    """Fail before resuming a tool if one of its retained inputs is unavailable."""
    keys = {job.page_html_key}
    keys.update(job.photos.values_list("file", flat=True))
    keys.update(job.library.values_list("file", flat=True))
    keys.update(job.produced.values_list("file", flat=True))
    keys.update(job.firecrawl.get("files", {}).values())
    for video in job.videos.all():
        keys.update((video.file, video.first_frame, *video.frames))
    for key in sorted(keys - {""}):
        if not (media / key).is_file():
            raise MissingPrefix(f"baseline is missing saved media: {key}")


def _keep_plan(job: Job, prefix: dict[str, Any]) -> None:
    """Drop scene execution and its lazy image caches, preserving shared outputs."""
    from agents.models import SystemNote
    from chat.models import Message
    from jobs.models import AgentFix, AgentFixTry, Job, LibraryItem, Scene, SceneStep

    first = (
        job.scenes.filter(steps__isnull=False)
        .order_by("steps__started_at")
        .values_list("steps__started_at", flat=True)
        .first()
    )
    calls = _later_calls(job, set())
    AgentFixTry.objects.filter(agent_fix__step__scene__job=job).delete()
    AgentFix.objects.filter(step__scene__job=job).update(passed=None, kept=None, first_clip=None)
    _remove_calls(job, calls)
    AgentFix.objects.filter(step__scene__job=job).delete()
    steps = SceneStep.objects.filter(scene__job=job)
    steps.update(picture_step=None, picture=None, made_from=None)
    steps.delete()
    _remove_items(
        job,
        set(
            job.produced.filter(
                kind__in=(
                    "starting_picture",
                    "end_picture",
                    "clip",
                    "transcript",
                    "line_audio",
                    "finished_ad",
                )
            ).values_list("pk", flat=True)
        ),
    )
    library = prefix["library"]
    ids = {row["id"] for row in library}
    # Owner-supplied part drawings are plan inputs, even if approved after setup.
    parts = set(
        job.scenes.exclude(owner_part_picture=None).values_list("owner_part_picture", flat=True)
    )
    job.library.exclude(pk__in=ids | parts).delete()
    for row in library:
        LibraryItem.objects.filter(pk=row["id"], job=job).update(
            **{key: value for key, value in row.items() if key not in ("id", "job_id")}
        )
    job.side_photos = prefix["job"]["side_photos"]
    job.scenes.update(status=Scene.Status.PLANNED)
    job.status = Job.Status.READY_TO_RENDER
    job.save(update_fields=["status", "side_photos"])
    if first is not None:
        SystemNote.objects.filter(session_id=_session(job), created_at__gte=first).delete()
        Message.objects.filter(
            session_id=_session(job), role=Message.Role.AGENT, created_at__gte=first
        ).delete()


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
    from agents.models import RefusedPicture, ToolCall
    from gateway.models import ModelCall

    RefusedPicture.objects.filter(session_id=_session(job), purpose="scene_agent").delete()
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

    from agents.models import SystemNote, ToolCall
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
    for row in selections:
        if (
            not SceneStep.objects.filter(
                pk=row["step"], scene_id=row["scene_id"], kind="scene_agent"
            ).exists()
            or not AgentFix.objects.filter(pk=row["fixing"], step_id=row["step"]).exists()
            or not SceneAgentRun.objects.filter(
                pk=row["window"]["id"], agent_fix_id=row["fixing"]
            ).exists()
            or not SceneAgentRun.objects.filter(agent_fix_id=row["fixing"], number=1).exists()
            or not job.produced.filter(
                pk=row["picture"], scene_id=row["scene_id"], kind="starting_picture"
            ).exists()
        ):
            raise MissingPrefix("baseline has an incomplete kept-picture execution snapshot")
        if (
            row.get("end_picture")
            and not job.produced.filter(
                pk=row["end_picture"],
                scene_id=row["scene_id"],
                kind="end_picture",
                made_from_id=row["picture"],
            ).exists()
        ):
            raise MissingPrefix("baseline has an incomplete kept end-picture pair")
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
        # started() always ensures window 1 exists. Keep its older receipt so it
        # cannot be recreated as a newer window that hides the selected one.
        SceneAgentRun.objects.filter(agent_fix_id=fix_id).exclude(pk=row["window"]["id"]).exclude(
            number=1
        ).delete()
        SceneAgentRun.objects.filter(pk=row["window"]["id"]).update(
            **window,
            clips=0,
            finished_at=None,
            reason="",
            asked=False,
            clips_at_talking_switch=None,
        )
        fix = {
            key: value
            for key, value in row["fixing_row"].items()
            if key
            not in (
                "id",
                "step_id",
                "passed_id",
                "kept_id",
                "first_clip_id",
                "finished_at",
                "gave_up",
                "ended_by_cap",
                "said",
                "scene_before",
            )
        }
        AgentFix.objects.filter(pk=fix_id).update(**fix, said=[], scene_before=scene)
    job.status = Job.Status.READY_TO_RENDER
    job.save(update_fields=["status"])
    first = min(row["step_row"]["started_at"] for row in selections)
    SystemNote.objects.filter(session_id=_session(job), created_at__gte=first).delete()
    Message.objects.filter(
        session_id=_session(job), role=Message.Role.AGENT, created_at__gte=first
    ).delete()


def before_tool(stage: Stage | None, agent: Agent, call: ToolCall) -> str | None:
    if stage is None:
        return None
    if agent.name == "producer":
        prefix_tools = {"read_page"}
        if stage in ("pictures", "clips"):
            prefix_tools.update(("plan_ad", "create_person", "remake_person", "create_music"))
        if call.tool in prefix_tools:
            return "This run already replayed that stage; continue from its saved outputs."
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
