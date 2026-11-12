"""Run fixed saved inputs through the app inside isolated scenario processes."""

import argparse
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from .budget import PaidCalls, SpendLedger, SpendLimitReached
from .database import child_environment, dump_database, isolated_database
from .estimates import estimate as estimate
from .estimates import estimated_video_seconds as estimated_video_seconds
from .replay import MissingPrefix
from .scenarios import Scenario, select_scenarios
from .store import Store, atomic_write_text, read_json, write_json

if TYPE_CHECKING:
    from django.db.models import Model
    from rest_framework.test import APIClient

    from agents.loop import Agent
    from agents.models import ToolCall
    from jobs.models import AgentFix, Job, ProducedItem

    from .replay import ReplaySource, Stage


class PlanReached(BaseException):
    """The requested successful terminal stage, not a failure for the app or owner."""


class ScenarioStopped(BaseException):
    """The eval needs an input or reached its explicit terminal boundary."""


class PictureReached(BaseException):
    """One scene reached its real, checked pre-film boundary."""


@dataclass
class ScenarioResult:
    scenario: str
    status: Literal["done", "stopped", "failed"] = "failed"
    reason: str = ""
    session_id: str | None = None
    job_id: str | None = None
    scenes: int = 0
    cost_usd: float = 0
    video_seconds: float = 0
    copy_session_id: str | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)
    replayed_from: dict[str, str] | None = None
    execution: str = ""
    model_call_ids: list[int] = field(default_factory=list)
    pid: int = field(default_factory=os.getpid)


@dataclass
class Dispatch:
    producers: list[str] = field(default_factory=list)
    scenes: list[int] = field(default_factory=list)


@contextmanager
def held_dispatch() -> Iterator[Dispatch]:
    """Queue the same tasks a worker would receive, without starting another worker."""
    from agents import tasks

    dispatch = Dispatch()
    producer_delay, scene_delay = tasks.run_producer.delay, tasks.run_scene_step.delay
    tasks.run_producer.delay = dispatch.producers.append  # type: ignore[method-assign,assignment]
    tasks.run_scene_step.delay = dispatch.scenes.append  # type: ignore[method-assign,assignment]
    try:
        yield dispatch
    finally:
        tasks.run_producer.delay = producer_delay  # type: ignore[method-assign]
        tasks.run_scene_step.delay = scene_delay  # type: ignore[method-assign]


def _save_media(into: Path) -> None:
    from django.conf import settings

    source = Path(settings.MEDIA_ROOT)
    into.mkdir(parents=True, exist_ok=True)
    if source.exists() and source.resolve() != into.resolve():
        shutil.copytree(source, into, dirs_exist_ok=True)


def _fields(row: Model) -> dict[str, Any]:
    return {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}


def _json_fields(value: dict[str, Any]) -> dict[str, Any]:
    import json

    from django.core.serializers.json import DjangoJSONEncoder

    ready: dict[str, Any] = json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    return ready


