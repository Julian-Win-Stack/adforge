"""A paid eval run starts only when every case can run, and hands out no new case once one
stops for a setup reason (a missing file or key, a service refusing us). A service refusing
us (out of money, a wrong key) also stops every running case at its next paid attempt
(budget.RunStopped)."""

import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import psycopg

from adforge.paid_runs import KeyStage

from .budget import SpendLedger
from .database import DatabaseError, url_for_database
from .replay import MissingPrefix
from .store import Store, atomic_write_text


def setup_errors(*more: type[Exception]) -> tuple[type[Exception], ...]:
    """What a case's own setup raises, before or between its paid calls: the files or
    database it needs aren't there. A command adds its own (`more`)."""
    from jobs.page_source import SavedPageMissing

    return SavedPageMissing, MissingPrefix, DatabaseError, psycopg.OperationalError, *more


_KEY_CHECK = """
import json, sys
import django
django.setup()
from adforge.paid_runs import missing_keys
print(json.dumps(missing_keys(sys.argv[1])))
"""


def missing_keys(stage: KeyStage) -> list[str]:
    """`paid_runs.missing_keys`, asked in a short-lived process when this one hasn't loaded
    Django's settings. A run's own process must not load them: its forked cases each set up
    Django with their own database, media folder and quality checks, and settings loaded
    before the fork would keep this process's values."""
    from django.conf import settings

    if settings.configured:
        from adforge.paid_runs import missing_keys as asked_here

        return asked_here(stage)
    environment = {**os.environ}
    environment.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    finished = subprocess.run(
        [sys.executable, "-c", _KEY_CHECK, stage],
        capture_output=True,
        text=True,
        env=environment,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    if finished.returncode != 0:
        last = (finished.stderr.strip().splitlines() or ["no output"])[-1]
        return [f"the key check couldn't run: {last}"]
    return list(json.loads(finished.stdout.splitlines()[-1]))


def database_problem() -> str | None:
    try:
        with psycopg.connect(url_for_database("postgres"), connect_timeout=5):
            return None
    except (ValueError, psycopg.Error) as error:
        return f"the eval database server can't be reached: {type(error).__name__}"


def refuse(problems: Iterable[str]) -> bool:
    """Print one line per problem; True when there was any, so the run starts nothing."""
    found = list(problems)
    for problem in found:
        print(f"Cannot run: {problem}", flush=True)
    return bool(found)


def report_stops(store: Store, run: str, ledger: SpendLedger, seen: set[str]) -> set[str]:
    """Print and append to runs/<run>/STOP each case that newly stopped for a setup reason."""
    for case, reason in ledger.stops().items():
        if case in seen:
            continue
        seen.add(case)
        line = f"STOP: {case}: {reason}"
        print(line, flush=True)
        path = store.run_dir(run) / "STOP"
        before = path.read_text(encoding="utf-8") if path.is_file() else ""
        atomic_write_text(path, before + line + "\n")
    return seen
