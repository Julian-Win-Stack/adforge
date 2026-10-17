"""Comparing what the decider answered with a case's expected answers, one check per thing
expected. A check says only whether the answer is the expected one: the expected answers are
Claude's until Julian grades the first run, so a check is not a grade.

A case's "expect" has one entry per turn, each with any of:
- "outcome": planned / not_a_fix / yes / no; "a|b" for either.
- "scenes": {"<scene>": "<action>"} for each scene the plan must change, an action being
  new_picture, new_clip, new_line, go_back:<N> or no_change ("a|b" for either). No other
  scene may be changed (a no-change row is not a change).
- "scenes_include": the same, but other scenes may be changed too.
- "scene_cants": {"<scene>": "person_look" | "slower"}.
- "scene_cants_include": can'ts that must be on some scene, whichever.
- "cants": the plan's can'ts, in any order.
- "question": whether the plan asks the shop owner a question.
- "clear": {"<photo>": whether the photo shows its thing clearly}.
- "thing_has": {"<photo>": words the thing named for the photo must contain}.
- "looks": [[tool, scene], ...] the decider must have made (for this turn or before).
- "judge_findings": whether it called the judge's findings.

Where a photo goes (in the picture now, more during the action) is not checked: no shop owner
asks or sees it, and the cases check only what the owner meets (Julian, 03:26 Oct 17)."""

from typing import Any

# What each kind of check counts towards in the run's numbers.
KINDS = {
    "outcome": "kind",
    "scenes": "kind",
    "scenes_include": "kind",
    "scene_cants": "cants",
    "scene_cants_include": "cants",
    "cants": "cants",
    "question": "question",
    "clear": "thing",
    "thing_has": "thing",
    "looks": "looks",
    "judge_findings": "judge_findings",
}


def score(case: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """One check per thing the case expects, each with what was expected and what came."""
    checks: list[dict[str, Any]] = []
    for number, (expected, turn) in enumerate(
        zip(case.get("expect", []), result["turns"], strict=False), 1
    ):
        if not expected:
            continue
        got = _answer(turn)
        for what, wanted in expected.items():
            came = _came(what, got, result)
            checks.append(
                {
                    "turn": number,
                    "what": what,
                    "counts_for": KINDS[what],
                    "expected": wanted,
                    "got": came,
                    "same": _same(what, wanted, came),
                }
            )
    return checks


def _answer(turn: dict[str, Any]) -> dict[str, Any]:
    plan = turn.get("plan") or {}
    rows = plan.get("scenes", [])
    decide = turn.get("decide") or {}
    return {
        "outcome": turn["outcome"],
        "scenes": {
            str(row["scene"]): row["action"]
            + (f":{row['go_back_to']}" if row["action"] == "go_back" else "")
            for row in rows
        },
        "scene_cants": {
            str(each["scene"]): each["cant"]
            for each in decide.get("scenes", [])
            if each.get("cant")
        },
        "cants": sorted(decide.get("cants", [])),
        "things": {str(each["photo"]): each["thing"] for each in decide.get("photos", [])},
        "question": bool(plan.get("question")),
        "clear": {str(each["photo"]): each["clear"] for each in decide.get("photos", [])},
    }


def _came(what: str, got: dict[str, Any], result: dict[str, Any]) -> Any:
    if what == "looks":
        seen = {(look["tool"], look.get("scene")) for look in result["looks"]}
        return [list(each) for each in sorted(seen, key=str)]
    if what == "judge_findings":
        return result["judge_findings_calls"] > 0
    if what == "scenes_include":
        return got["scenes"]
    if what == "scene_cants_include":
        return sorted(set(got["scene_cants"].values()))
    if what == "thing_has":
        return got["things"]
    return got[what]


def _same(what: str, wanted: Any, came: Any) -> bool:
    if what == "outcome":
        return bool(came in str(wanted).split("|"))
    if what == "scenes":
        changed = {scene: action for scene, action in came.items() if action != "no_change"}
        wanted_changes = {
            scene: action for scene, action in wanted.items() if action != "no_change"
        }
        return set(changed) == set(wanted_changes) and all(
            came.get(scene) in str(action).split("|") for scene, action in wanted.items()
        )
    if what == "scenes_include":
        return all(came.get(scene) in str(action).split("|") for scene, action in wanted.items())
    if what == "scene_cants_include":
        return set(wanted) <= set(came)
    if what == "thing_has":
        return all(words.lower() in came.get(photo, "").lower() for photo, words in wanted.items())
    if what == "cants":
        return bool(sorted(wanted) == came)
    if what == "looks":
        return all([tool, scene] in came for tool, scene in wanted)
    return bool(wanted == came)


def counts(results: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Per kind of check, [the same as expected, all checks], over every case."""
    totals: dict[str, list[int]] = {}
    for result in results:
        for check in result["checks"]:
            total = totals.setdefault(check["counts_for"], [0, 0])
            total[0] += int(check["same"])
            total[1] += 1
    return totals