class PlansBoundary:
    """Run the app's real script checks before rendering at a plans-only boundary."""

    RENDER_TOOLS = {
        "create_person",
        "remake_person",
        "create_music",
        "set_place",
        "make_picture",
        "start_scene_agent",
        "correct_owner_part",
        "approve_owner_part",
        "assemble_ad",
    }
    PLAN_EDITS = {"plan_ad", "edit_scene", "add_scene", "remove_scene", "reorder_scenes"}

    def __init__(self, folder: Path, database_url: str) -> None:
        self.folder, self.database_url = folder, database_url
        self.checking = False
        self.replay_stage: Stage | None = None

    def before_tool(self, agent: Agent, call: ToolCall) -> str | None:
        from .replay import before_tool

        if reason := before_tool(self.replay_stage, agent, call):
            return reason
        if agent.name == "producer" and call.tool in self.RENDER_TOOLS:
            return (
                "This eval ends at the checked plan. Finish its planning checks before rendering."
            )
        return None

    def after_tool(self, agent: Agent, call: ToolCall) -> None:
        from agents import loop
        from agents.models import ToolCall
        from agents.producer import PRODUCER
        from jobs.work import why_the_checks_havent_passed

        if agent.name != "producer":
            return
        job = call.session.jobs.first()
        if call.tool == "read_page":
            if job is None or not job.page_html_key:
                raise ScenarioStopped(
                    "saved page read failed; see the scenario's developer notices"
                )
            self.checkpoint("page", str(call.session_id))
        if job is None or not job.scenes.exists():
            return
        if call.tool == "run_planning_checks" and why_the_checks_havent_passed(job) is None:
            raise PlanReached()
        if self.checking or call.tool not in self.PLAN_EDITS:
            return
        # A normal producer creates the presenter before checking. This terminal scope
        # instead settles the very same real check tool now, keeping its ordinary repair
        # instructions in the conversation. No result or paid provider is fabricated.
        self.checking = True
        try:
            checked = ToolCall.objects.create(
                session=call.session,
                job=job,
                agent="producer",
                tool="run_planning_checks",
                call_id=f"eval-check-{uuid4().hex}",
                arguments={},
                why="Code checks the plan at the eval's requested terminal stage.",
            )
            loop.settle(PRODUCER, checked, counted=False)
        finally:
            self.checking = False

    def defer_owner_parts(self, job: Job) -> bool:
        return True

    def before_film(
        self,
        call: ToolCall,
        fixing: AgentFix,
        picture: ProducedItem,
        end_picture: ProducedItem | None,
    ) -> None:
        raise ScenarioStopped("A plans-only run cannot start a scene's filming work")

    def checkpoint(
        self, name: str, session_id: str, *, prefix_state: dict[str, Any] | None = None
    ) -> None:
        folder = self.folder / "checkpoints" / name
        (folder / "done").unlink(missing_ok=True)
        _save_media(folder / "media")
        dump_database(self.database_url, folder / "db.dump")
        write_json(folder / "scenario.json", {"session_id": session_id})
        if prefix_state is not None:
            write_json(folder / "prefix-state.json", prefix_state)
        atomic_write_text(folder / "done", "")


