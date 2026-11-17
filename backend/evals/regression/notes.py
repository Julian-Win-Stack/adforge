"""Note runs: each owner-note case's frozen ad is copied, sent its note and fixed by the app.

Every case restores its source run's saved scenario into its own fresh database, copies the
chat, sends the note through the real chat and lets the app work with real paid services and
quality checks on. Nothing answers an owner question for the app."""

import argparse
import multiprocessing
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from . import preflight
from .budget import PaidCalls, SpendLedger, SpendLimitReached
from .database import dump_database, isolated_database
from .note_cases import NoteCase, select_note_cases
from .store import Store, read_json, write_json

if TYPE_CHECKING:
    from jobs.models import Job, ProducedItem

    from .runner import Dispatch

# A guess until a note run has a real cost: the Manscaped note copy cost $12.95 when its scene
# was retried five times; most fixes remake one scene once or twice.
DEFAULT_CASE_USD = Decimal("4")


class CaseStopped(Exception):
    """The case can't go on without paid work it may not do, or an answer nobody gives."""


@dataclass
class CaseResult:
    case: str
    status: Literal["done", "stopped", "failed"] = "failed"
    why: str | None = None
    cost_usd: float = 0
    minutes: float | None = None
    copy_session_id: str | None = None
    owner_questions: list[str] | None = None
    video_seconds: float = 0

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "why": self.why,
            "cost_usd": self.cost_usd,
            "minutes": self.minutes,
        }


@dataclass(frozen=True)
class Source:
    run: str
    scenario: str
    folder: Path
    session_id: str


def source_of(store: Store, case: NoteCase) -> Source:
    """The case's saved scenario folder, checked before any database or paid work."""
    folder = store.scenario_dir(case.source_run, case.scenario)
    where = f"source run {case.source_run} has no finished {case.scenario}"
    if not store.scenario_done(case.source_run, case.scenario):
        raise CaseStopped(f"{where}; copy that run folder into this store first")
    session_id = read_json(folder / "scenario.json").get("session_id")
    if not session_id or not (folder / "media").is_dir():
        raise CaseStopped(f"{where} chat with saved media")
    return Source(case.source_run, case.scenario, folder, str(session_id))


def _settle(dispatch: Dispatch) -> None:
    """Run the producer and scene work a worker would get until nothing is left to do."""
    from agents import tasks

    from .runner import run_scene_batch

    while dispatch.producers or dispatch.scenes:
        while dispatch.producers:
            tasks.run_producer(dispatch.producers.pop(0))
        if dispatch.scenes:
            run_scene_batch(dispatch)


def _scene_clips(ad: ProducedItem) -> list[tuple[int, int | None, str]]:
    """(scene number, scene id, clip file) for each cut, in the ad's order."""
    from jobs.models import ProducedItem

    clips = ProducedItem.objects.in_bulk(cut["clip"] for cut in ad.cuts)
    return [
        (int(cut["scene"]), clips[cut["clip"]].scene_id, clips[cut["clip"]].file) for cut in ad.cuts
    ]


def note_export(
    base: Path, case: NoteCase, before: ProducedItem, after: ProducedItem | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The note's whole before/after ads, and every scene of the before ad with its before
    and after clip files and `changed`, all saved under note-1/ in the case's export."""
    from adforge import file_store

    def keep(file: str, relative: str) -> str:
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_store.read(file))
        return relative

    note = {"number": 1, "text": case.note, "before": keep(before.file, "note-1/before.mp4")}
    if after is not None:
        note["after"] = keep(after.file, "note-1/after.mp4")

    new = {scene_id: file for _, scene_id, file in _scene_clips(after)} if after else {}
    scenes = []
    for number, scene_id, file in _scene_clips(before):
        after_file = new.get(scene_id)
        scenes.append(
            {
                "number": number,
                "before_clip": keep(file, f"note-1/before-s{number}.mp4"),
                "after_clip": keep(after_file, f"note-1/after-s{number}.mp4")
                if after_file
                else None,
                "changed": after_file is not None and after_file != file,
            }
        )
    return note, {"id": case.id, "expected_scenes": list(case.scenes), "scenes": scenes}


