"""Compare regression answers with their trusted baselines, without paid services."""

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evals.cases import FOLDER, load

from .questions import all_questions
from .store import Store, atomic_write_text

type Change = Literal["broke", "got fixed", "unchanged", "unresolved"]


def compare_items(old: Sequence[str], new: Sequence[str]) -> Change:
    """Questions compare across all their items; scene counts may change between runs."""
    if not old or not new or any(value not in ("pass", "fail") for value in (*old, *new)):
        return "unresolved"
    if all(value == "pass" for value in old) and "fail" in new:
        return "broke"
    if "fail" in old and all(value == "pass" for value in new):
        return "got fixed"
    return "unchanged"


@dataclass(frozen=True)
class ItemAnswer:
    item: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class Report:
    text: str
    failures: tuple[str, ...]
    unfiled_scenarios: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.failures


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f"Timestamp needs a timezone: {value!r}")
    return result


def _intentions(
    store: Store, run: str, started_at: str, scenarios: Sequence[str]
) -> dict[tuple[str, str], list[str]]:
    selected: dict[tuple[str, str], list[str]] = {}
    intentions = store.read_intended()
    changed = False
    for intention in intentions:
        if (
            intention["scenario"] not in scenarios
            or _instant(intention["at"]) >= _instant(started_at)
            or intention.get("used_by") not in (None, run)
        ):
            continue
        if "used_by" not in intention:
            intention["used_by"] = run
            changed = True
        key = intention["scenario"], intention["question"]
        selected.setdefault(key, []).append(intention["why"])
    if changed:
        store.write_intended(intentions)
    return selected


def _case_answers(cases_dir: Path) -> dict[tuple[str, str, str], list[ItemAnswer]]:
    answers: dict[tuple[str, str, str], list[ItemAnswer]] = {}
    for path in sorted(cases_dir.glob("*.jsonl")):
        for case in load(path):
            key = case.source["round"], case.source["ad"], path.stem
            item = str(case.inputs["handoff"].get("item", case.id))
            answers.setdefault(key, []).append(ItemAnswer(item, case.label, case.reason))
    return answers


def _code_answers(raw: dict[str, Any]) -> dict[str, list[ItemAnswer]]:
    questions: dict[str, list[ItemAnswer]] = {}
    for question, answer in raw.get("answers", {}).items():
        items = [
            ItemAnswer(item["item"], item["verdict"], item["reason"])
            for item in answer.get("items", [])
        ]
        # An aggregate unresolved answer cannot become a pass through an empty item list.
        if not items or answer["verdict"] in ("not_reached", "needs_label"):
            items.append(ItemAnswer(question, answer["verdict"], answer.get("reason", "")))
        questions[question] = items
    return questions


def _answers_for(
    run: str,
    scenario: str,
    raw: dict[str, Any],
    human: dict[tuple[str, str, str], list[ItemAnswer]],
    *,
    graded: bool,
) -> dict[str, list[ItemAnswer]]:
    answers = _code_answers(raw)
    if graded:
        answers.update(
            {
                question: items
                for (source_run, source_scenario, question), items in human.items()
                if source_run == run and source_scenario == scenario
            }
        )
    return answers


def _description(items: Sequence[ItemAnswer]) -> str:
    if not items:
        return "not_reached (no answer recorded)"
    if any(item.verdict not in ("pass", "fail") for item in items):
        verdict = ", ".join(sorted({item.verdict for item in items}))
    else:
        verdict = "fail" if any(item.verdict == "fail" for item in items) else "pass"
    reasons = [f"{item.item}: {item.reason}" for item in items if item.reason]
    return verdict + (f" ({'; '.join(reasons)})" if reasons else "")


def _first_try(raw: dict[str, Any]) -> tuple[str, str]:
    first = raw.get("first_try")
    if not first:
        return "not recorded", "not recorded"
    scenes, passed, clips = first["scenes"], first["first_passed"], first["clips"]
    if not scenes:
        return "0/0 (no scenes)", "n/a"
    return f"{passed}/{scenes} ({100 * passed / scenes:.1f}%)", f"{clips / scenes:.2f}"


def _cost(raw: dict[str, Any]) -> str:
    return f"${raw['cost_usd']:.2f}" if "cost_usd" in raw else "not recorded"