class RenderBoundary(PlansBoundary):
    """Observe real rendering, optionally ending after each checked starting picture."""

    def __init__(self, folder: Path, database_url: str, *, pictures_only: bool) -> None:
        super().__init__(folder, database_url)
        self.pictures_only = pictures_only
        self.selections: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.page_saved = False
        self.plan_saved = False

    def before_tool(self, agent: Agent, call: ToolCall) -> str | None:
        from .replay import before_tool

        return before_tool(self.replay_stage, agent, call)

    def after_tool(self, agent: Agent, call: ToolCall) -> None:
        if agent.name == "producer" and call.tool == "read_page" and not self.page_saved:
            job = call.session.jobs.first()
            if job is None or not job.page_html_key:
                raise ScenarioStopped(
                    "saved page read failed; see the scenario's developer notices"
                )
            self.checkpoint("page", str(call.session_id))
            self.page_saved = True

    def defer_owner_parts(self, job: Job) -> bool:
        return False

    def before_film(
        self,
        call: ToolCall,
        fixing: AgentFix,
        picture: ProducedItem,
        end_picture: ProducedItem | None,
    ) -> None:
        from jobs.models import Scene

        scene = Scene.objects.get(pk=fixing.step.scene_id)
        selection = {
            "job_id": str(scene.job_id),
            "scene": scene.number,
            "scene_id": str(scene.pk),
            "step": fixing.step_id,
            "fixing": fixing.pk,
            "call": str(call.pk),
            "picture": picture.pk,
            "end_picture": end_picture.pk if end_picture is not None else None,
            "arguments": call.arguments,
            "scene_row": _fields(scene),
            "at": _now(),
        }
        from agents.scene_agent import current_run

        window = current_run(fixing)
        selection["step_row"] = _fields(fixing.step)
        selection["window"] = _fields(window)
        selection["fixing_row"] = _fields(fixing)
        with self.lock:
            self.selections.append(_json_fields(selection))
            write_json(self.folder / "film-selections.json", self.selections)
        if self.pictures_only:
            raise PictureReached()

    def prepare(self, step_ids: Sequence[int], session_id: str) -> None:
        """Keep real shared setup before any scene agent starts changing its scene."""
        from gateway.gateway import charged_to
        from jobs import work
        from jobs.models import Job, SceneStep

        if self.plan_saved:
            return
        for step in SceneStep.objects.filter(pk__in=step_ids, kind=SceneStep.Kind.SCENE_AGENT):
            with charged_to(step.tool_call):
                work.person_and_room(
                    step, work.person_shown_for(step.scene.kind, step.scene.person_shown)
                )
        job = Job.objects.get(session_id=session_id)
        self.checkpoint(
            "plan",
            session_id,
            prefix_state=_json_fields(
                {
                    "job": _fields(job),
                    "library": list(job.library.order_by("number").values()),
                    "produced_ids": list(job.produced.values_list("pk", flat=True)),
                }
            ),
        )
        self.plan_saved = True

    def save_kept(self, job: Job) -> None:
        from jobs.models import AgentFixTry, ProducedItem

        ad = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version").first()
        own = [row for row in self.selections if row["job_id"] == str(job.pk)]
        kept = []
        if ad is None:
            for scene in job.scenes.order_by("number"):
                selected = next(
                    (row for row in reversed(own) if row["scene_id"] == str(scene.pk)), None
                )
                if selected is not None:
                    kept.append(selected)
        else:
            for cut in ad.cuts:
                clip = ProducedItem.objects.get(pk=cut["clip"])
                filmed = AgentFixTry.objects.filter(clip=clip).first()
                selected = next(
                    (
                        row
                        for row in reversed(own)
                        if filmed is not None
                        and row["call"] == str(filmed.tool_call_id)
                        and row["picture"] == filmed.picture_id
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError(f"No saved pre-film selection for kept clip {clip.pk}")
                kept.append({**selected, "clip": clip.pk, "ad": ad.pk})
        write_json(self.folder / "kept-pictures.json", kept)


def run_scene_batch(dispatch: Dispatch) -> None:
    """A worker's independently connected scene tasks, concurrently in this child."""
    from django.db import close_old_connections

    from agents import tasks

    steps = list(dict.fromkeys(dispatch.scenes))
    dispatch.scenes.clear()
    if not steps:
        return

    def execute(step_id: int) -> None:
        close_old_connections()
        try:
            tasks.run_scene_step(step_id)
        except PictureReached:
            pass
        finally:
            close_old_connections()

    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=len(steps)) as pool:
        futures = [pool.submit(copy_context().run, execute, step) for step in steps]
        for future in as_completed(futures):
            try:
                future.result()
            except BaseException as error:
                errors.append(error)
    if errors:
        raise errors[0]


def _drive(
    api: APIClient,
    session_id: str,
    scenario: Scenario,
    dispatch: Dispatch,
    boundary: PlansBoundary,
    *,
    to: Literal["plans", "pictures", "full"],
) -> None:
    from agents import tasks
    from jobs.models import Job

    while True:
        while dispatch.producers:
            tasks.run_producer(dispatch.producers.pop(0))
        if _answer_waiting(api, session_id, scenario):
            continue
        if dispatch.scenes:
            if isinstance(boundary, RenderBoundary):
                boundary.prepare(dispatch.scenes, session_id)
            run_scene_batch(dispatch)
            if isinstance(boundary, RenderBoundary) and boundary.pictures_only:
                job = Job.objects.get(session_id=session_id)
                reached = {selection["scene_id"] for selection in boundary.selections}
                missing = [
                    scene.number for scene in job.scenes.all() if str(scene.pk) not in reached
                ]
                if missing:
                    raise ScenarioStopped(
                        "Scenes did not reach a checked starting picture: "
                        + ", ".join(map(str, missing))
                    )
                return
            continue
        if (
            to == "full"
            and Job.objects.filter(session_id=session_id, produced__kind="finished_ad").exists()
        ):
            return
        raise ScenarioStopped("producer stopped before the requested stage was finished")


def _run_owner_notes(
    api: APIClient,
    result: ScenarioResult,
    scenario: Scenario,
    dispatch: Dispatch,
    boundary: PlansBoundary,
) -> None:
    from chat import session_copy
    from chat.models import Message, Session
    from jobs.models import ProducedItem

    assert result.session_id is not None
    original = Session.objects.get(pk=result.session_id)
    if why := session_copy.still_working(original):
        raise ScenarioStopped(why)
    copied = session_copy.copy(original)
    result.copy_session_id = str(copied.pk)
    job = copied.jobs.get()
    for number, text in enumerate(scenario.owner_notes, 1):
        copied.refresh_from_db()
        if why := session_copy.still_working(copied):
            raise ScenarioStopped(f"Owner note {number} cannot start: {why}")
        before = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).latest("version")
        _send(api, str(copied.pk), text)
        sent = copied.messages.filter(role=Message.Role.USER).latest("seq")
        _drive(api, str(copied.pk), scenario, dispatch, boundary, to="full")
        after = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).latest("version")
        result.notes.append(
            {
                "number": number,
                "text": text,
                "before_ad": before.pk,
                "after_ad": after.pk,
                "sent_at": sent.created_at.isoformat(),
            }
        )