def clear_attempt(folder: Path) -> None:
    """An earlier attempt's marker and results must never pass for this attempt's."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("done", "result.json", "answers.json"):
        (folder / name).unlink(missing_ok=True)


def run_note_case(
    store: Store,
    run: str,
    case: NoteCase,
    source: Source,
    ledger: SpendLedger,
    *,
    database_url: str,
    execution: str,
) -> CaseResult:
    """Fix one case in the caller's database, already restored from the source run."""
    from rest_framework.test import APIClient

    from chat import session_copy
    from chat.models import Message, Session
    from evals.round_export import export_run
    from gateway.call_observer import use_call_observer
    from jobs import notices
    from jobs.page_source import use_page_source

    from .answers import _answer, not_reached, note_new_ad, note_right_scenes, note_untouched_same
    from .answers import _finished as _latest_ad
    from .grading_records import export_job
    from .pages import SavedPageSource
    from .runner import _owner_questions, _save_media, _send, held_dispatch
    from .scenarios import select_scenarios

    result = CaseResult(case.id)
    folder = store.scenario_dir(run, case.id)
    clear_attempt(folder)
    scenario = select_scenarios([case.scenario])[0]
    entry: dict[str, Any] = {
        **case.entry(),
        "source": {"run": source.run, "scenario": source.scenario, "session_id": source.session_id},
        "copy_session_id": None,
    }
    original = Session.objects.filter(pk=source.session_id).first()
    source_job = original.jobs.first() if original is not None else None
    source_ad = _latest_ad(source_job) if source_job is not None else None
    if original is None or source_job is None or source_ad is None:
        result.status, result.why = "stopped", f"source {source.run} has no finished ad"
    elif why := session_copy.still_working(original):
        result.status, result.why = "stopped", f"source chat is not idle: {why}"
    if result.status == "stopped":
        if source_job is not None:
            notices.post_notice(
                source_job,
                notices.REGRESSION_SCENARIO_FAILED,
                result.why or "",
                Message.Level.PROBLEM,
            )
        write_json(folder / "case.json", entry)
        write_json(folder / "result.json", asdict(result))
        return result
    assert original is not None and source_ad is not None
    entry["source"]["ad"] = source_ad.pk
    copied = session_copy.copy(original)
    result.copy_session_id = entry["copy_session_id"] = str(copied.pk)
    job: Job = copied.jobs.get()
    before = _latest_ad(job)
    assert before is not None
    sent_at: datetime | None = None
    try:
        with (
            use_page_source(SavedPageSource(store.page_dir(case.scenario))),
            use_call_observer(PaidCalls(ledger, case.id, execution)),
            held_dispatch() as dispatch,
        ):
            api = APIClient()
            _send(api, str(copied.pk), case.note)
            sent_at = copied.messages.filter(role=Message.Role.USER).latest("seq").created_at
            _settle(dispatch)
        result.status = "done"
    except SpendLimitReached as stopped:
        result.status, result.why = "stopped", str(stopped)
    except Exception as error:
        result.status, result.why = "failed", f"{type(error).__name__}: {error}"
    finally:
        Session.objects.filter(pk=copied.pk).update(
            producer_running=False, producer_copy=None, producer_beat_at=None
        )

    latest = _latest_ad(job)
    after = latest if latest is not None and latest.version > before.version else None
    if sent_at is not None:
        result.owner_questions = _owner_questions({str(copied.pk): sent_at})
        if result.status == "done" and after is None and result.owner_questions:
            # The app is waiting on the owner. Nothing invents an answer.
            result.status = "stopped"
            result.why = "The app asked the owner: " + " | ".join(result.owner_questions)
    if after is not None and sent_at is not None:
        result.minutes = round((after.created_at - sent_at).total_seconds() / 60, 2)
    # This attempt's calls only: an earlier stopped attempt of the case was paid for then.
    result.cost_usd = float(ledger.scenario_cost(case.id, execution))
    result.video_seconds = ledger.video_seconds(case.id, execution)
    if result.status != "done":
        notices.post_notice(
            job, notices.REGRESSION_SCENARIO_FAILED, result.why or "", Message.Level.PROBLEM
        )

    if sent_at is None:
        no_note = not_reached("The note was never sent.")
        answers = {name: no_note for name in ("note_new_ad", "note_right_scenes")}
    else:
        last = (
            copied.messages.filter(role=Message.Role.AGENT, created_at__gte=sent_at)
            .order_by("seq")
            .last()
        )
        answers = {
            "note_new_ad": _answer(
                job, "note_new_ad", lambda: note_new_ad(before, after, last.text if last else "")
            ),
            "note_right_scenes": _answer(
                job,
                "note_right_scenes",
                lambda: note_right_scenes(case.scenes, before, after, sent_at),
            ),
        }
    answers["note_untouched_same"] = (
        _answer(
            job,
            "note_untouched_same",
            lambda: note_untouched_same(job, before, after, sent_at),
        )
        if after is not None and sent_at is not None
        else not_reached("There is no new ad to compare.")
    )
    dump_database(database_url, store.dump_path(run, case.id))
    _save_media(store.media_dir(run, case.id))
    try:
        exported = store.export_dir(run, case.id)
        if exported.exists():
            shutil.rmtree(exported)
        export_run({case.id: str(copied.pk)}, exported, run)
        base = exported / case.id
        simple = export_job(
            job,
            base,
            scenario={
                "key": case.id,
                "kind": "full",
                "link": scenario.link,
                "situation": scenario.situation,
            },
        )
        note, simple["note_case"] = note_export(base, case, before, after)
        simple["notes"] = [note]
        write_json(base / "regression.json", simple)
    except Exception as error:
        result.status = "failed"
        failure = f"Could not save export: {type(error).__name__}: {error}"
        result.why = "; ".join(text for text in (result.why, failure) if text)
        notices.post_notice(
            job, notices.REGRESSION_SCENARIO_FAILED, result.why, Message.Level.PROBLEM
        )
        dump_database(database_url, store.dump_path(run, case.id))
    write_json(folder / "case.json", entry)
    store.write_answers(
        run,
        case.id,
        {
            "case": case.id,
            "run": run,
            "answers": answers,
            "cost_usd": result.cost_usd,
            "minutes": result.minutes,
        },
    )
    write_json(folder / "result.json", asdict(result))
    if result.status == "done":
        store.mark_done(run, case.id)
    return result


