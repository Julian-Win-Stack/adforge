"""Runs the scripted owner sessions: prints the cost estimate, waits for --yes, then plays
every chosen session N times with pytest (tests/repeated_asks_play.py), each parallel worker
in its own database, and stops with a STOP: line once spending passes 1.5 times the estimate.
A session whose record is already saved is skipped. Cut-off attempts are saved separately
and only those sessions are replayed automatically."""

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from .cases import Case

# The producer's real cost per owner message: the prototype's hand run measured $0.40 for a
# three-message session and this eval's first session $0.81 for four (Nov 24).
USD_PER_MESSAGE = Decimal("0.20")
# Spending past this times the estimate stops the run.
STOP_AT = Decimal("1.5")
BACKEND = Path(__file__).resolve().parents[2]
PLAYER = "tests/repeated_asks_play.py"


@dataclass(frozen=True)
class Session:
    case: str
    repeat: int

    @property
    def folder(self) -> str:
        return f"{self.case}--{self.repeat}"

    def record_at(self, out: Path) -> Path:
        return out / "sessions" / self.folder / "record.json"

    def attempts_at(self, out: Path) -> Path:
        return out / "attempts" / self.folder


def was_cut_off(record: dict[str, Any]) -> bool:
    return any(ask.get("cut_offs") for ask in record["asks"])