def _send(api: APIClient, session_id: str, text: str) -> None:
    sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
    if sent.status_code != 201:
        raise RuntimeError(f"chat message was refused ({sent.status_code})")


def _asks_for_pending_part_approval(question: ToolCall) -> bool:
    """Recognize explicit approval of a currently shown part, never a generic question.

    AskOwner has no typed subject; its photos/videos refer to owner attachments. Keep
    this shorthand conservative. Other wording can always use an exact saved question.
    """
    from jobs import owner_parts
    from jobs.models import Scene

    if question.arguments.get("photos") or question.arguments.get("videos"):
        return False
    words = " ".join(str(question.arguments.get("question", "")).casefold().split())
    for scene in Scene.objects.filter(
        job__session_id=question.session_id, owner_part_drawn__isnull=False
    ):
        part = owner_parts.owner_part(scene)
        if (
            part is None
            or not owner_parts.picture_is_current(scene)
            or owner_parts.is_approved(scene)
        ):
            continue
        assert scene.owner_part_drawn is not None
        shown_at = datetime.fromisoformat(str(scene.owner_part_drawn["shown_at"]))
        if question.finished_at is None or question.finished_at < shown_at:
            continue
        noun = re.escape(re.sub(r"^(?:the|a|an)\s+", "", part.what.casefold()))
        named = rf"(?:(?:this|that|the) )?{noun}"
        pictured = rf"(?:{named}|(?:the )?(?:picture|drawing) of {named})"
        forms = (
            rf"(?:may|can|shall|should) (?:i|we) (?:use|keep|film) {pictured}"
            rf"(?: shown)?(?: for scene \d+)?",
            rf"is this how {named} looks",
            rf"does {pictured} look (?:right|correct|okay|ok)",
            rf"is {pictured}(?: shown)? (?:right|correct|okay|ok)",
        )
        if any(re.fullmatch(form + r"\?", words) for form in forms):
            return True
    return False


def _answer_waiting(api: APIClient, session_id: str, scenario: Scenario) -> bool:
    """Take only approved app defaults, otherwise the scenario's exact saved answer."""
    from django.utils import timezone

    from agents import loop
    from agents.models import PlannerQuestion, ToolCall
    from agents.planning_questions import expire_waiting_questions
    from agents.producer import PRODUCER
    from chat.models import Message
    from jobs.planning_questions import default_for

    waiting = (
        PlannerQuestion.objects.filter(
            plan_call__session_id=session_id, answered_at__isnull=True, timed_out_at__isnull=True
        )
        .select_related("plan_call__session", "shown_message")
        .order_by("plan_call__created_at")
        .first()
    )
    if waiting is not None:
        required = [item for item in waiting.items if default_for(item["kind"]) is None]
        if not waiting.items:
            required = [{"kind": "question", "question": waiting.question}]
        if required:
            answer = scenario.saved_answers.get(waiting.question)
            if answer is None:
                answer = next(
                    (
                        scenario.saved_answers[item["kind"]]
                        for item in required
                        if item["kind"] in scenario.saved_answers
                    ),
                    None,
                )
            if answer is None:
                questions = "; ".join(f"{item['kind']}: {item['question']}" for item in required)
                raise ScenarioStopped(f"No saved answer for planner question: {questions}")
            _send(api, session_id, answer)
            return True
        if waiting.shown_message_id is None:
            # Use the real showing path too: timeouts must resolve an actually shown round.
            shown = ToolCall.objects.create(
                session=waiting.plan_call.session,
                agent="producer",
                tool="ask_owner",
                call_id=f"eval-question-{uuid4().hex}",
                arguments={"question": waiting.question, "photos": []},
                why="Code shows the planner's question before taking its approved defaults.",
            )
            loop.settle(PRODUCER, shown, counted=False)
        PlannerQuestion.objects.filter(pk=waiting.pk).update(deadline_at=timezone.now())
        expire_waiting_questions()
        return True

    last_user = Message.objects.filter(session_id=session_id, role=Message.Role.USER).last()
    question = ToolCall.objects.filter(
        session_id=session_id,
        tool="ask_owner",
        finished_at__isnull=False,
        created_at__gt=last_user.created_at if last_user else datetime.min.replace(tzinfo=UTC),
    ).last()
    if question is None:
        return False
    words = str(question.arguments.get("question", ""))
    answer = scenario.saved_answers.get(words)
    if answer is None and _asks_for_pending_part_approval(question):
        answer = scenario.saved_answers.get("owner_part")
    if answer is None:
        raise ScenarioStopped(f"No saved answer for owner question: {words}")
    _send(api, session_id, answer)
    return True


