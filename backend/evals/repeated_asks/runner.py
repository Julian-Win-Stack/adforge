"""Runs the scripted owner sessions: prints the cost estimate, waits for --yes, then plays
every chosen session N times with pytest (tests/repeated_asks_play.py), each parallel worker
in its own database, and stops with a STOP: line once spending passes 1.5 times the estimate.
A session whose record is already saved is not played again, so a stopped run carries on."""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from .cases import Case

# The producer's real cost per owner message: the prototype's hand run measured $0.40 for a
# three-message session and this eval's first session $0.81 for four (Nov 24).
USD_PER_MESSAGE = Decimal(os.environ.get("REPEATED_ASKS_USD_PER_MESSAGE") or "0.09")
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


def sessions_to_play(cases: Sequence[Case], repeats: int, out: Path) -> list[Session]:
    """Every case's repeats not saved yet."""
    return [
        Session(case.id, repeat)
        for case in cases
        for repeat in range(1, repeats + 1)
        if not (out / "sessions" / f"{case.id}--{repeat}" / "record.json").is_file()
    ]


def estimate(cases: Sequence[Case], sessions: Sequence[Session]) -> Decimal:
    messages = {case.id: len(case.messages) for case in cases}
    return sum((USD_PER_MESSAGE * messages[each.case] for each in sessions), Decimal(0))


def spent(out: Path, sessions: Sequence[Session]) -> Decimal:
    """What the sessions saved so far cost."""
    total = Decimal(0)
    for each in sessions:
        record = out / "sessions" / each.folder / "record.json"
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
    messages = sum(len(case.messages) for case in cases) * repeats
    print(
        f"Estimate: ${cost:.2f} for {len(sessions)} sessions to play "
        f"({len(cases)} cases x {repeats}, {messages} owner messages in all) at "
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
        "REPEATED_ASKS_OUT": str(out),
        "REPEATED_ASKS_CASES": str(cases_file),
        "REPEATED_ASKS_REAL": "1",
        "REPEATED_ASKS_SESSIONS": json.dumps(
            [{"case": each.case, "repeat": each.repeat} for each in sessions]
        ),
        # pytest-django makes test_<name> from it, and each parallel worker its own copy.
        "DATABASE_URL": _database_url(name),
    }
    log = (out / "pytest.log").open("a")
    child = subprocess.Popen(
        command(parallel),
        cwd=BACKEND,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    done = 0
    while child.poll() is None:
        time.sleep(poll_seconds)
        now = spent(out, sessions)
        saved = sum((out / "sessions" / each.folder / "record.json").is_file() for each in sessions)
        if saved != done:
            done = saved
            print(f"{done}/{len(sessions)} sessions saved, ${now:.2f} spent", file=say, flush=True)
        if now > cost * STOP_AT:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait()
            print(
                f"STOP: spent ${now:.2f}, past {STOP_AT} times the ${cost:.2f} estimate. "
                f"{done}/{len(sessions)} sessions saved; run again to carry on.",
                file=say,
                flush=True,
            )
            return 2
    now = spent(out, sessions)
    missing = [each.folder for each in sessions if not (out / "sessions" / each.folder).is_dir()]
    print(
        f"Done: {len(sessions) - len(missing)}/{len(sessions)} sessions saved, ${now:.2f} spent."
        + (f" Not saved (see pytest.log): {', '.join(missing)}" if missing else ""),
        file=say,
        flush=True,
    )
    return 0 if not missing else 1


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
        "problem_list": _has_problem_list(),
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


def _has_problem_list() -> bool:
    return (BACKEND / "agents" / "owner_problems.py").is_file()
