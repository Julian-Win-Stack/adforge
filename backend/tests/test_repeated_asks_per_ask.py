import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from evals.repeated_asks import cases as case_files
from evals.repeated_asks.__main__ import main
from evals.repeated_asks.runner import BACKEND, run


def test_the_asked_changes_case_set_has_nine_groups_and_labels_every_ask() -> None:
    cases = case_files.load_cases(case_files.ASKED_CHANGES_CASES_FILE)

    case_files.check_counts(cases)

    assert Counter(case.group for case in cases.values()) == {
        "cant_be_done": 20,
        "hard_but_possible": 8,
        "alternative": 6,
        "reworded_after_closed": 4,
        "other_scene": 8,
        "closed_and_new": 4,
        "breaks": 3,
        "much_later": 4,
        "normal": 14,
    }
    assert all(
        type(message.expected_try) is bool for case in cases.values() for message in case.messages
    )
    assert [message.expected_try for message in cases["impossible-fails-01-brush"].messages] == [
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def _save(folder: Path, case: str, asked: list[bool], *, repeat: int = 1) -> None:
    session = folder / "sessions" / f"{case}--{repeat}"
    session.mkdir(parents=True)
    (session / "record.json").write_text(
        json.dumps(
            {
                "case": case,
                "repeat": repeat,
                "asks": [
                    {
                        "number": number,
                        "runs": [{"origin": "owner", "clips": 2}] if tried else [],
                    }
                    for number, tried in enumerate(asked, 1)
                ],
            }
        )
    )


def test_the_saved_run_reports_sessions_that_tried_on_each_ask_and_the_main_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = tmp_path / "results.json"
    _save(tmp_path, "impossible-fails-01-brush", [True] * 3 + [False] * 7)
    _save(tmp_path, "impossible-fails-01-brush", [True] * 4 + [False] * 6, repeat=2)
    _save(tmp_path, "alternative-works-01-brush", [True, True, True, False, True, False])
    _save(
        tmp_path, "alternative-fails-04-floss", [True] * 3 + [False, True, True, True] + [False] * 4
    )

    assert (
        main(
            [
                "report",
                "--cases-file",
                str(case_files.ASKED_CHANGES_CASES_FILE),
                "--after",
                str(tmp_path),
                "--json",
                str(result),
            ]
        )
        == 0
    )

    data: dict[str, Any] = json.loads(result.read_text())
    impossible = data["groups"]["cant_be_done"]
    assert impossible["sessions"] == 2
    assert impossible["sessions_as_expected"] == 1
    assert impossible["per_ask"][:4] == [
        {"number": 1, "sessions": 2, "tried": 2, "as_expected": 2},
        {"number": 2, "sessions": 2, "tried": 2, "as_expected": 2},
        {"number": 3, "sessions": 2, "tried": 2, "as_expected": 2},
        {"number": 4, "sessions": 2, "tried": 1, "as_expected": 1},
    ]
    assert data["groups"]["alternative"]["per_ask"][6] == {
        "number": 7,
        "sessions": 1,
        "tried": 1,
        "as_expected": 1,
    }
    assert data["main_baseline"] == {"sessions": 60, "tried": [51, 43, 42, 33]}
    printed = capsys.readouterr().out
    assert printed.startswith("Did the producer try again?")
    assert "51/60" in printed and "33/60" in printed
    assert "remakes per session" not in printed and "clips" not in result.read_text()


def test_the_new_case_set_prints_its_estimate_without_starting_paid_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "run",
                "--cases-file",
                str(case_files.ASKED_CHANGES_CASES_FILE),
                "--name",
                "estimate",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )

    printed = capsys.readouterr().out
    assert "Estimate: $96.60 for 71 sessions" in printed
    assert "483 owner messages" in printed
    assert not (tmp_path / "sessions").exists()
    assert not (tmp_path / "run.json").exists()


def test_a_cut_off_session_is_saved_separately_and_replayed(tmp_path: Path) -> None:
    cases = case_files.load_cases(case_files.ASKED_CHANGES_CASES_FILE)
    case_id = "impossible-fails-01-brush"
    child = tmp_path / "player.py"
    child.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(BACKEND)!r})\n"
        "from evals.repeated_asks.runner import Session, save_attempt\n"
        "out = Path(os.environ['REPEATED_ASKS_OUT'])\n"
        "for item in json.loads(os.environ['REPEATED_ASKS_SESSIONS']):\n"
        "    session = Session(**item)\n"
        "    replay = any((out / 'attempts' / session.folder).glob('*.json'))\n"
        "    ask = {'number': 1, 'runs': [{'origin': 'owner'}]}\n"
        "    if not replay:\n"
        "        ask['cut_offs'] = ['The producer hit its tool limit.']\n"
        "    save_attempt(out, session, {**item, 'asks': [ask], 'cost_usd': 0.1})\n"
    )
    said = io.StringIO()

    assert (
        run(
            "fake",
            [cases[case_id]],
            cases_file=case_files.ASKED_CHANGES_CASES_FILE,
            out=tmp_path,
            repeats=1,
            parallel=1,
            yes=True,
            command=lambda _: [sys.executable, str(child)],
            poll_seconds=0.02,
            say=said,
        )
        == 0
    )

    [discarded] = (tmp_path / "attempts" / f"{case_id}--1").glob("*.json")
    assert json.loads(discarded.read_text())["asks"][0]["cut_offs"] == [
        "The producer hit its tool limit.",
    ]
    assert "Replaying 1 cut-off session" in said.getvalue()
    assert "$0.20 spent" in said.getvalue()
    results = tmp_path / "results.json"
    assert (
        main(
            [
                "report",
                "--cases-file",
                str(case_files.ASKED_CHANGES_CASES_FILE),
                "--after",
                str(tmp_path),
                "--json",
                str(results),
            ]
        )
        == 0
    )
    summary = json.loads(results.read_text())["groups"]["cant_be_done"]
    assert summary["sessions"] == 1
    assert summary["per_ask"][0]["tried"] == 1