def run_scenario(
    store: Store,
    run: str,
    scenario: Scenario,
    ledger: SpendLedger,
    *,
    database_url: str,
    execution: str,
    to: Literal["plans", "pictures", "full"] = "plans",
    replay: ReplaySource | None = None,
) -> ScenarioResult:
    """Drive one real in-process chat in the caller's already isolated database."""
    from rest_framework.test import APIClient

    from agents.execution_boundary import use_execution_boundary
    from chat.models import Message, Session
    from evals.round_export import export_run
    from gateway.call_observer import use_call_observer
    from jobs import notices
    from jobs.models import Job, ProducedItem
    from jobs.page_source import use_page_source

    from .answers import NoteComparison, answer_job
    from .grading_records import export_job
    from .pages import SavedPageSource

    result = ScenarioResult(
        scenario.key, execution=execution, replayed_from=replay.provenance if replay else None
    )
    folder = store.scenario_dir(run, scenario.key)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "done").unlink(missing_ok=True)
    boundary = (
        PlansBoundary(folder, database_url)
        if to == "plans"
        else RenderBoundary(
            folder, database_url, pictures_only=to == "pictures" or scenario.kind == "picture"
        )
    )
    if replay is not None:
        boundary.replay_stage = replay.stage
        if isinstance(boundary, RenderBoundary):
            boundary.page_saved = True
            boundary.plan_saved = replay.stage != "plan"
            if replay.stage == "notes":
                boundary.selections = read_json(replay.folder / "film-selections.json", [])
    try:
        from .replay import ReplayedPageSource

        source = ReplayedPageSource() if replay else SavedPageSource(store.page_dir(scenario.key))
        with (
            use_page_source(source),
            use_execution_boundary(boundary),
            use_call_observer(PaidCalls(ledger, scenario.key, execution)),
            held_dispatch() as dispatch,
        ):
            api = APIClient()
            if replay is None:
                made = api.post("/api/sessions/", {}, format="json")
                if made.status_code != 201:
                    raise RuntimeError(f"chat session could not start ({made.status_code})")
                result.session_id = str(made.json()["id"])
                _send(api, result.session_id, scenario.first_message)
            else:
                from agents import tasks

                from .replay import prepare

                replayed_job = prepare(
                    replay, folder, needs_ad=to == "full" and scenario.kind == "full"
                )
                result.session_id = replay.session_id
                if replay.stage == "clips":
                    dispatch.scenes.extend(
                        replayed_job.scenes.values_list("steps__pk", flat=True).filter(
                            steps__kind="scene_agent", steps__status="running"
                        )
                    )
                elif replay.stage != "notes":
                    tasks.wake_producer(result.session_id)
            if replay is None or replay.stage != "notes":
                _drive(api, result.session_id, scenario, dispatch, boundary, to=to)
            if to == "full" and scenario.kind == "full" and scenario.owner_notes:
                _run_owner_notes(api, result, scenario, dispatch, boundary)
            result.status = "done"
    except PlanReached:
        result.status = "done"
    except (SpendLimitReached, ScenarioStopped, MissingPrefix) as stopped:
        result.status, result.reason = "stopped", str(stopped)
    except Exception as error:
        result.status, result.reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        sessions = [
            pk
            for pk in (
                result.session_id if replay is None or replay.stage != "notes" else None,
                result.copy_session_id,
            )
            if pk is not None
        ]
        if sessions:
            Session.objects.filter(pk__in=sessions).update(
                producer_running=False, producer_copy=None, producer_beat_at=None
            )

    job = Job.objects.filter(session_id=result.session_id).first() if result.session_id else None
    result.cost_usd = float(ledger.scenario_cost(scenario.key))
    result.video_seconds = ledger.video_seconds(scenario.key)
    result.model_call_ids = ledger.call_ids(execution)
    if job is not None:
        result.job_id, result.scenes = str(job.pk), job.scenes.count()
        if result.status != "done":
            notices.post_notice(
                job, notices.REGRESSION_SCENARIO_FAILED, result.reason, Message.Level.PROBLEM
            )
        answers = answer_job(
            job,
            scenario=scenario.key,
            run=run,
            to=to,
            kind=scenario.kind,
            photo_labels=store.read_photo_labels().get(scenario.key, {}),
            model_call_ids=result.model_call_ids,
            cost_usd=result.cost_usd,
            notes=[
                NoteComparison(
                    ProducedItem.objects.get(pk=note["before_ad"]),
                    ProducedItem.objects.get(pk=note["after_ad"]),
                    datetime.fromisoformat(note["sent_at"]),
                )
                for note in result.notes
            ],
        )
    else:
        answers = {
            "scenario": scenario.key,
            "run": run,
            "answers": {},
            "first_try": {"scenes": 0, "first_passed": 0, "clips": 0},
            "cost_usd": result.cost_usd,
        }
    dump_database(database_url, store.dump_path(run, scenario.key))
    _save_media(store.media_dir(run, scenario.key))
    if job is not None and result.session_id is not None:
        try:
            exported = store.export_dir(run, scenario.key)
            identity = {
                "key": scenario.key,
                "kind": scenario.kind,
                "link": scenario.link,
                "situation": scenario.situation,
            }
            note_files = [
                {
                    "number": note["number"],
                    "text": note["text"],
                    "before": ProducedItem.objects.get(pk=note["before_ad"]).file,
                    "after": ProducedItem.objects.get(pk=note["after_ad"]).file,
                }
                for note in result.notes
            ]
            if isinstance(boundary, RenderBoundary):
                boundary.save_kept(job)
            export_run({scenario.key: result.session_id}, exported, run)
            simple = export_job(
                job,
                exported / scenario.key,
                scenario=identity,
                notes=note_files,
            )
            if result.copy_session_id is not None:
                copied_job = Job.objects.get(session_id=result.copy_session_id)
                note_export = exported / scenario.key / "owner-notes"
                export_run(
                    {scenario.key: result.copy_session_id}, note_export, f"{run}:owner-notes"
                )
                export_job(
                    copied_job, note_export / scenario.key, scenario=identity, notes=note_files
                )
                simple["owner_notes_export"] = "owner-notes"
                write_json(exported / scenario.key / "regression.json", simple)
        except Exception as error:
            result.status = "failed"
            failure = f"Could not save export: {type(error).__name__}: {error}"
            result.reason = "; ".join(text for text in (result.reason, failure) if text)
            notices.post_notice(
                job, notices.REGRESSION_SCENARIO_FAILED, result.reason, Message.Level.PROBLEM
            )
            # Keep the developer notice in the durable database as well as result.json.
            dump_database(database_url, store.dump_path(run, scenario.key))
    write_json(folder / "scenario.json", {**asdict(result), "kind": scenario.kind})
    store.write_answers(run, scenario.key, answers)
    write_json(folder / "result.json", asdict(result))
    if result.status == "done":
        store.mark_done(run, scenario.key)
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _app_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _initialize_child(database_url: str, media: Path) -> None:
    """Initialize only this child, including the test case where Django was forked ready."""
    import django
    from django.apps import apps

    was_ready = apps.ready
    os.environ.update(child_environment(database_url, media))
    django.setup()
    if was_ready:
        import dj_database_url
        from django.conf import settings
        from django.db import connections

        connections.close_all()
        config = dj_database_url.parse(database_url)
        settings.DATABASES["default"] = config
        connections.settings["default"] = config
        connections.configure_settings(connections.settings)
        try:
            del connections["default"]
        except AttributeError:
            pass