def _graded_item(suffix: str) -> tuple[str, str] | None:
    if suffix in ("page", "plan", "ad"):
        return suffix, suffix
    if match := re.fullmatch(r"(s[1-9]\d*)__(picture|clip)", suffix):
        return ("pictures" if match[2] == "picture" else "clips"), match[1]
    if re.fullmatch(r"note[1-9]\d*", suffix):
        return "note", suffix
    return None


def _unfiled_failures(
    run: str,
    scenario: str,
    grades: dict[str, Any],
    human: dict[tuple[str, str, str], list[ItemAnswer]],
    stages: dict[str, str],
) -> list[str]:
    unfiled = []
    prefix = f"{scenario}__"
    for doc_id, grade in sorted(grades.items()):
        if not doc_id.startswith(prefix) or grade.get("grade") != "fail":
            continue
        suffix = doc_id.removeprefix(prefix)
        if not (graded_item := _graded_item(suffix)):
            continue  # No-worse and photo labels aren't case-producing taps.
        stage, item = graded_item
        filed = any(
            answer.item == item and answer.verdict == "fail" and answer.reason == grade.get("note")
            for (source_run, source_scenario, question), answers in human.items()
            if source_run == run and source_scenario == scenario and stages.get(question) == stage
            for answer in answers
        )
        if not filed:
            unfiled.append(suffix.replace("__", " "))
    return unfiled


def report(store: Store, run: str, *, cases_dir: Path = FOLDER) -> Report:
    """Print and save the comparison; reporting a second time keeps the same intentions."""
    record = store.read_run(run)
    if not record:
        raise ValueError(f"No saved run named {run!r}")
    scenarios = record["scenarios"]
    if not scenarios:
        raise ValueError(f"Run {run!r} has no scenarios")
    intentions = _intentions(store, run, record["started_at"], scenarios)
    baselines = store.read_baselines()
    human = _case_answers(cases_dir)
    stages = {question.id: str(question.stage) for question in all_questions(cases_dir=cases_dir)}
    graded = (store.run_dir(run) / "grades.json").is_file()
    grades = store.read_grades(run)
    lines = [f"# Regression report: {run}", ""]
    if not graded:
        lines.extend(["Julian has not graded this run; code answers only.", ""])
    failures: list[str] = []
    unfiled_scenarios: list[str] = []
    broke = fixed = intended = unresolved = first_runs = 0
    old_cost = new_cost = 0.0
    for scenario in scenarios:
        lines.extend([f"## {scenario}", ""])
        baseline = baselines.get(scenario)
        current = store.read_answers(run, scenario)
        old = store.read_answers(baseline, scenario) if baseline else {}
        new_cost += current.get("cost_usd", 0)
        old_cost += old.get("cost_usd", 0)
        if not baseline:
            first_runs += 1
            lines.append("first run, becomes the baseline when promoted")
        else:
            lines.append(f"Baseline: {baseline}")
            before = _answers_for(
                baseline,
                scenario,
                old,
                human,
                graded=(store.run_dir(baseline) / "grades.json").is_file(),
            )
            after = _answers_for(run, scenario, current, human, graded=graded)
            for question in sorted(before.keys() | after.keys()):
                old_items, new_items = before.get(question, []), after.get(question, [])
                change = compare_items(
                    [item.verdict for item in old_items], [item.verdict for item in new_items]
                )
                reasons = intentions.get((scenario, question), [])
                if reasons:
                    intended += 1
                    lines.append(f"- intended: {question}: {'; '.join(reasons)}")
                elif change == "broke":
                    broke += 1
                    failures.append(f"{scenario}: {question}")
                    lines.append(f"- broke: {question}")
                elif change == "got fixed":
                    fixed += 1
                    lines.append(f"- got fixed: {question}")
                elif change == "unresolved":
                    unresolved += 1
                    lines.append(f"- unresolved: {question}")
                else:
                    continue
                lines.append(f"  old: {_description(old_items)}; new: {_description(new_items)}")
        unfiled = _unfiled_failures(run, scenario, grades, human, stages)
        if unfiled:
            unfiled_scenarios.append(scenario)
            for item in unfiled:
                failures.append(f"{scenario}: unfiled grade {item}")
                lines.append(f"Pending filing: {item}; run file-grades with Julian's words.")
        no_worse = grades.get(f"{scenario}__no-worse", {})
        verdict = no_worse.get("grade") or "pass"
        lines.append(
            f"New is no worse: {verdict}"
            + (f" ({no_worse['note']})" if no_worse.get("note") else "")
        )
        if baseline and verdict == "fail":
            failures.append(f"{scenario}: no worse")
        before_rate, before_tries = _first_try(old)
        after_rate, after_tries = _first_try(current)
        lines.extend(
            [
                f"First try old: {before_rate}; new: {after_rate}",
                f"clips per scene old: {before_tries}; new: {after_tries}",
                f"cost old: {_cost(old)}; new: {_cost(current)}",
                "",
            ]
        )
    lines.extend(
        [
            f"Totals: {len(scenarios)} scenarios; {broke} broke; {fixed} got fixed; "
            f"{intended} intended; {unresolved} unresolved; {first_runs} first runs.",
            f"Real cost old: ${old_cost:.2f}; new: ${new_cost:.2f}",
            "",
            "SHIP RULE FAILED: " + ", ".join(failures) if failures else "SHIP RULE PASSED",
        ]
    )
    text = "\n".join(lines) + "\n"
    atomic_write_text(store.run_dir(run) / "report.md", text)
    print(text, end="")
    return Report(text, tuple(failures), tuple(unfiled_scenarios))


