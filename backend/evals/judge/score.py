"""Score saved eval-judge answers against Julian's grades. No model is called.

The judge's chronological split is rounds 9-15 for tuning and baseline-2 onward
for scoring. The generic case format's hash-based half remains for evals.boxes;
it does not discard half the current-run labels from this judge's score.
"""

import argparse
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from evals.boxes import counted
from evals.cases import FOLDER, BadCase, Case, Verdict, load
from evals.judge.records import ITEM_QUESTIONS
from evals.judge.scoring import Score, score
from evals.regression.questions import judge_questions


@dataclass(frozen=True)
class Result:
    score: Score
    not_judged: int = 0
    doesnt_apply: int = 0


@dataclass(frozen=True)
class Scores:
    questions: dict[str, Result]
    items: Result


def _scoring_run(case: Case) -> bool:
    name = case.source["round"].strip().lower()
    if not name or name == "baseline":
        return False
    if baseline := re.fullmatch(r"baseline-(\d+)", name):
        return int(baseline[1]) >= 2
    if old_round := re.fullmatch(r"(?:r|round[-_ ]?)?(\d+)", name):
        return int(old_round[1]) > 15
    return True


def _result(pairs: Iterable[tuple[str, str | None]]) -> Result:
    binary: list[tuple[str, str]] = []
    not_judged = doesnt_apply = 0
    for label, verdict in pairs:
        if verdict == "doesnt_apply":
            doesnt_apply += 1
        elif verdict not in ("pass", "fail"):
            not_judged += 1
        else:
            binary.append((label, verdict))
    return Result(score(binary), not_judged, doesnt_apply)


def _item_key(case: Case, stage: str) -> tuple[str, str, str, str]:
    handoff = case.inputs["handoff"]
    if handoff.get("stage") != stage:
        raise ValueError(f"Judge case {case.id}: expected stage {stage!r}")
    item = handoff.get("item")
    valid = (
        item == "ad"
        if stage == "ad"
        else isinstance(item, str) and re.fullmatch(r"s[1-9]\d*", item)
    )
    if not valid:
        raise ValueError(f"Judge case {case.id}: invalid item {item!r} at {stage!r}")
    return case.source["round"], case.source["ad"], stage, str(item)


def _item_result(
    rows: dict[str, tuple[Case, Verdict | None]], stage: str
) -> tuple[str, str | None]:
    expected = {q.id for q in judge_questions() if q.stage == stage}
    # The judge's applicability answer cannot change Julian's independent label.
    labels = [case.label for case, _ in rows.values()]
    # The item-only case gives Julian's overall label without guessing its question.
    # Otherwise a known fail suffices, but a pass needs all question labels.
    if "fail" in labels:
        label = "fail"
    elif ITEM_QUESTIONS[stage] in rows or expected <= rows.keys():
        label = "pass"
    else:
        return "pass", None
    verdicts = {
        question: verdict.verdict if verdict else None
        for question, (_, verdict) in rows.items()
        if question in expected
    }
    if "fail" in verdicts.values():
        return label, "fail"
    if not expected <= verdicts.keys() or any(
        verdict not in ("pass", "doesnt_apply") for verdict in verdicts.values()
    ):
        return label, None
    if "pass" in verdicts.values():
        return label, "pass"
    return label, "doesnt_apply"


def read_scores(*, cases_dir: Path = FOLDER, judge: str | None = None) -> Scores:
    """Read judge case files once, using newest verdicts or one exact judge id.

    Only evidence on an applicable question enters agreement. Missing/unsure
    answers remain unjudged. At item level, any known fail suffices; a pass needs
    all the stage's questions answered (pass or doesn't apply), and at least one
    applicable answer. Ad questions have their own score, not an item roll-up.
    """
    questions = judge_questions()
    results: dict[str, Result] = {}
    items: dict[tuple[str, str, str, str], dict[str, tuple[Case, Verdict | None]]] = defaultdict(
        dict
    )
    for question in questions:
        path = cases_dir / f"{question.id}.jsonl"
        cases = [case for case in load(path) if _scoring_run(case)] if path.is_file() else []
        pairs = []
        for case in cases:
            key = _item_key(case, question.stage)
            verdict = counted(case, judge)
            pairs.append((case.label, verdict.verdict if verdict else None))
            if question.stage in ITEM_QUESTIONS:
                items[key][question.id] = (case, verdict)
        results[question.id] = _result(pairs)
    for stage, question_id in ITEM_QUESTIONS.items():
        path = cases_dir / f"{question_id}.jsonl"
        if path.is_file():
            for case in load(path):
                if _scoring_run(case):
                    items[_item_key(case, stage)][question_id] = (case, None)
    item_pairs = [_item_result(rows, key[2]) for key, rows in items.items()]
    return Scores(results, _result(item_pairs))


def _percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _line(name: str, result: Result, *, trust: bool, words: str = "") -> str:
    scored = result.score
    kappa = "n/a" if scored.kappa is None else f"{scored.kappa:.3f} ({_percentage(scored.kappa)})"
    description = f"{words}; " if words else ""
    line = (
        f"{name}: {description}recall caught {scored.caught} of {scored.caught + scored.missed} "
        f"({_percentage(scored.recall)}); precision real {scored.caught} of "
        f"{scored.caught + scored.false_alarms} ({_percentage(scored.precision)}); "
        f"agreement {scored.agreed} of {scored.total}; kappa {kappa}; "
        f"not judged {result.not_judged}; doesn't apply {result.doesnt_apply}"
    )
    if trust:
        line += "; trusted" if scored.trusted else "; not trusted yet"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.judge.score", description=__doc__)
    parser.add_argument("--judge", help="Score only this exact judge id, like evals.boxes")
    parser.add_argument("--cases", type=Path, default=FOLDER, help="Case folder")
    args = parser.parse_args(argv)
    try:
        scores = read_scores(cases_dir=args.cases, judge=args.judge)
    except (BadCase, ValueError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print("Eval judge: baseline-2 onward, all current-run labels (old rounds tune only).")
    print(f"Judge: {args.judge}" if args.judge else "Judge: newest verdict per case")
    for question in judge_questions():
        print(_line(question.id, scores.questions[question.id], trust=True, words=question.words))
    print(_line("items", scores.items, trust=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
