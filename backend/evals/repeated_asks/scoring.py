"""Score saved chat sessions using the app's record of what each owner asked for."""

import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any, cast

from .cases import Case

if TYPE_CHECKING:
    from agents.asked_changes import AskRecord, ChangeRecord, SceneRecord

# Each number is the ask that introduces a distinct requested result in these scripts.
_NEW = {
    "alternative_works": {5},
    "alternative_fails": {5},
    "closed_and_new": {5},
    "much_later": {5, 6, 7, 8, 9},
    "healthy": {2, 3},
    "solved_then_different": {4},
}
_ACKNOWLEDGEMENTS = {"hard_but_possible": 4, "alternative_works": 6, "solved_then_different": 3}
_SCENE = re.compile(r"\bscene\s+(\d+)\b", re.IGNORECASE)


def intentions(case: Case, number: int) -> dict[int, int]:
    """Map the script's scene numbers to the ask that introduced each requested result."""
    if _ACKNOWLEDGEMENTS.get(case.kind) == number:
        return {}
    said = case.messages[number - 1].say
    scenes = list(dict.fromkeys(int(match) for match in _SCENE.findall(said)))
    if not scenes:
        # Most follow-ups name the scene. The first ask can supply it for brief repeats.
        scenes = [int(match) for match in _SCENE.findall(case.messages[0].say)]
    if case.kind == "closed_and_new" and number == 5 and len(scenes) == 2:
        return {scenes[0]: 5, scenes[1]: 1}
    if (case.kind, number) in {("alternative_fails", 11), ("much_later", 10)}:
        return dict.fromkeys(scenes, 1)
    introduced = max((ask for ask in _NEW.get(case.kind, ()) if ask <= number), default=1)
    return dict.fromkeys(scenes, introduced)


def enrich(data: dict[str, Any], cases: dict[str, Case]) -> None:
    """Attach the score and the owner scripts to a report of per-ask results."""
    for session in data["sessions"]:
        changes = cast("list[ChangeRecord]", session.get("asked_changes", []))
        case = cases[session["case"]]
        session["scoring"] = _score(session, changes, case)
    refresh_groups(data)
    data["scripts"] = [
        {
            "id": case.id,
            "group": case.group,
            "shop": case.shop.product,
            "messages": [
                {
                    "say": message.say,
                    "expected_try": message.expected_try,
                    "ending": message.ending,
                    "look": message.look,
                    "reason": message.reason,
                }
                for message in case.messages
            ],
        }
        for case in cases.values()
        if case.group is not None
    ]


def _score(session: dict[str, Any], changes: list[ChangeRecord], case: Case) -> dict[str, Any]:
    filings: dict[int, list[tuple[ChangeRecord, SceneRecord, AskRecord]]] = defaultdict(list)
    first_messages: dict[int, int] = {}
    order = {ask.get("message_id"): ask["number"] for ask in session["asks"]}
    for change in changes:
        for scene in change["scenes"]:
            for filing in scene["asks"]:
                message = filing["message_id"]
                filings[message].append((change, scene, filing))
                if message in order:
                    number = change["number"]
                    first_messages[number] = min(
                        first_messages.get(number, order[message]), order[message]
                    )

    # Establish which record belongs to each scripted result from its first valid filing.
    references: dict[int, int] = {}
    closed: dict[tuple[int, int], str] = {}
    mix_ups: Counter[str] = Counter()
    owner_filed = False
    self_started = 0
    for ask in session["asks"]:
        number = ask["number"]
        goals = intentions(case, number)
        early: set[str] = set()
        closing = {closed[goal, scene] for scene, goal in goals.items() if (goal, scene) in closed}
        mistakes: set[tuple[str, int, int]] = set()
        for change, scene, filing in filings.get(ask.get("message_id"), ()):
            actual = change["number"]
            scene_number = scene["scene"]
            goal = goals.get(scene_number)
            first = first_messages[actual]
            if goal == number and first < number:
                mistakes.add(("different_filed_as_old", actual, scene_number))
            elif goal is not None and first == number:
                if goal in references and references[goal] != actual:
                    mistakes.add(("repeat_filed_as_new", actual, scene_number))
                else:
                    references[goal] = actual
            title = f"Scene {scene_number}: {change['description']}"
            for run in filing["runs"]:
                attempt = run["try_number"]
                if attempt is None:
                    continue
                if attempt < session["try_limit"]:
                    early.add(title)
                elif scene["closed"] and goal is not None and references.get(goal) == actual:
                    closed[goal, scene_number] = title
                    if run["outcome"] != "passed" or case.messages[number - 1].ending != "passes":
                        closing.add(title)
        mix_ups.update(kind for kind, _, _ in mistakes)
        ask["filing_mix_ups"] = [
            {"kind": kind, "change": change, "scene": scene}
            for kind, change, scene in sorted(mistakes)
        ]
        for run in ask["runs"]:
            if run["origin"] == "owner":
                owner_filed = True
            elif owner_filed:
                self_started += 1

        checks = {}
        if early:
            checks["gave_up_early"] = _check(ask, early)
        if closing:
            checks["honest_close"] = _check(ask, closing)
        if case.kind == "closed_and_new" and number == 5:
            checks["covered_both"] = _check(ask, closing)
        ask["reply_checks"] = checks

    return {
        "self_started": self_started,
        "repeat_filed_as_new": mix_ups["repeat_filed_as_new"],
        "different_filed_as_old": mix_ups["different_filed_as_old"],
        "closed_changes": sum(
            any(scene["closed"] for scene in change["scenes"]) for change in changes
        ),
        "tries_by_change": [
            sum(scene["tries"] for scene in change["scenes"]) for change in changes
        ],
    }


def _check(ask: dict[str, Any], changes: set[str]) -> dict[str, Any]:
    return {
        "owner": ask.get("said", ""),
        "reply": "\n".join(
            entry["text"] for entry in ask.get("chat", ()) if entry["role"] == "agent"
        ),
        "changes": sorted(changes),
        "answer": None,
    }


def refresh_groups(data: dict[str, Any]) -> None:
    """Compute group counts again after additional replies have been judged."""
    for group, row in data["groups"].items():
        sessions = [session for session in data["sessions"] if session["group"] == group]
        counts = Counter[str]()
        tries: Counter[int] = Counter()
        replies: dict[str, Counter[str]] = defaultdict(Counter)
        for session in sessions:
            for key, value in session["scoring"].items():
                if key == "tries_by_change":
                    tries.update(value)
                else:
                    counts[key] += value
            for ask in session["asks"]:
                for kind, check in ask["reply_checks"].items():
                    replies[kind][check["answer"] or "pending"] += 1
        row["scoring"] = {
            **dict.fromkeys(
                ("self_started", "repeat_filed_as_new", "different_filed_as_old", "closed_changes"),
                0,
            ),
            **counts,
            "tries_per_change": dict(sorted(tries.items())),
            "replies": {kind: dict(tally) for kind, tally in replies.items()},
        }
