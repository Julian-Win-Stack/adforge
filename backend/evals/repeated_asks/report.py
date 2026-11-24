"""The before/after report: per kind, the counts of a "before" run and an "after" run side by
side, as counts, then every session whose remakes differ, and one JSON file a results page is
built from."""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, TextIO

from .cases import KINDS, Case
from .counts import counts

LIMIT = (
    "Known limit: the owner's messages are fixed. A real owner would react to the producer's "
    "reply; here the next message is sent whatever the producer said."
)


def load_run(folder: Path) -> list[dict[str, Any]]:
    """Every session record a run saved, oldest case first."""
    return [
        json.loads(path.read_text()) for path in sorted((folder / "sessions").glob("*/record.json"))
    ]


def kind_counts(records: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """One kind's counts over every session and repeat of a run."""
    mine = [(record, counts(record)) for record in records if record["kind"] == kind]
    asks = KINDS[kind][1]
    found: dict[str, Any] = {
        "sessions": len(mine),
        "errors": sum(each["error"] for _, each in mine),
        "remakes": sum(each["remakes"] for _, each in mine),
        "owner": sum(each["owner"] for _, each in mine),
        "producer": sum(each["producer"] for _, each in mine),
        "code": sum(each["code"] for _, each in mine),
        "per_ask": [sum(_at(each["per_ask"], n) for _, each in mine) for n in range(asks)],
        "remade_on_ask": {
            str(n + 1): sum(_at(each["per_ask"], n) > 0 for _, each in mine) for n in range(2, asks)
        },
        "refused_first": sum(each["refused_first"] for _, each in mine),
        "cost_usd": round(sum(record["cost_usd"] for record, _ in mine), 2),
    }
    listed = [each for _, each in mine if "problems" in each]
    if listed:
        found["problems_filed"] = sum(each["problems"] for each in listed)
        found["sessions_by_problems"] = _tally(each["problems"] for each in listed)
        found["most_asks_on_one_problem"] = _tally(
            max(each["asks_per_problem"], default=0) for each in listed
        )
        found["suggested_sessions"] = sum(any(each["suggested_on_ask"]) for each in listed)
    return found


def _at(values: list[int], index: int) -> int:
    return values[index] if index < len(values) else 0


def _tally(values: Any) -> dict[str, int]:
    tally: dict[str, int] = defaultdict(int)
    for value in values:
        tally[str(value)] += 1
    return dict(sorted(tally.items(), key=lambda item: int(item[0])))


ROWS: list[tuple[str, str]] = [
    ("sessions", "sessions played (cases x repeats)"),
    ("errors", "sessions that crashed"),
    ("remakes", "paid remakes started"),
    ("owner", "  of them owner-asked notes"),
    ("producer", "  of them the producer's own ideas"),
    ("code", "  of them code's talking recovery"),
    ("per_ask", "remakes on ask 1, 2, 3, 4"),
    ("remade_on_ask", "sessions with a remake on ask 3 / 4"),
    ("refused_first", "sessions with no remake on ask 1"),
    ("problems_filed", "problems filed"),
    ("sessions_by_problems", "sessions by problems filed"),
    ("most_asks_on_one_problem", "sessions by most asks on one problem"),
    ("suggested_sessions", "sessions given a 'don't repeat' suggestion"),
    ("cost_usd", "producer cost, $"),
]


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = {
        kind: {
            "good": KINDS[kind][2],
            "before": kind_counts(before, kind),
            "after": kind_counts(after, kind),
        }
        for kind in KINDS
    }
    return {"kinds": kinds, "differ": _differing(before, after), "limit": LIMIT}


def _remakes_by_case(records: list[dict[str, Any]]) -> dict[str, list[list[int]]]:
    found: dict[str, list[list[int]]] = defaultdict(list)
    for record in sorted(records, key=lambda each: each["repeat"]):
        found[record["case"]].append(counts(record)["per_ask"])
    return found


def _differing(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    then, now = _remakes_by_case(before), _remakes_by_case(after)
    kinds = {record["case"]: record["kind"] for record in [*before, *after]}
    return [
        {
            "case": case,
            "kind": kinds[case],
            "before": then.get(case, []),
            "after": now.get(case, []),
        }
        for case in sorted(set(then) | set(now))
        if then.get(case) != now.get(case)
    ]


def print_report(compared: dict[str, Any], say: TextIO) -> None:
    for kind, found in compared["kinds"].items():
        print(f"\n== {kind}: {found['good']}", file=say)
        print(f"{'':48} {'before':>16} {'after':>16}", file=say)
        for key, label in ROWS:
            then, now = found["before"].get(key), found["after"].get(key)
            if then is None and now is None:
                continue
            print(f"{label:48} {_shown(then):>16} {_shown(now):>16}", file=say)
    print("\n== Sessions whose remakes per ask differ (one list per repeat)", file=say)
    for each in compared["differ"]:
        print(f"{each['case']}: before {each['before']}  after {each['after']}", file=say)
    print(f"\n{compared['limit']}", file=say)


def _shown(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return " ".join(str(each) for each in value)
    if isinstance(value, dict):
        return " ".join(f"{key}:{count}" for key, count in value.items()) or "none"
    return str(value)


def page_data(
    compared: dict[str, Any],
    cases: dict[str, Case],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, Any]:
    """What a results page shows: the comparison, and every session's messages with each
    run's chat and the producer's instruction on each ask, before beside after."""
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"before": [], "after": []}
    )
    for side, records in (("before", before), ("after", after)):
        for record in sorted(records, key=lambda each: each["repeat"]):
            by_case[record["case"]][side].append(
                {
                    "repeat": record["repeat"],
                    "error": record["error"],
                    "cost_usd": record["cost_usd"],
                    "problems": record.get("problems"),
                    "counts": counts(record),
                    "asks": record["asks"],
                }
            )
    sessions = [
        {
            "case": case.id,
            "kind": case.kind,
            "shop": case.shop.product,
            "messages": [message.say for message in case.messages],
            **by_case.get(case.id, {"before": [], "after": []}),
        }
        for case in cases.values()
    ]
    return {**compared, "sessions": sessions}


def report(
    before: Path, after: Path, cases: dict[str, Case], json_out: Path | None, say: TextIO
) -> dict[str, Any]:
    then, now = load_run(before), load_run(after)
    compared = compare(then, now)
    print_report(compared, say)
    data = page_data(compared, cases, then, now)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(data, indent=1, default=str))
        print(f"\nWrote {json_out}", file=say)
    return data
