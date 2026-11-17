"""A paid eval run starts only when every case can run, and hands out no new case once one
stops for a setup reason (a missing file or key, a service refusing us)."""

from collections.abc import Iterable

import psycopg

from .budget import SpendLedger
from .database import DatabaseError, url_for_database
from .replay import MissingPrefix
from .store import Store, atomic_write_text


def setup_errors(*more: type[Exception]) -> tuple[type[Exception], ...]:
    """What a case's own setup raises, before or between its paid calls: the files or
    database it needs aren't there. A command adds its own (`more`)."""
    from jobs.page_source import SavedPageMissing

    return SavedPageMissing, MissingPrefix, DatabaseError, psycopg.OperationalError, *more


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
