"""Run fixed saved inputs through the app inside isolated scenario processes."""

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from .budget import PaidCalls, SpendLedger, SpendLimitReached
from .database import dump_database
from .scenarios import Scenario
from .store import Store, atomic_write_text, write_json

if TYPE_CHECKING:
    from agents.loop import Agent
    from agents.models import ToolCall
    from jobs.models import Job


class PlanReached(BaseException):
    """The requested successful terminal stage, not a failure for the app or owner."""


class ScenarioStopped(BaseException):
    """The eval needs an input or reached its explicit terminal boundary."""


@dataclass
class ScenarioResult:
    scenario: str
    status: Literal["done", "stopped", "failed"] = "failed"
    reason: str = ""
    session_id: str | None = None
    job_id: str | None = None
    scenes: int = 0
    cost_usd: float = 0
    execution: str = ""
    model_call_ids: list[int] = field(default_factory=list)


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

    def before_tool(self, agent: Agent, call: ToolCall) -> str | None:
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

    def checkpoint(self, name: str, session_id: str) -> None:
        folder = self.folder / "checkpoints" / name
        _save_media(folder / "media")
        dump_database(self.database_url, folder / "db.dump")
        write_json(folder / "scenario.json", {"session_id": session_id})
        atomic_write_text(folder / "done", "")


def run_scenario(
    store: Store,
    run: str,
    scenario: Scenario,
    ledger: SpendLedger,
    *,
    database_url: str,
    execution: str,
) -> ScenarioResult:
    """Drive one real in-process chat in the caller's already isolated database."""
    from rest_framework.test import APIClient

    from agents import tasks
    from agents.execution_boundary import use_execution_boundary
    from chat.models import Message, Session
    from gateway.call_observer import use_call_observer
    from jobs import notices
    from jobs.models import Job
    from jobs.page_source import use_page_source

    from .answers import answer_job
    from .pages import SavedPageSource

    result = ScenarioResult(scenario.key, execution=execution)
    folder = store.scenario_dir(run, scenario.key)
    folder.mkdir(parents=True, exist_ok=True)
    boundary = PlansBoundary(folder, database_url)
    try:
        source = SavedPageSource(store.page_dir(scenario.key))
        with (
            use_page_source(source),
            use_execution_boundary(boundary),
            use_call_observer(PaidCalls(ledger, scenario.key, execution)),
            held_dispatch() as dispatch,
        ):
            api = APIClient()
            made = api.post("/api/sessions/", {}, format="json")
            if made.status_code != 201:
                raise RuntimeError(f"chat session could not start ({made.status_code})")
            result.session_id = str(made.json()["id"])
            sent = api.post(
                f"/api/sessions/{result.session_id}/messages/",
                {"text": scenario.first_message},
                format="json",
            )
            if sent.status_code != 201:
                raise RuntimeError(f"first chat message was refused ({sent.status_code})")
            while dispatch.producers:
                tasks.run_producer(dispatch.producers.pop(0))
            raise ScenarioStopped("producer stopped before its plan passed the planning checks")
    except PlanReached:
        result.status = "done"
    except (SpendLimitReached, ScenarioStopped) as stopped:
        result.status, result.reason = "stopped", str(stopped)
    except Exception as error:
        result.status, result.reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        if result.session_id is not None:
            Session.objects.filter(pk=result.session_id).update(
                producer_running=False, producer_copy=None, producer_beat_at=None
            )

    job = Job.objects.filter(session_id=result.session_id).first() if result.session_id else None
    result.cost_usd = float(ledger.scenario_cost(scenario.key))
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
            to="plans",
            kind=scenario.kind,
            photo_labels=store.read_photo_labels().get(scenario.key, {}),
            model_call_ids=result.model_call_ids,
            cost_usd=result.cost_usd,
        )
    else:
        answers = {
            "scenario": scenario.key,
            "run": run,
            "answers": {},
            "first_try": {"scenes": 0, "first_passed": 0, "clips": 0},
            "cost_usd": result.cost_usd,
        }
    _save_media(store.media_dir(run, scenario.key))
    dump_database(database_url, store.dump_path(run, scenario.key))
    write_json(folder / "scenario.json", {**asdict(result), "kind": scenario.kind, "notes": []})
    store.write_answers(run, scenario.key, answers)
    write_json(folder / "result.json", asdict(result))
    if result.status == "done":
        store.mark_done(run, scenario.key)
    return result
