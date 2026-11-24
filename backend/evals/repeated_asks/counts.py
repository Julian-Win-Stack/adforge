"""What code counts from one session's record: the paid remakes the producer started, per
owner message, and, when the app keeps a problem list, how it filed them. Pure: reads the
record only."""

import re
from typing import Any

# A problem-list note's suggestion names the problem it is about (prototype #738's wording).
_SUGGESTED = re.compile(r"about problem (\d+) again")


def counts(record: dict[str, Any]) -> dict[str, Any]:
    """The session's counts. A remake is one scene agent run started while an owner message
    was worked on: the owner's note ("owner"), the producer's own idea ("producer"), or
    code's talking recovery after a failed extra idea ("code")."""
    asks = record["asks"]
    per_ask = [_by_origin(ask["runs"]) for ask in asks]
    found: dict[str, Any] = {
        "remakes": sum(each["all"] for each in per_ask),
        "owner": sum(each["owner"] for each in per_ask),
        "producer": sum(each["producer"] for each in per_ask),
        "code": sum(each["code"] for each in per_ask),
        "per_ask": [each["all"] for each in per_ask],
        "per_ask_by_origin": per_ask,
        "remade_from_ask_3": [each["all"] > 0 for each in per_ask[2:]],
        "refused_first": bool(asks) and per_ask[0]["all"] == 0,
        "error": bool(record.get("error")),
    }
    problems = record.get("problems")
    if problems is not None:
        found["problems"] = len(problems)
        found["asks_per_problem"] = [problem["asks"] for problem in problems]
        found["suggested_on_ask"] = [_suggested(ask) for ask in asks]
    return found


def _by_origin(runs: list[dict[str, Any]]) -> dict[str, int]:
    by = {"owner": 0, "producer": 0, "code": 0}
    for run in runs:
        by[run["origin"]] += 1
    return {**by, "all": sum(by.values())}


def _suggested(ask: dict[str, Any]) -> list[int]:
    """The problems whose 'don't repeat' suggestion came with this owner message."""
    return sorted(
        {
            int(number)
            for note in ask["system_notes"]
            if note["kind"] == "problem_list"
            for number in _SUGGESTED.findall(note["text"])
        }
    )