def _scenario_process(
    store_root: Path,
    run_name: str,
    scenario: Scenario,
    estimate_usd: Decimal,
    pacing: bool,
    response: Path,
    to: Literal["plans", "pictures", "full"],
    from_stage: Stage | None = None,
    baseline: str | None = None,
) -> None:
    result = ScenarioResult(scenario.key)
    try:
        from .replay import source_for

        replay = (
            source_for(Store(store_root), scenario, from_stage, baseline=baseline)
            if from_stage
            else None
        )
        with tempfile.TemporaryDirectory(prefix="regression-media-") as temporary:
            media = Path(temporary)
            if replay is not None:
                shutil.copytree(replay.media, media, dirs_exist_ok=True)
            with isolated_database(replay.dump if replay else None) as url:
                _initialize_child(url, media)
                from django.core.management import call_command
                from django.db import connections
                from django.test import override_settings

                from adforge import celery_app

                celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
                with override_settings(
                    MEDIA_ROOT=media,
                    QUALITY_CHECKS=True,
                    ONE_AD_AT_A_TIME=False,
                    ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
                ):
                    call_command("migrate", verbosity=0)
                    store = Store(store_root)
                    ledger = SpendLedger(
                        store.run_dir(run_name) / "spend.json", estimate_usd, pacing=pacing
                    )
                    result = run_scenario(
                        store,
                        run_name,
                        scenario,
                        ledger,
                        database_url=url,
                        execution=url.rsplit("/", 1)[-1],
                        to=to,
                        replay=replay,
                    )
                    connections.close_all()
    except (SpendLimitReached, ScenarioStopped, MissingPrefix) as stopped:
        result.status, result.reason = "stopped", str(stopped)
    except Exception as error:
        result.status, result.reason = "failed", f"{type(error).__name__}: {error}"
    write_json(response, asdict(result))