def intend(store: Store, scenario: str, question: str, why: str) -> None:
    """Declare a change before its next run starts; reporting binds it to that run."""
    store.scenario_dir("next", scenario)  # Validate the key without creating any files.
    if question not in {item.id for item in all_questions()}:
        raise ValueError(f"Unknown eval question: {question!r}")
    if not why.strip():
        raise ValueError("An intended change needs a reason")
    intentions = store.read_intended()
    intentions.append(
        {
            "scenario": scenario,
            "question": question,
            "why": why,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    store.write_intended(intentions)
    print(f"Next run of {scenario}: {question} is intended to change: {why}")


def promote(store: Store, run: str, *, cases_dir: Path = FOLDER) -> int:
    """Preserve trusted baselines on failure, with the first graded baseline exception."""
    record = store.read_run(run)
    scenarios = record.get("scenarios", [])
    if not record or not scenarios:
        print(f"Refused promotion: no completed run named {run!r}")
        return 1
    if not (store.run_dir(run) / "grades.json").is_file():
        print("Refused promotion: Julian has not graded this run")
        return 1
    unfinished = [
        scenario
        for scenario in scenarios
        if not store.scenario_done(run, scenario) or not store.read_answers(run, scenario)
    ]
    if unfinished:
        print(f"Refused promotion: unfinished scenarios: {', '.join(unfinished)}")
        return 1
    result = report(store, run, cases_dir=cases_dir)
    baselines = store.read_baselines()
    if result.passed:
        baselines.update({scenario: run for scenario in scenarios})
        store.write_baselines(baselines)
        print(f"Promoted {run}: {', '.join(scenarios)}")
        return 0
    first = [
        scenario
        for scenario in scenarios
        if scenario not in baselines and scenario not in result.unfiled_scenarios
    ]
    if first:
        baselines.update({scenario: run for scenario in first})
        store.write_baselines(baselines)
        print(f"Promoted first baseline: {', '.join(first)}")
    print(f"Refused promotion: {', '.join(result.failures)}")
    return 1


def _run_arg(command: str, argv: Sequence[str]) -> str:
    parser = argparse.ArgumentParser(prog=f"python -m evals.regression {command}")
    parser.add_argument("run")
    return str(parser.parse_args(argv).run)


def report_main(argv: Sequence[str], *, store: Store) -> int:
    run = _run_arg("report", argv)
    try:
        return 0 if report(store, run).passed else 1
    except (OSError, ValueError) as error:
        print(f"Cannot report {run}: {error}", file=sys.stderr)
        return 1


def promote_main(argv: Sequence[str], *, store: Store) -> int:
    run = _run_arg("promote", argv)
    try:
        return promote(store, run)
    except (OSError, ValueError) as error:
        print(f"Refused promotion: {error}", file=sys.stderr)
        return 1


def intend_main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression intend")
    parser.add_argument("scenario")
    parser.add_argument("question")
    parser.add_argument("why")
    args = parser.parse_args(argv)
    try:
        intend(store, args.scenario, args.question, args.why)
    except (OSError, ValueError) as error:
        print(f"Cannot record intended change: {error}", file=sys.stderr)
        return 1
    return 0