def _case_process(
    store_root: Path,
    run_name: str,
    case: NoteCase,
    estimate_usd: Decimal,
    pacing: bool,
    response: Path,
) -> None:
    from .runner import _initialize_child

    result = CaseResult(case.id)
    store = Store(store_root)
    folder = store.scenario_dir(run_name, case.id)
    try:
        clear_attempt(folder)
        source = source_of(store, case)
        with tempfile.TemporaryDirectory(prefix="note-media-") as temporary:
            media = Path(temporary)
            # Only copies are changed: the source run's folder stays as it was saved.
            shutil.copytree(source.folder / "media", media, dirs_exist_ok=True)
            with isolated_database(store.dump_path(source.run, source.scenario)) as url:
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
                    # The saved source may predate the app's latest migrations.
                    call_command("migrate", verbosity=0)
                    ledger = SpendLedger(
                        store.run_dir(run_name) / "spend.json", estimate_usd, pacing=pacing
                    )
                    result = run_note_case(
                        store,
                        run_name,
                        case,
                        source,
                        ledger,
                        database_url=url,
                        execution=url.rsplit("/", 1)[-1],
                    )
                    connections.close_all()
    except SpendLimitReached as stopped:
        result.status, result.why = "stopped", str(stopped)
    except preflight.setup_errors(CaseStopped) as stopped:
        result.status, result.why = "stopped", str(stopped) or type(stopped).__name__
        SpendLedger(store.run_dir(run_name) / "spend.json", estimate_usd).stop(case.id, result.why)
    except Exception as error:
        result.status, result.why = "failed", f"{type(error).__name__}: {error}"
    if not (folder / "result.json").is_file():
        # The case ended before its database work could save anything: keep why.
        source_block = {"run": case.source_run, "scenario": case.scenario}
        write_json(
            folder / "case.json",
            {**case.entry(), "source": source_block, "copy_session_id": None},
        )
        try:
            from adforge import monitoring
            from jobs import notices

            monitoring.failed(
                notices.REGRESSION_SCENARIO_FAILED.name,
                f"Note case {case.id} could not start: {result.why}",
                run=run_name,
            )
        except Exception as error:
            result.why = f"{result.why} (Sentry notice failed: {type(error).__name__}: {error})"
        write_json(folder / "result.json", asdict(result))
    write_json(response, asdict(result))


def estimate(store: Store, cases: Sequence[NoteCase]) -> Decimal:
    """Each case's most recent actual cost from earlier note runs, else the default guess."""
    known: dict[str, Decimal] = {}
    previous = [read_json(path) for path in (store.root / "runs").glob("*/run.json")]
    for record in sorted(previous, key=lambda row: row.get("started_at", ""), reverse=True):
        if record.get("kind") != "notes":
            continue
        for case in cases:
            if case.id in known or not store.scenario_done(record["name"], case.id):
                continue
            actual = store.read_answers(record["name"], case.id).get("cost_usd", 0)
            if actual > 0:
                known[case.id] = Decimal(str(actual))
    return sum((known.get(case.id, DEFAULT_CASE_USD) for case in cases), Decimal(0))


def preflight_problems(store: Store, cases: Sequence[NoteCase]) -> list[str]:
    """Every reason any case can't run, found before any paid work."""
    from adforge.paid_runs import missing_keys
    from jobs.page_source import SavedPageMissing

    from .pages import SavedPageSource

    problems = missing_keys("full")
    if database := preflight.database_problem():
        problems.append(database)
    for case in cases:
        try:
            source_of(store, case)
            SavedPageSource(store.page_dir(case.scenario))
        except (CaseStopped, SavedPageMissing) as error:
            problems.append(f"{case.id}: {error}")
    return problems