def save_attempt(out: Path, session: Session, record: dict[str, Any]) -> None:
    """Keep a rejected attempt with its developer reasons and cost, outside the scored run."""
    attempt_id = uuid4().hex
    path = (
        session.attempts_at(out) / f"{attempt_id}.json"
        if was_cut_off(record)
        else session.record_at(out)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # The budget monitor can see the file while other sessions are still playing.
    writing = path.with_suffix(f".{attempt_id}.tmp")
    writing.write_text(json.dumps(record, indent=1, default=str))
    writing.replace(path)


def sessions_to_play(cases: Sequence[Case], repeats: int, out: Path) -> list[Session]:
    """Every case's repeats not saved yet."""
    planned = (Session(case.id, repeat) for case in cases for repeat in range(1, repeats + 1))
    return [session for session in planned if not session.record_at(out).is_file()]


def estimate(cases: Sequence[Case], sessions: Sequence[Session]) -> Decimal:
    return USD_PER_MESSAGE * _message_count(cases, sessions)


def _message_count(cases: Sequence[Case], sessions: Sequence[Session]) -> int:
    messages = {case.id: len(case.messages) for case in cases}
    return sum(messages[each.case] for each in sessions)


def spent(out: Path, sessions: Sequence[Session]) -> Decimal:
    """What the scored sessions and their discarded attempts cost."""
    total = Decimal(0)
    for each in sessions:
        for record in [each.record_at(out), *each.attempts_at(out).glob("*.json")]:
            if record.is_file():
                total += Decimal(str(json.loads(record.read_text())["cost_usd"]))
    return total


def player_command(parallel: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        PLAYER,
        "-q",
        "-p",
        "no:cacheprovider",
        *(["-n", str(parallel), "--dist", "worksteal"] if parallel > 1 else []),
    ]


def run(
    name: str,
    cases: Sequence[Case],
    *,
    cases_file: Path,
    out: Path,
    repeats: int,
    parallel: int,
    yes: bool,
    command: Callable[[int], list[str]] = player_command,
    poll_seconds: float = 10,
    say: TextIO = sys.stdout,
) -> int:
    """Play the sessions; 0 when every one was played, 2 on a STOP."""
    out.mkdir(parents=True, exist_ok=True)
    sessions = sessions_to_play(cases, repeats, out)
    cost = estimate(cases, sessions)
    messages = _message_count(cases, sessions)
    print(
        f"Estimate: ${cost:.2f} for {len(sessions)} sessions to play "
        f"({len(cases)} cases x {repeats}, {messages} owner messages left) at "
        f"${USD_PER_MESSAGE} an owner message; STOP past ${cost * STOP_AT:.2f}.",
        file=say,
        flush=True,
    )
    if not sessions:
        print("Every session is saved already.", file=say, flush=True)
        return 0
    if not yes:
        print("Nothing started. Run again with --yes to spend it.", file=say, flush=True)
        return 0
    _write_run(out, name, cases, repeats, cost)
    environment = {
        **os.environ,
        "REPEATED_ASKS_OUT": str(out.resolve()),
        "REPEATED_ASKS_CASES": str(cases_file.resolve()),
        "REPEATED_ASKS_REAL": "1",
        # pytest-django makes test_<name> from it, and each parallel worker its own copy.
        "DATABASE_URL": _database_url(name),
    }
    spent_before = spent(out, sessions)
    now = Decimal(0)
    done = 0

    def over_budget() -> bool:
        nonlocal now, done
        now = spent(out, sessions) - spent_before
        saved = sum(each.record_at(out).is_file() for each in sessions)
        if saved != done:
            done = saved
            print(f"{done}/{len(sessions)} sessions saved, ${now:.2f} spent", file=say, flush=True)
        return now > cost * STOP_AT

    pending = sessions
    exit_code = 0
    with (out / "pytest.log").open("a") as log:
        while pending:
            prior = {each: set(each.attempts_at(out).glob("*.json")) for each in pending}
            environment["REPEATED_ASKS_SESSIONS"] = json.dumps(
                [{"case": each.case, "repeat": each.repeat} for each in pending]
            )
            status = _play(command(parallel), environment, log, poll_seconds, over_budget)
            if status is None:
                print(
                    f"STOP: spent ${now:.2f}, past {STOP_AT} times the ${cost:.2f} estimate. "
                    f"{done}/{len(sessions)} sessions saved; run again to carry on.",
                    file=say,
                    flush=True,
                )
                return 2
            if status:
                exit_code = 1
                break
            pending = [
                each
                for each in pending
                if not each.record_at(out).is_file()
                and set(each.attempts_at(out).glob("*.json")) - prior[each]
            ]
            if pending:
                print(f"Replaying {len(pending)} cut-off sessions.", file=say, flush=True)

    missing = [each.folder for each in sessions if not each.record_at(out).is_file()]
    print(
        f"Done: {len(sessions) - len(missing)}/{len(sessions)} sessions saved, ${now:.2f} spent."
        + (f" Not saved (see pytest.log): {', '.join(missing)}" if missing else ""),
        file=say,
        flush=True,
    )
    return 1 if missing else exit_code


def _play(
    command: list[str],
    environment: dict[str, str],
    log: TextIO,
    poll_seconds: float,
    over_budget: Callable[[], bool],
) -> int | None:
    child = subprocess.Popen(
        command,
        cwd=BACKEND,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        while True:
            try:
                status = child.wait(timeout=poll_seconds)
            except subprocess.TimeoutExpired:
                status = None
            if over_budget():
                return None
            if status is not None:
                return status
    finally:
        if child.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            child.wait()


def _database_url(name: str) -> str:
    base = os.environ.get("DATABASE_URL", "postgres://adforge:adforge@localhost:5432/adforge")
    root, _, _ = base.rpartition("/")
    safe = "".join(c if c.isalnum() else "_" for c in name.lower())
    return f"{root}/repeated_asks_{safe}"


def _write_run(out: Path, name: str, cases: Sequence[Case], repeats: int, cost: Decimal) -> None:
    info: dict[str, Any] = {
        "name": name,
        "started_at": datetime.now(UTC).isoformat(),
        "app_commit": _commit(),
        "asked_changes": True,
        "cases": [case.id for case in cases],
        "repeats": repeats,
        "estimate_usd": str(cost),
    }
    (out / "run.json").write_text(json.dumps(info, indent=1))


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip()
    except OSError, subprocess.CalledProcessError:
        return ""
