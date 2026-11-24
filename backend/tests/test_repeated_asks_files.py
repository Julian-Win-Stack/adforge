"""The repeated-ask eval's case file, counts, report and runner (evals/repeated_asks), without
a chat: pure logic and files."""

import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agents.current_limits import current_limits
from agents.hard_limits import hard_limits
from evals.repeated_asks.cases import (
    CASES_FILE,
    KINDS,
    BadCases,
    check_counts,
    load_cases,
)
from evals.repeated_asks.counts import counts
from evals.repeated_asks.report import compare, print_report
from evals.repeated_asks.runner import STOP_AT, USD_PER_MESSAGE, estimate, run, sessions_to_play


def test_the_case_file_holds_each_kinds_sessions_with_unique_ids() -> None:
    cases = load_cases()

    check_counts(cases)
    assert len(cases) == 50
    for case in cases.values():
        assert len(case.messages) == KINDS[case.kind][1]


def _file(tmp_path: Path, change: Any) -> Path:
    data = json.loads(CASES_FILE.read_text())
    change(data)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (lambda d: d["cases"][3].update(kind="tricky"), "unknown kind"),
        (lambda d: d["cases"][3]["messages"].pop(), "sends 4 owner messages"),
        (lambda d: d["cases"][3]["messages"][1].pop("look"), "needs say, ending and look"),
        (lambda d: d["cases"][3]["messages"][1].update(ending="maybe"), "ending must be"),
        (lambda d: d["cases"][3]["messages"][1].pop("reason"), "reason is given exactly"),
        (lambda d: d["cases"][4].update(id=d["cases"][3]["id"]), "used twice"),
        (lambda d: d["cases"][3].update(shop="nowhere"), "unknown shop"),
    ],
)
def test_a_malformed_case_file_is_refused_naming_the_case(
    tmp_path: Path, change: Any, named: str
) -> None:
    case_id = json.loads(CASES_FILE.read_text())["cases"][3]["id"]

    with pytest.raises(BadCases, match=named) as refused:
        load_cases(_file(tmp_path, change))
    assert case_id in str(refused.value)


def test_a_shop_line_too_short_to_film_is_refused(tmp_path: Path) -> None:
    def short(data: dict[str, Any]) -> None:
        data["shops"]["brush"]["lines"][2] = "Get it today."

    with pytest.raises(BadCases, match="shop brush"):
        load_cases(_file(tmp_path, short))


# The words of each line of the producer's written limits lists that an ask must not touch,
# or the up-front "can't" answers it and the eval measures nothing.
LIMIT_WORDS = [
    ("spell or write", ["word", "text", "spell", "writ", "letter"]),
    ("tiny print", ["print", "label", "letter"]),
    ("Captions", ["caption", "end card", "font", "on-screen"]),
    ("Two presenters", ["two presenters", "celebrit", "second person", "another person"]),
    ("Silence", ["music", "song", "silen"]),
    ("Logos", ["logo", "language", "english", "spanish"]),
    ("Exact length", ["seconds long", "length of the ad", "shorter ad", "longer ad"]),
    ("Stop filming", ["stop filming"]),
    ("Whole-ad", ["every scene", "whole ad", "all scenes", "product photo"]),
    ("Side-by-side", ["side-by-side", "side by side", "versions"]),
    ("footage", ["footage", "my video"]),
]


def test_no_impossible_or_hard_ask_touches_a_written_limit() -> None:
    limits = (hard_limits() + "\n" + current_limits()).splitlines()[1:]
    lines = [line for line in limits if line.startswith("- **")]
    assert len(lines) == len(LIMIT_WORDS), "a limit was added: give its words here"
    for line, (named, _) in zip(lines, LIMIT_WORDS, strict=True):
        assert named in line
    for case in load_cases().values():
        if case.kind not in (
            "impossible_check_fails",
            "impossible_check_passes",
            "hard_but_possible",
        ):
            continue
        for message in case.messages:
            for named, words in LIMIT_WORDS:
                touched = [word for word in words if word in message.say.lower()]
                assert not touched, f"{case.id} touches the limit {named!r}: {touched}"


def record(per_ask: list[list[str]], problems: Any = None, notes: Any = None) -> dict[str, Any]:
    return {
        "case": "c",
        "kind": "healthy",
        "repeat": 1,
        "cost_usd": 0.5,
        "error": "",
        "problems": problems,
        "asks": [
            {
                "runs": [{"origin": origin} for origin in origins],
                "system_notes": (notes or {}).get(number, []),
                "chat": [],
            }
            for number, origins in enumerate(per_ask, 1)
        ],
    }


