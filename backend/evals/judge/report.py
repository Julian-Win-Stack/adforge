"""Per-question flaw counts, kept separate from regression ship decisions (#680)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.cases import load
from evals.regression.questions import Question, judge_questions
from evals.regression.store import Store

from . import trust
from .records import answer_cost, parse_tap_doc_id, tap_doc_id, tap_verdict

type ItemKey = tuple[str, str]


@dataclass(frozen=True)
class ItemAnswer:
    verdict: str
    source: str
    dont_ship: bool = False


@dataclass(frozen=True)
class HumanLabel:
    verdict: str
    tap: str = ""
    corrected_at: str = ""


@dataclass(frozen=True)
class JudgeSection:
    lines: tuple[str, ...]
    failures: tuple[str, ...]
    broke: int
    fixed: int
    cost: float


def _labels(cases_dir: Path) -> dict[tuple[str, str, str, str], HumanLabel]:
    labels = {}
    for question in judge_questions():
        path = cases_dir / f"{question.id}.jsonl"
        if not path.is_file():
            continue
        for case in load(path):
            item = str(case.inputs["handoff"]["item"])
            labels[case.source["round"], case.source["ad"], question.id, item] = HumanLabel(
                case.label, case.source.get("tap", ""), case.source.get("corrected_at", "")
            )
    return labels


def _correction_wins(label: HumanLabel, tap: Mapping[str, Any]) -> bool:
    if not label.corrected_at:
        return False
    if not tap["at"]:
        return True
    corrected = datetime.fromisoformat(label.corrected_at)
    tapped = datetime.fromisoformat(tap["at"])
    if corrected.tzinfo is None or tapped.tzinfo is None:
        raise ValueError("Judge correction and tap timestamps need a timezone")
    return corrected >= tapped


def _items(
    run: str,
    scenario: str,
    question: Question,
    answers: Mapping[str, Any],
    taps: Mapping[str, Any],
    labels: Mapping[tuple[str, str, str, str], HumanLabel],
) -> dict[ItemKey, ItemAnswer]:
    raw = answers.get(scenario, {}).get(question.id, {})
    items = set(raw)
    items.update(
        item
        for source_run, source_scenario, source_question, item in labels
        if (source_run, source_scenario, source_question) == (run, scenario, question.id)
    )
    for doc_id in taps:
        parsed = parse_tap_doc_id(doc_id)
        if parsed and (parsed[0], parsed[1], parsed[3]) == (
            scenario,
            question.stage,
            question.id,
        ):
            items.add(parsed[2])
    selected = {}
    for item in items:
        tap = taps.get(tap_doc_id(scenario, question.stage, item, question.id))
        label = labels.get((run, scenario, question.id, item))
        dont_ship = False
        if label is not None and (not tap or _correction_wins(label, tap)):
            verdict, source = label.verdict, "Julian"
            dont_ship = verdict == "fail" and label.tap == "real_flaw_dont_ship"
        elif tap:
            verdict = tap_verdict(tap)
            source = "Julian"
            dont_ship = tap["tap"] == "real_flaw_dont_ship"
        else:
            verdict, source = raw[item].get("verdict"), "judge"
        if verdict in ("pass", "fail"):
            selected[scenario, item] = ItemAnswer(verdict, source, dont_ship)
    return selected


def _count(items: Mapping[ItemKey, ItemAnswer], unit: str) -> str:
    return f"{sum(item.verdict == 'fail' for item in items.values())} of {len(items)} {unit}"


def _names(keys: set[ItemKey], items: Mapping[ItemKey, ItemAnswer]) -> str:
    return (
        ", ".join(
            f"{scenario} {item} ({items[scenario, item].source})" for scenario, item in sorted(keys)
        )
        or "none"
    )


def build_section(
    store: Store,
    run: str,
    scenarios: Sequence[str],
    baselines: Mapping[str, str],
    *,
    cases_dir: Path,
) -> JudgeSection | None:
    current = store.read_judge_answers(run)
    current_taps = store.read_judge_taps(run)
    if not current and not current_taps:
        return None
    labels = _labels(cases_dir)
    trusted = trust.trusted_questions(cases_dir=cases_dir)
    baseline_answers = {
        baseline: store.read_judge_answers(baseline)
        for baseline in {baselines[scenario] for scenario in scenarios if scenario in baselines}
    }
    baseline_taps = {
        baseline: store.read_judge_taps(baseline)
        for baseline in {baselines[scenario] for scenario in scenarios if scenario in baselines}
    }
    lines = ["## Eval judge: flaws against the baseline", ""]
    failures: list[str] = []
    broke = fixed = 0
    for question in judge_questions():
        before: dict[ItemKey, ItemAnswer] = {}
        after: dict[ItemKey, ItemAnswer] = {}
        missing = []
        comparable = set()
        versions = {
            answer["judge"]
            for scenario in scenarios
            for answer in current.get(scenario, {}).get(question.id, {}).values()
        }
        if len(versions) > 1:
            missing.append(
                f"{question.id} has multiple judge versions on this run; "
                f"run python -m evals.regression judge {run}"
            )
        for scenario in scenarios:
            after.update(_items(run, scenario, question, current, current_taps, labels))
            baseline = baselines.get(scenario)
            if baseline:
                raw = baseline_answers[baseline]
                old_items = raw.get(scenario, {}).get(question.id, {})
                current_items = current.get(scenario, {}).get(question.id, {})
                old_versions = {answer["judge"] for answer in old_items.values()}
                stale = bool(versions and old_versions and versions != old_versions)
                if old_items and current_items and not stale and len(versions) == 1:
                    comparable.add(scenario)
                if not old_items:
                    missing.append(
                        f"{scenario}: {question.id} not judged on baseline {baseline}; "
                        f"run python -m evals.regression judge {baseline}"
                    )
                elif stale:
                    missing.append(
                        f"{scenario}: {question.id} baseline {baseline} uses a different judge "
                        f"version; run python -m evals.regression judge {baseline}"
                    )
                if old_items and not current_items:
                    missing.append(
                        f"{scenario}: {question.id} not judged on this run; "
                        f"run python -m evals.regression judge {run}"
                    )
                if not stale:
                    before.update(
                        _items(baseline, scenario, question, raw, baseline_taps[baseline], labels)
                    )
            else:
                missing.append(f"{scenario}: no baseline for {question.id}")
        status = "trusted" if question.id in trusted else "not trusted yet"
        failures.extend(
            f"{scenario} {item} {question.id}: Julian says real flaw, don't ship"
            for (scenario, item), answer in after.items()
            if answer.dont_ship
        )
        lines.append(
            f"- {question.id} {question.words}: {_count(before, question.unit)}, "
            f"then {_count(after, question.unit)} ({status})"
        )
        old_flaws = {key for key, answer in before.items() if answer.verdict == "fail"}
        new_flaws = {key for key, answer in after.items() if answer.verdict == "fail"}
        matched = {key for key in before.keys() & after.keys() if key[0] in comparable}
        gone = (old_flaws - new_flaws) & matched
        new = (new_flaws - old_flaws) & matched
        lines.extend(
            [
                f"  gone: {_names(gone, {**before, **after})}",
                f"  new: {_names(new, after)}",
                f"  still: {_names(old_flaws & new_flaws, after)}",
            ]
        )
        lines.extend(f"  {message}" for message in missing)
        if question.id in trusted and not missing:
            old_count, new_count = len(old_flaws & matched), len(new_flaws & matched)
            broke += new_count > old_count
            fixed += new_count < old_count
    unjudged = []
    untapped = []
    cost = 0.0
    questions = {question.id: question for question in judge_questions()}
    for scenario in scenarios:
        for question_id, items in current.get(scenario, {}).items():
            question = questions[question_id]
            for item, answer in items.items():
                name = f"{scenario} {item} {question_id}"
                cost += answer_cost(answer)
                if answer.get("error") or answer.get("verdict") is None:
                    unjudged.append(f"{name}: {answer.get('error') or 'asks incomplete'}")
                doc_id = tap_doc_id(scenario, question.stage, item, question_id)
                tap = current_taps.get(doc_id)
                if answer.get("verdict") == "fail" and not tap:
                    untapped.append(name)
    lines.extend(
        [
            "",
            "Not judged: " + ("; ".join(unjudged) or "none"),
            "Waiting for Julian's tap: " + ("; ".join(untapped) or "none"),
            f"Judge cost this run: ${cost:.2f}",
            "",
        ]
    )
    return JudgeSection(tuple(lines), tuple(failures), broke, fixed, cost)
