"""The break test of the rule evals: a judge that passes a case should pass it for the right
reason, so each case Julian passed is copied with one small edit that breaks its rule, and
the judge must now fail the copy (Julian 2026-10-09 21:15: "you break every single rule that
are passing ... and see whether they actually break or not"). The copies are Claude's, not
Julian's grades: they live in their own file, never in rule_cases.json. From backend/:

    uv run python -m evals.break_passes --model gpt-6-luna --edits FILE [--out FILE]

Each entry in the edits file is {"id": <a passing case>, "breaks": <why the edit breaks the
rule>, "edits": [{"field": <scene field, or plan.<index>.<field>>, "find": <exact text>,
"replace": <text>} or {"field": ..., "value": <the field's new value>}]}. The field
"start_picture" takes the path of another picture the app really drew, for a copy whose
broken text the case's own picture would still contradict.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from evals.ai_checks import Case, RuleVerdict, ask_majority, load_cases  # noqa: E402
from gateway.types import UnusableReply  # noqa: E402

# As in calibrate_ai_checks: far under Azure's per-model text limits.
AT_ONCE = 8


def broken(case: Case, edits: list[dict[str, Any]]) -> Case:
    """`case` with `edits` made to what its judge reads; raises when an edit's text is missing,
    so a copy never silently goes to the judge unbroken."""
    scene = deepcopy(case.handoff.scene)
    plan = deepcopy(case.handoff.plan)
    start_picture = case.start_picture
    for edit in edits:
        if edit["field"] == "start_picture":
            start_picture = Path(edit["value"])
            continue
        path = edit["field"].split(".")
        if path[0] == "plan":
            assert plan is not None, f"{case.id} has no plan"
            holder, key = plan[int(path[1])], path[2]
        else:
            assert scene is not None, f"{case.id} has no scene"
            holder, key = scene, path[0]
        if "value" in edit:
            holder[key] = edit["value"]
        else:
            assert edit["find"] in holder[key], f"{case.id}: {edit['find']!r} not in {key}"
            holder[key] = holder[key].replace(edit["find"], edit["replace"], 1)
    handoff = case.handoff.model_copy(update={"scene": scene, "plan": plan})
    return replace(case, handoff=handoff, label="fail", start_picture=start_picture)


def main(model: str, edits_file: Path, out: Path | None) -> None:
    cases = {case.id: case for case in load_cases()}
    entries = json.loads(edits_file.read_text())
    copies = []
    for entry in entries:
        case = cases[entry["id"]]
        assert case.label == "pass", f"{case.id} is not one Julian passed"
        copies.append((entry, broken(case, entry["edits"])))

    def ask(copy: Case) -> RuleVerdict:
        try:
            return ask_majority(copy, model).output
        except UnusableReply as error:
            return RuleVerdict(decision="pass", reason=f"NO USABLE ANSWER: {error}"[:300])

    with ThreadPoolExecutor(AT_ONCE) as pool:
        verdicts = list(pool.map(ask, (copy for _, copy in copies)))
    still_pass = [
        (entry, verdict)
        for (entry, _), verdict in zip(copies, verdicts, strict=True)
        if verdict.decision == "pass"
    ]
    print(f"# {model}: {len(copies)} broken copies, {len(copies) - len(still_pass)} now fail")
    for entry, verdict in still_pass:
        print(f"  STILL PASSES {entry['id']} ({entry['breaks']}): {verdict.reason}")
    if out is not None:
        out.write_text(
            json.dumps(
                [
                    {**entry, "decision": verdict.decision, "reason": verdict.reason}
                    for (entry, _), verdict in zip(copies, verdicts, strict=True)
                ],
                indent=2,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--edits", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    main(args.model, args.edits, args.out)
