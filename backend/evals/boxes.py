"""The 4 boxes (issue #386): how far a check agrees with Julian's grades, per question and per
half, from the case files alone. Free: it calls no model.

    uv run python -m evals.boxes [<question> ...] [--judge <id>]

Each case lands in one box by the check's newest verdict, or the newest one from `--judge`:
both pass, both fail, a false alarm (the check fails his pass: a needless redo, money), a miss
(the check passes his fail: a bad result reaches the owner), unsure (never folded into pass or
fail), or no verdict yet. Counts, not percentages.
"""

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from evals.cases import FOLDER, HALVES, KINDS, BadCase, Case, Verdict, load, questions

# Each box's key, and how the report names it.
BOXES = {
    "both pass": "both pass",
    "both fail": "both fail",
    "false alarm": "judge fails his pass (false alarm)",
    "miss": "judge passes his fail (miss)",
    "unsure": "judge unsure",
    "no verdict": "no verdict yet",
}
# The boxes where the check and Julian don't agree, listed case by case.
DISAGREE = ("false alarm", "miss", "unsure")


@dataclass
class Half:
    counts: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, int]:
        return {box: self.counts[box] for box in BOXES}


@dataclass(frozen=True)
class Seen:
    """One case, the verdict counted for it (None when there is none) and its box."""

    case: Case
    verdict: Verdict | None
    box: str


@dataclass
class Boxes:
    halves: dict[str, Half]
    disagreements: list[Seen]
    kinds: dict[str, int]


def counted(case: Case, judge: str | None = None) -> Verdict | None:
    """The verdict a case is counted by: its newest, or the newest from `judge`."""
    said = [each for each in case.verdicts if judge is None or each.judge == judge]
    return said[-1] if said else None


def box(label: str, verdict: Verdict | None) -> str:
    if verdict is None:
        return "no verdict"
    if verdict.verdict == "unsure":
        return "unsure"
    if verdict.verdict == label:
        return "both pass" if label == "pass" else "both fail"
    return "false alarm" if label == "pass" else "miss"


def count(cases: list[Case], judge: str | None = None) -> Boxes:
    boxes = Boxes({half: Half() for half in HALVES}, [], {kind: 0 for kind in KINDS})
    for case in cases:
        verdict = counted(case, judge)
        seen = Seen(case, verdict, box(case.label, verdict))
        boxes.halves[case.half].counts[seen.box] += 1
        boxes.kinds[case.kind] += 1
        if seen.box in DISAGREE:
            boxes.disagreements.append(seen)
    return boxes


def report(question: str, boxes: Boxes, judge: str | None) -> str:
    lines = [f"== {question}" + (f" (judge {judge})" if judge else " (newest verdicts)")]
    width = max(len(name) for name in BOXES.values())
    lines.append(f"  {'':<{width}}  " + "  ".join(f"{half:>5}" for half in HALVES))
    for key, name in BOXES.items():
        lines.append(
            f"  {name:<{width}}  "
            + "  ".join(f"{boxes.halves[half].counts[key]:>5}" for half in HALVES)
        )
    lines.append("  " + ", ".join(f"{kind} {n}" for kind, n in boxes.kinds.items()))
    if boxes.disagreements:
        lines.append("  disagreements:")
    for seen in boxes.disagreements:
        case, verdict = seen.case, seen.verdict
        assert verdict is not None
        lines.append(f"  - {case.id} [{case.half}] {BOXES[seen.box]}")
        # A planted case's label is how it was made, not Julian's grade.
        labelled_by = "Planted" if case.source.get("from") == "planted" else "Julian"
        lines.append(
            f"      {labelled_by}: {case.label}" + (f": {case.reason}" if case.reason else "")
        )
        lines.append(f"      judge {verdict.judge}: {verdict.verdict}: {verdict.reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.boxes", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "questions", nargs="*", help="questions to count (default: every case file)"
    )
    parser.add_argument("--judge", help="count only this judge's verdicts (a verdict's `judge`)")
    parser.add_argument("--cases", type=Path, default=FOLDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    known = questions(args.cases)
    if unknown := [name for name in args.questions if name not in known]:
        print(f"no case file for {', '.join(unknown)} in {args.cases}", file=sys.stderr)
        return 2
    asked = args.questions or known
    if not asked:
        print(f"no case files yet in {args.cases}")
        return 0
    try:
        loaded = {name: load(args.cases / f"{name}.jsonl") for name in asked}
    except BadCase as wrong:
        print(f"refused: {wrong}", file=sys.stderr)
        return 2
    print(
        "\n\n".join(
            report(name, count(cases, args.judge), args.judge) for name, cases in loaded.items()
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