def run_notes(
    store: Store,
    cases: Sequence[NoteCase],
    *,
    name: str,
    yes: bool = False,
    parallel: int = 6,
    pacing: bool = True,
) -> int:
    """Run the selected cases, each in its own process and database; save each as it ends."""
    from .runner import _app_commit, _now

    if parallel < 1:
        raise ValueError("--parallel must be at least one")
    if not cases:
        raise ValueError("Select at least one note case")
    ids = [case.id for case in cases]
    record = store.read_run(name)
    if record and (record.get("kind") != "notes" or set(ids) - set(record.get("cases", []))):
        raise ValueError("A saved run has other cases or is not a note run; choose a new name")
    if record and record.get("app_commit") != _app_commit():
        raise ValueError("This run was started at another app commit; choose a new name")
    for case in cases:
        if store.scenario_done(name, case.id):
            print(f"{case.id}: skipped (already done)", flush=True)
    pending = [case for case in cases if not store.scenario_done(name, case.id)]
    if not pending:
        print(f"{name}: all selected cases already done; no paid work started")
        return 0
    if preflight.refuse(preflight_problems(store, pending)):
        return 1
    estimate_usd = Decimal(str(record["estimate_usd"])) if record else estimate(store, cases)
    print(
        f"Estimate: ${estimate_usd:.2f} for {len(cases)} note cases; "
        f"stop new paid work at ${estimate_usd * Decimal('1.5'):.2f}.",
        flush=True,
    )
    if not yes and input("Type yes to start: ").strip().lower() != "yes":
        print("Not started.")
        return 1
    if not record:
        record = {
            "kind": "notes",
            "name": name,
            "app_commit": _app_commit(),
            "cases": ids,
            "estimate_usd": float(estimate_usd),
            "started_at": _now(),
            "finished_at": None,
            "spend_usd": 0.0,
            "results": {},
        }
    record["finished_at"] = None
    store.write_run(name, record)
    ledger = SpendLedger(store.run_dir(name) / "spend.json", estimate_usd, pacing=pacing)
    ledger.clear_stops()
    stops: set[str] = set()
    from django.apps import apps

    if apps.ready:
        from django.db import connections

        # A forked child must not close the parent's copied live socket.
        connections.close_all()
    context = multiprocessing.get_context("fork")
    running: dict[str, tuple[BaseProcess, Path]] = {}
    results: dict[str, dict[str, Any]] = dict(record.get("results", {}))
    while pending or running:
        preflight.report_stops(store, name, ledger, stops)
        while pending and len(running) < parallel and ledger.can_start() and not stops:
            case = pending.pop(0)
            response = store.run_dir(name) / "attempts" / f"{case.id}-{uuid4().hex}.json"
            process: BaseProcess = context.Process(
                target=_case_process,
                args=(store.root, name, case, estimate_usd, pacing, response),
            )
            process.start()
            running[case.id] = process, response
        if not running:
            break
        for key, (process, response) in list(running.items()):
            if process.is_alive():
                continue
            process.join()
            ended = (
                read_json(response)
                if response.exists()
                else asdict(
                    CaseResult(
                        key, why=f"child exited without saved output (exit {process.exitcode})"
                    )
                )
            )
            results[key] = CaseResult(**ended).summary()
            del running[key]
            record["results"], record["spend_usd"] = results, float(ledger.spend_usd)
            store.write_run(name, record)
            minutes = ended.get("minutes")
            print(
                f"{key}: {ended['status']}; ${float(ended['cost_usd']):.2f}"
                + (f"; {minutes:g} minutes" if minutes is not None else "")
                + (f"; {ended['why']}" if ended.get("why") else ""),
                flush=True,
            )
        if running:
            time.sleep(0.05)
    preflight.report_stops(store, name, ledger, stops)
    if pending:
        record["stopped_why"] = (
            "a case stopped for a setup reason (see STOP); no new case started"
            if stops
            else "spend reached 1.5 times the estimate; no new case started"
        )
        print(record["stopped_why"], flush=True)
    record.update(finished_at=_now(), spend_usd=float(ledger.spend_usd), results=results)
    store.write_run(name, record)
    count = sum(store.scenario_done(name, case.id) for case in cases)
    print(
        f"{name}: total ${ledger.spend_usd:.2f}; {ledger.video_seconds():.1f} Creatify seconds; "
        f"{count}/{len(cases)} done.",
        flush=True,
    )
    return int(count < len(cases))


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression notes")
    parser.add_argument("name", help="The note run's name (same name again carries on)")
    parser.add_argument("--only", nargs="+", metavar="CASE")
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    ids = args.only
    if ids is None and (record := store.read_run(args.name)):
        ids = record.get("cases")
    try:
        return run_notes(
            store, select_note_cases(ids), name=args.name, yes=args.yes, parallel=args.parallel
        )
    except (ValueError, EOFError) as error:
        print(f"Cannot run: {error}")
        return 1