def test_counts_split_remakes_by_who_started_them_and_by_ask() -> None:
    found = counts(record([["owner", "producer", "code"], [], ["owner"], []]))

    assert found["per_ask"] == [3, 0, 1, 0]
    assert (found["owner"], found["producer"], found["code"]) == (2, 1, 1)
    assert found["remade_from_ask_3"] == [True, False]
    assert found["refused_first"] is False
    assert "problems" not in found


def test_counts_read_the_problem_list_and_each_asks_suggestion() -> None:
    suggestion = {
        "kind": "problem_list",
        "text": "Problem 1: x\n  If the owner's newest message is about problem 1 again, ...",
    }
    found = counts(
        record(
            [["owner"], ["owner"], []],
            problems=[{"asks": 3}, {"asks": 1}],
            notes={3: [suggestion]},
        )
    )

    assert found["problems"] == 2
    assert found["asks_per_problem"] == [3, 1]
    assert found["suggested_on_ask"] == [[], [], [1]]


def test_the_report_prints_counts_side_by_side_and_every_differing_session() -> None:
    before = [record([["owner"], ["owner"], ["owner"]])]
    after = [record([["owner"], ["owner"], []], problems=[{"asks": 2}])]
    printed = io.StringIO()

    compared = compare(before, after)
    print_report(compared, printed)

    healthy = compared["kinds"]["healthy"]
    assert healthy["before"]["per_ask"] == [1, 1, 1]
    assert healthy["after"]["per_ask"] == [1, 1, 0]
    assert "problems_filed" not in healthy["before"] and healthy["after"]["problems_filed"] == 1
    assert compared["differ"] == [
        {"case": "c", "kind": "healthy", "before": [[1, 1, 1]], "after": [[1, 1, 0]]}
    ]
    assert "remakes on ask 1, 2, 3, 4" in printed.getvalue()
    assert "Known limit" in printed.getvalue()


def _saving(costs: list[float], tmp: Path) -> Any:
    """A stand-in for the player that saves one record per session at these costs."""
    script = tmp / "save.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "out = pathlib.Path(os.environ['REPEATED_ASKS_OUT'])\n"
        "sessions = json.loads(os.environ['REPEATED_ASKS_SESSIONS'])\n"
        f"costs = {costs!r}\n"
        "for each, cost in zip(sessions, costs):\n"
        "    folder = out / 'sessions' / f\"{each['case']}--{each['repeat']}\"\n"
        "    folder.mkdir(parents=True)\n"
        "    (folder / 'record.json').write_text(json.dumps({'cost_usd': cost}))\n"
        "import time; time.sleep(5)\n"
    )
    return lambda parallel: [sys.executable, str(script)]


def test_the_run_prints_its_estimate_and_starts_nothing_without_yes(tmp_path: Path) -> None:
    cases = list(load_cases().values())[:2]
    said = io.StringIO()

    code = run(
        "t",
        cases,
        cases_file=CASES_FILE,
        out=tmp_path,
        repeats=3,
        parallel=1,
        yes=False,
        command=_saving([9.0], tmp_path),
        say=said,
    )

    assert code == 0
    assert f"Estimate: ${USD_PER_MESSAGE * 8 * 3:.2f}" in said.getvalue()
    assert not (tmp_path / "sessions").exists()


def test_the_run_stops_with_a_stop_line_past_one_and_a_half_times_the_estimate(
    tmp_path: Path,
) -> None:
    cases = list(load_cases().values())[:1]
    planned = estimate(cases, sessions_to_play(cases, 2, tmp_path))
    said = io.StringIO()

    code = run(
        "t",
        cases,
        cases_file=CASES_FILE,
        out=tmp_path,
        repeats=2,
        parallel=1,
        yes=True,
        command=_saving([float(planned * STOP_AT), 0.01], tmp_path),
        poll_seconds=0.2,
        say=said,
    )

    assert code == 2
    assert "STOP: spent" in said.getvalue()


def test_a_saved_session_is_not_played_again(tmp_path: Path) -> None:
    cases = list(load_cases().values())[:1]
    folder = tmp_path / "sessions" / f"{cases[0].id}--1"
    folder.mkdir(parents=True)
    (folder / "record.json").write_text(json.dumps({"cost_usd": 0.3}))

    left = sessions_to_play(cases, 2, tmp_path)

    assert [(each.case, each.repeat) for each in left] == [(cases[0].id, 2)]
    assert estimate(cases, left) == USD_PER_MESSAGE * len(cases[0].messages) * Decimal(1)
