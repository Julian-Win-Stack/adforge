"""Run fixed saved inputs through the app inside isolated scenario processes."""

import argparse
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from .budget import PaidCalls, SpendLedger, SpendLimitReached
from .database import child_environment, dump_database, isolated_database
from .scenarios import Scenario, select_scenarios
from .store import Store, atomic_write_text, read_json, write_json

if TYPE_CHECKING:
    from rest_framework.test import APIClient

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


def _send(api: APIClient, session_id: str, text: str) -> None:
    sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
    if sent.status_code != 201:
        raise RuntimeError(f"chat message was refused ({sent.status_code})")


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
    answer = scenario.saved_answers.get(words) or scenario.saved_answers.get("owner_part")
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
) -> ScenarioResult:
    """Drive one real in-process chat in the caller's already isolated database."""
    from rest_framework.test import APIClient

    from agents import tasks
    from agents.execution_boundary import use_execution_boundary
    from chat.models import Message, Session
    from evals.round_export import export_run
    from gateway.call_observer import use_call_observer
    from jobs import notices
    from jobs.models import Job
    from jobs.page_source import use_page_source

    from .answers import answer_job
    from .grading_records import export_job
    from .pages import SavedPageSource

    if to != "plans":
        raise ValueError("This runner currently ends at plans")
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
            _send(api, result.session_id, scenario.first_message)
            while True:
                while dispatch.producers:
                    tasks.run_producer(dispatch.producers.pop(0))
                if not _answer_waiting(api, result.session_id, scenario):
                    raise ScenarioStopped(
                        "producer stopped before its plan passed the planning checks"
                    )
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
    dump_database(database_url, store.dump_path(run, scenario.key))
    _save_media(store.media_dir(run, scenario.key))
    if job is not None and result.session_id is not None:
        try:
            exported = store.export_dir(run, scenario.key)
            export_run({scenario.key: result.session_id}, exported, run)
            export_job(
                job,
                exported / scenario.key,
                scenario={
                    "key": scenario.key,
                    "kind": scenario.kind,
                    "link": scenario.link,
                    "situation": scenario.situation,
                },
            )
        except Exception as error:
            result.status = "failed"
            failure = f"Could not save export: {type(error).__name__}: {error}"
            result.reason = "; ".join(text for text in (result.reason, failure) if text)
            notices.post_notice(
                job, notices.REGRESSION_SCENARIO_FAILED, result.reason, Message.Level.PROBLEM
            )
            # Keep the developer notice in the durable database as well as result.json.
            dump_database(database_url, store.dump_path(run, scenario.key))
    write_json(folder / "scenario.json", {**asdict(result), "kind": scenario.kind, "notes": []})
    store.write_answers(run, scenario.key, answers)
    write_json(folder / "result.json", asdict(result))
    if result.status == "done":
        store.mark_done(run, scenario.key)
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _app_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def estimate(store: Store, scenarios: Sequence[Scenario], *, to: str = "plans") -> Decimal:
    """Use actual same-stage scenario costs where known, else catalogue × typical calls."""
    from gateway import catalog

    # A typical page has ten photos to label; producer turns include ordinary repairs.
    calls = {
        "produce": (6, 12_000, 1_500),
        "plan_ad": (1, 30_000, 4_000),
        "fact_check": (1, 18_000, 2_000),
        "pick_photos": (1, 15_000, 2_000),
        "pick_side_photos": (1, 15_000, 2_000),
        "check_plan_quality": (1, 10_000, 1_000),
        "label_photo": (10, 5_000, 500),
        "copy_page_text": (1, 30_000, 10_000),
        "check_page": (1, 30_000, 1_000),
    }
    fallback = sum(
        (
            count * catalog.cost_usd(catalog.MODEL_FOR_PURPOSE[purpose], inputs, outputs)
            for purpose, (count, inputs, outputs) in calls.items()
        ),
        Decimal(0),
    )
    known: dict[str, Decimal] = {}
    previous = [read_json(path) for path in (store.root / "runs").glob("*/run.json")]
    for record in sorted(previous, key=lambda row: row.get("started_at", ""), reverse=True):
        if record.get("to") != to or record.get("from") is not None:
            continue
        for scenario in scenarios:
            if scenario.key in known or not store.scenario_done(record["name"], scenario.key):
                continue
            actual = store.read_answers(record["name"], scenario.key).get("cost_usd", 0)
            if actual > 0:
                known[scenario.key] = Decimal(str(actual))
    return sum((known.get(scenario.key, fallback) for scenario in scenarios), Decimal(0))


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
) -> None:
    result = ScenarioResult(scenario.key)
    try:
        with tempfile.TemporaryDirectory(prefix="regression-media-") as temporary:
            media = Path(temporary)
            with isolated_database() as url:
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
                    )
                    connections.close_all()
    except (SpendLimitReached, ScenarioStopped) as stopped:
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
) -> int:
    """Run the selected scenarios in separate fresh databases and save each as it ends."""
    if parallel < 1:
        raise ValueError("--parallel must be at least one")
    if not scenarios:
        raise ValueError("Select at least one scenario")
    record = store.read_run(name)
    keys = [scenario.key for scenario in scenarios]
    if record and (record.get("to") != to or set(keys) - set(record["scenarios"])):
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
        Decimal(str(record["estimate_usd"])) if record else estimate(store, scenarios, to=to)
    )
    print(
        f"Estimate: ${estimate_usd:.2f}; "
        f"stop new paid work at ${estimate_usd * Decimal('1.5'):.2f}.",
        flush=True,
    )
    if not yes and input("Type yes to start: ").strip().lower() != "yes":
        print("Not started.")
        return 1
    if not record:
        record = {
            "name": name,
            "app_commit": _app_commit(),
            "to": to,
            "from": None,
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
        while pending and len(running) < parallel and ledger.can_start():
            scenario = pending.pop(0)
            response = store.run_dir(name) / "attempts" / f"{scenario.key}-{uuid4().hex}.json"
            process: BaseProcess = context.Process(
                target=_scenario_process,
                args=(store.root, name, scenario, estimate_usd, pacing, response, to),
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
                f"${float(result['cost_usd']):.2f}"
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
    print(f"{name}: total ${ledger.spend_usd:.2f}; {count}/{len(scenarios)} done.", flush=True)
    return int(any(not store.scenario_done(name, scenario.key) for scenario in scenarios))


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression run")
    parser.add_argument("--to", choices=["plans"], required=True)
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
        return run(store, select_scenarios(keys), name=name, yes=args.yes, parallel=args.parallel)
    except (ValueError, EOFError) as error:
        print(f"Cannot run: {error}")
        return 1