def run(
    store: Store,
    scenarios: Sequence[Scenario],
    *,
    name: str,
    yes: bool = False,
    parallel: int = 6,
    pacing: bool = True,
    to: Literal["plans", "pictures", "full"] = "plans",
    from_stage: Stage | None = None,
) -> int:
    """Run the selected scenarios in separate fresh databases and save each as it ends."""
    if parallel < 1:
        raise ValueError("--parallel must be at least one")
    if not scenarios:
        raise ValueError("Select at least one scenario")
    if from_stage in ("pictures", "clips", "notes") and to == "plans":
        raise ValueError("--from must precede the --to stage")
    if from_stage in ("clips", "notes") and to != "full":
        raise ValueError(f"--from {from_stage} requires --to full")
    if from_stage == "notes" and any(scenario.kind != "full" for scenario in scenarios):
        raise ValueError("--from notes requires full-ad scenarios; select them with --only")
    record = store.read_run(name)
    keys = [scenario.key for scenario in scenarios]
    if record and (
        record.get("to") != to
        or record.get("from") != from_stage
        or set(keys) - set(record["scenarios"])
    ):
        raise ValueError("A saved run has different inputs or stage; choose a new --name")
    if record and record.get("app_commit") != _app_commit():
        raise ValueError("This run was started at another app commit; choose a new --name")
    pending = [scenario for scenario in scenarios if not store.scenario_done(name, scenario.key)]
    for scenario in scenarios:
        if store.scenario_done(name, scenario.key):
            print(f"{scenario.key}: skipped (already done)", flush=True)
    if not pending:
        print(f"{name}: all selected scenarios already done; no paid work started")
        return 0
    estimate_usd = (
        Decimal(str(record["estimate_usd"]))
        if record
        else estimate(store, scenarios, to=to, from_stage=from_stage)
    )
    print(
        f"Estimate: ${estimate_usd:.2f}; "
        f"stop new paid work at ${estimate_usd * Decimal('1.5'):.2f}.",
        flush=True,
    )
    if to == "full":
        seconds = estimated_video_seconds(store, scenarios, to=to, from_stage=from_stage)
        print(f"Estimated Creatify: {seconds:g} seconds ({seconds * 0.4:g} credits).", flush=True)
    if not yes and input("Type yes to start: ").strip().lower() != "yes":
        print("Not started.")
        return 1
    if not record:
        record = {
            "name": name,
            "app_commit": _app_commit(),
            "to": to,
            "from": from_stage,
            "baselines": {key: store.read_baselines().get(key) for key in keys}
            if from_stage
            else {},
            "started_at": _now(),
            "finished_at": None,
            "estimate_usd": float(estimate_usd),
            "spend_usd": 0.0,
            "scenarios": keys,
        }
    record["finished_at"] = None
    store.write_run(name, record)
    ledger = SpendLedger(store.run_dir(name) / "spend.json", estimate_usd, pacing=pacing)
    # Close idle parent connections before fork: closing a copied live socket in the child
    # would otherwise terminate the parent's connection too. Settings are never repointed.
    from django.apps import apps

    if apps.ready:
        from django.db import connections

        connections.close_all()
    context = multiprocessing.get_context("fork")
    running: dict[str, tuple[BaseProcess, Scenario, Path]] = {}
    results: dict[str, dict[str, object]] = dict(record.get("results", {}))
    while pending or running:
        while pending and len(running) < parallel and (ledger.can_start() or estimate_usd == 0):
            scenario = pending.pop(0)
            response = store.run_dir(name) / "attempts" / f"{scenario.key}-{uuid4().hex}.json"
            process: BaseProcess = context.Process(
                target=_scenario_process,
                args=(
                    store.root,
                    name,
                    scenario,
                    estimate_usd,
                    pacing,
                    response,
                    to,
                    from_stage,
                    record.get("baselines", {}).get(scenario.key),
                ),
            )
            process.start()
            running[scenario.key] = process, scenario, response
        if not running:
            break
        for key, (process, _scenario, response) in list(running.items()):
            if process.is_alive():
                continue
            process.join()
            result = (
                read_json(response)
                if response.exists()
                else asdict(
                    ScenarioResult(
                        key, reason=f"child exited without saved output (exit {process.exitcode})"
                    )
                )
            )
            results[key] = result
            del running[key]
            record["results"], record["spend_usd"] = results, float(ledger.spend_usd)
            store.write_run(name, record)
            print(
                f"{key}: {result['status']}; {result['scenes']} plan scenes; "
                f"${float(result['cost_usd']):.2f}; "
                f"{float(result.get('video_seconds', 0)):.1f} Creatify seconds"
                + (f"; {result['reason']}" if result["reason"] else ""),
                flush=True,
            )
        if running:
            time.sleep(0.05)
    if pending:
        record["stopped_why"] = "spend reached 1.5 times the estimate; no new scenario started"
        print(record["stopped_why"], flush=True)
    record.update(finished_at=_now(), spend_usd=float(ledger.spend_usd), results=results)
    store.write_run(name, record)
    count = sum(store.scenario_done(name, scenario.key) for scenario in scenarios)
    print(
        f"{name}: total ${ledger.spend_usd:.2f}; "
        f"{ledger.video_seconds():.1f} Creatify seconds; {count}/{len(scenarios)} done.",
        flush=True,
    )
    return int(any(not store.scenario_done(name, scenario.key) for scenario in scenarios))


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression run")
    parser.add_argument("--to", choices=["plans", "pictures", "full"], required=True)
    parser.add_argument("--from", dest="from_stage", choices=["plan", "pictures", "clips", "notes"])
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--name", default=None)
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    name = args.name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    keys = args.only
    if keys is None and (record := store.read_run(name)):
        keys = record["scenarios"]
    try:
        scenarios = select_scenarios(keys)
        if args.from_stage == "notes" and keys is None:
            scenarios = [scenario for scenario in scenarios if scenario.kind == "full"]
        return run(
            store,
            scenarios,
            name=name,
            yes=args.yes,
            parallel=args.parallel,
            to=args.to,
            from_stage=args.from_stage,
        )
    except (ValueError, EOFError) as error:
        print(f"Cannot run: {error}")
        return 1
