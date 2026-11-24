"""Whether the producer tried again on each scripted ask, by group."""

import json
from pathlib import Path
from typing import Any, TextIO, TypedDict

from .cases import ASKED_CHANGE_GROUPS, Case
from .report import LIMIT, load_run
from .runner import was_cut_off
from .scoring import enrich


class Baseline(TypedDict):
    sessions: int
    tried: list[int]


# Saved from the 60 baseline sessions in #743. Reporting never plays them again.
MAIN_BASELINE: Baseline = {"sessions": 60, "tried": [51, 43, 42, 33]}


def summarize(records: list[dict[str, Any]], cases: dict[str, Case]) -> dict[str, Any]:
    """Read one yes or no per saved ask and compare it with the case's expectation."""
    sessions: list[dict[str, Any]] = []
    for record in records:
        case = cases.get(record["case"])
        if case is None or case.group is None or was_cut_off(record):
            continue
        asks = []
        for ask in record["asks"]:
            expected = case.messages[ask["number"] - 1].expected_try
            runs = [
                {key: value for key, value in run.items() if key != "clips"} for run in ask["runs"]
            ]
            asks.append({**ask, "runs": runs, "tried": bool(runs), "expected": expected})
        sessions.append(
            {
                **record,
                "group": case.group,
                "asks": asks,
                "as_expected": (
                    not record.get("error")
                    and len(asks) == len(case.messages)
                    and all(ask["tried"] == ask["expected"] for ask in asks)
                ),
            }
        )

    groups = {}
    for group, title in ASKED_CHANGE_GROUPS.items():
        scripts = [len(case.messages) for case in cases.values() if case.group == group]
        if scripts:
            mine = [session for session in sessions if session["group"] == group]
            groups[group] = _group(title, mine, max(scripts))
    result: dict[str, Any] = {"groups": groups, "sessions": sessions, "limit": LIMIT}
    if "cant_be_done" in groups:
        result["main_baseline"] = MAIN_BASELINE
    enrich(result, cases)
    return result


def _group(title: str, sessions: list[dict[str, Any]], ask_count: int) -> dict[str, Any]:
    by_number: dict[int, list[dict[str, Any]]] = {number: [] for number in range(1, ask_count + 1)}
    for session in sessions:
        for ask in session["asks"]:
            by_number[ask["number"]].append(ask)
    return {
        "title": title,
        "sessions": len(sessions),
        "sessions_as_expected": sum(session["as_expected"] for session in sessions),
        "per_ask": [
            {
                "number": number,
                "sessions": len(asks),
                "tried": sum(ask["tried"] for ask in asks),
                "as_expected": sum(ask["tried"] == ask["expected"] for ask in asks),
            }
            for number, asks in by_number.items()
        ],
    }


def report(
    folder: Path, cases: dict[str, Case], json_out: Path | None, say: TextIO
) -> dict[str, Any]:
    data = summarize(load_run(folder), cases)
    print("Did the producer try again?", file=say)
    for group, counts in data["groups"].items():
        print(f"\n{counts['title']}", file=say)
        baseline = MAIN_BASELINE if group == "cant_be_done" else None
        for ask in counts["per_ask"]:
            n = ask["number"]
            line = (
                f"  Ask {n}: {ask['tried']}/{ask['sessions']} tried, "
                f"{ask['as_expected']}/{ask['sessions']} as expected"
            )
            if baseline is not None and n <= len(baseline["tried"]):
                line += f" (main: {baseline['tried'][n - 1]}/{baseline['sessions']} tried)"
            print(line, file=say)
        print(
            f"  {counts['sessions_as_expected']}/{counts['sessions']} "
            "sessions as expected on every ask",
            file=say,
        )
    print("\nHow the changes were handled", file=say)
    for counts in data["groups"].values():
        scores = counts["scoring"]
        print(
            f"  {counts['title']}: {scores['self_started']} unasked filming starts, "
            f"{scores['repeat_filed_as_new']} repeats filed as new, "
            f"{scores['different_filed_as_old']} new changes filed under an old one, "
            f"{scores['closed_changes']} changes closed. "
            f"Tries per change: {scores['tries_per_change'] or 'none'}.",
            file=say,
        )
        for label, tally in scores["replies"].items():
            print(f"    {label.replace('_', ' ')}: {tally}", file=say)
    print(f"\n{data['limit']}", file=say)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(data, indent=1))
        print(f"\nWrote {json_out}", file=say)
    return data
