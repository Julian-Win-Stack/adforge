"""Per-question flaw counts, kept separate from regression ship decisions (#680)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.cases import load
from evals.regression.questions import Question, judge_questions
from evals.regression.store import Store

from . import trust
from .records import answer_cost, tap_doc_id

type ItemKey = tuple[str, str]


@dataclass(frozen=True)
class ItemAnswer:
    verdict: str
    source: str


@dataclass(frozen=True)
class JudgeSection:
    lines: tuple[str, ...]
    failures: tuple[str, ...]
    broke: int
    fixed: int
    cost: float


def _labels(cases_dir: Path) -> dict[tuple[str, str, str, str], str]:
    labels = {}
    for question in judge_questions():
        path = cases_dir / f"{question.id}.jsonl"
        if not path.is_file():
            continue
        for case in load(path):
            item = str(case.inputs["handoff"]["item"])
            labels[case.source["round"], case.source["ad"], question.id, item] = case.label
    return labels


def _items(
    run: str,
    scenario: str,
    question: Question,
    answers: Mapping[str, Any],
    taps: Mapping[str, Any],
    labels: Mapping[tuple[str, str, str, str], str],
) -> dict[ItemKey, ItemAnswer]:
    raw = answers.get(scenario, {}).get(question.id, {})
    items = set(raw)
    items.update(
        item
        for source_run, source_scenario, source_question, item in labels
        if (source_run, source_scenario, source_question) == (run, scenario, question.id)
    )
    selected = {}
    for item in items:
        tap = taps.get(tap_doc_id(scenario, question.stage, item, question.id))
        label = labels.get((run, scenario, question.id, item))
        if tap:
            verdict = "pass" if tap["tap"] == "not_a_flaw" else "fail"
            source = "Julian"
        elif label is not None:
            verdict, source = label, "Julian"
        else:
            verdict, source = raw[item].get("verdict"), "judge"
        if verdict in ("pass", "fail"):
            selected[scenario, item] = ItemAnswer(verdict, source)
    return selected


def _unknown_items(
    scenario: str,
    question: Question,
    answers: Mapping[str, Any],
    known: Mapping[ItemKey, ItemAnswer],
) -> set[ItemKey]:
    return {
        (scenario, item)
        for item, answer in answers.get(scenario, {}).get(question.id, {}).items()
        if answer.get("verdict") not in ("pass", "fail", "doesnt_apply")
        and (scenario, item) not in known
    }


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
    if not current:
        return None
    labels = _labels(cases_dir)
    trusted = trust.trusted_questions(cases_dir=cases_dir)
    current_taps = store.read_judge_taps(run)
    baseline_answers = {
        baseline: store.read_judge_answers(baseline)
        for baseline in {baselines[scenario] for scenario in scenarios if scenario in baselines}
    }
    baseline_taps = {
        baseline: store.read_judge_taps(baseline)
        for baseline in {baselines[scenario] for scenario in scenarios if scenario in baselines}
    }
    lines = ["## Eval judge: flaws against the baseline", ""]
    failures = []
    broke = fixed = 0
    for question in judge_questions():
        before: dict[ItemKey, ItemAnswer] = {}
        after: dict[ItemKey, ItemAnswer] = {}
        missing = []
        comparable = set()
        unknown = set()
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
            unknown.update(_unknown_items(scenario, question, current, after))
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
                    unknown.update(_unknown_items(scenario, question, raw, before))
            else:
                missing.append(f"{scenario}: no baseline for {question.id}")
        status = "trusted" if question.id in trusted else "not trusted yet"
        lines.append(
            f"- {question.id} {question.words}: {_count(before, question.unit)}, "
            f"then {_count(after, question.unit)} ({status})"
        )
        old_flaws = {key for key, answer in before.items() if answer.verdict == "fail"}
        new_flaws = {key for key, answer in after.items() if answer.verdict == "fail"}
        gone = {key for key in old_flaws - new_flaws if key[0] in comparable and key not in unknown}
        new = {key for key in new_flaws - old_flaws if key[0] in comparable and key not in unknown}
        lines.extend(
            [
                f"  gone: {_names(gone, {**before, **after})}",
                f"  new: {_names(new, after)}",
                f"  still: {_names(old_flaws & new_flaws, after)}",
            ]
        )
        lines.extend(f"  {message}" for message in missing)
        if question.id in trusted and not missing and not unknown:
            broke += len(new_flaws) > len(old_flaws)
            fixed += len(new_flaws) < len(old_flaws)
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
                if tap and tap["tap"] == "real_flaw_dont_ship":
                    failures.append(f"{name}: Julian says real flaw, don't ship")
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
