"""Eval judge prototype (spec #644). From backend/:

    uv run python -m evals.judge_prototype --questions P1,C5 --split tune [--limit N] [--fake]
    uv run python -m evals.judge_prototype --questions P1,C5 --split tune --score

Run: asks each chosen question of each item of the split it applies to, one call per
question, and appends every answer to the results file. Resumes: an item already answered by
the same judge id (question + prompt hash) is not asked again. --fake answers with the
gateway's fake model and prints what each call would be sent (free).

Score: per question and per item (an item fails when any question fails) against Julian's
labels, with counts, recall, precision, kappa and the trust bar, then every disagreement.
Scores the judge ids of the prompt files as they are now, unless --judge names others.
"""

import argparse
import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.judge_prototype import cases as cases_module
from evals.judge_prototype.questions import Question, applies, judge_id, parse

RESULTS = cases_module.PROJECT_FILES / "eval-store/judge-cases/results/judge-results.jsonl"
# Items judged at once; one item's questions are asked one after another (prompt cache).
AT_ONCE = int(os.environ.get("JUDGE_AT_ONCE", "4"))
_write = threading.Lock()


def _done(results: Path) -> set[tuple[str, str]]:
    if not results.exists():
        return set()
    done = set()
    for text in results.read_text(encoding="utf-8").splitlines():
        if text.strip():
            row = json.loads(text)
            if row.get("verdict"):
                done.add((row["item"], row["judge"]))
    return done


def _append(results: Path, row: dict[str, Any]) -> None:
    with _write:
        results.parent.mkdir(parents=True, exist_ok=True)
        with results.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def _judge_item(
    item: cases_module.Item, questions: list[Question], results: Path, fake: bool
) -> list[dict[str, Any]]:
    from evals.judge_prototype.ask import ask, shown_for

    from evals.judge_prototype.ask import Shown

    rows = []
    shown: Shown | None = None
    for question in questions:
        presenter = question.purpose == "eval_judge_presenter"
        try:
            if shown is None and not presenter:
                shown = shown_for(item)
        except Exception as error:  # noqa: BLE001 - shown, never a pass
            row = _row(item, question, None, error=f"inputs: {type(error).__name__}: {error}")
            _append(results, row)
            rows.append(row)
            continue
        if fake:
            if presenter:
                print(
                    f"  [fake] {item.name} {question.id}: {len(item.talking_clips)} talking clips on Gemini with sound"
                )
            else:
                assert shown is not None
                labels = [image.label for image in shown.images]
                print(
                    f"  [fake] {item.name} {question.id}: {len(labels)} pictures ({labels[0]} ... {labels[-1]})"
                    f" | shown: {shown.said}"
                )
        asked = ask(question, item, shown or Shown())
        row = _row(item, question, asked)
        _append(results, row)
        rows.append(row)
        verdict = row["verdict"] or f"ERROR {row['error'][:120]}"
        print(
            f"{item.name} {question.id}: {verdict} (${row['cost_usd']:.4f}, cached {row['cached_tokens']}/{row['input_tokens']})"
        )
    return rows


def _row(
    item: cases_module.Item, question: Question, asked: Any, error: str = ""
) -> dict[str, Any]:
    answer = asked.answer if asked else None
    return {
        "item": item.key,
        "split": item.split,
        "level": item.level,
        "scenario": item.scenario,
        "name": item.name,
        "question": question.id,
        "judge": judge_id(question),
        "model": asked.model if asked else "",
        "sees": answer.sees if answer else "",
        "when": answer.when if answer else "",
        "on_what": answer.on_what if answer else "",
        "verdict": answer.verdict if answer else None,
        "input_tokens": asked.input_tokens if asked else 0,
        "cached_tokens": asked.cached_tokens if asked else 0,
        "cache_write_tokens": asked.cache_write_tokens if asked else 0,
        "output_tokens": asked.output_tokens if asked else 0,
        "cost_usd": float(asked.cost_usd) if asked else 0.0,
        "pictures": asked.pictures if asked else 0,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error": (asked.error if asked else "") or error,
    }


def run(
    items: list[cases_module.Item], questions: list[Question], results: Path, fake: bool
) -> int:
    done = _done(results)
    work = []
    for item in items:
        todo = [q for q in questions if applies(q, item) and (item.key, judge_id(q)) not in done]
        if todo:
            work.append((item, todo))
    print(f"{len(work)} items to judge, {sum(len(t) for _, t in work)} calls.")
    with ThreadPoolExecutor(AT_ONCE) as pool:
        rows = [
            r for rs in pool.map(lambda w: _judge_item(w[0], w[1], results, fake), work) for r in rs
        ]
    cost = sum(Decimal(str(r["cost_usd"])) for r in rows)
    errors = [r for r in rows if r["error"]]
    print(
        f"Done: {len(rows)} answers, {len(errors)} errors, ${cost:.2f} (cached input billed at 10%, assumed)."
    )
    for r in errors:
        print(f"  NOT JUDGED {r['name']} {r['question']}: {r['error']}", file=sys.stderr)
    return 1 if errors else 0


def score(
    items: list[cases_module.Item], questions: list[Question], results: Path, judges: dict[str, str]
) -> int:
    from evals.judge_prototype.score import counted, line

    newest: dict[tuple[str, str], dict[str, Any]] = {}
    if results.exists():
        for text in results.read_text(encoding="utf-8").splitlines():
            if text.strip():
                row = json.loads(text)
                if row.get("verdict") and row["judge"] == judges.get(row["question"]):
                    newest[(row["item"], row["question"])] = row
    print("Judges scored: " + ", ".join(judges[q.id] for q in questions))
    disagreements: list[str] = []
    print("\nPer question:")
    for q in questions:
        pairs, doesnt_apply, unjudged, no_label = [], 0, 0, 0
        for item in items:
            if not applies(q, item):
                continue
            julian = item.label_for(q.id)
            row = newest.get((item.key, q.id))
            if julian is None:
                no_label += 1
                continue
            if row is None:
                unjudged += 1
                continue
            verdict = row["verdict"]
            if verdict == "doesnt_apply":
                doesnt_apply += 1
                if julian == "pass":
                    continue
                verdict = "pass"  # his fail the judge said doesn't apply to: a miss
            pairs.append((julian, verdict))
            if julian != verdict:
                kind = "MISS" if julian == "fail" else "FALSE ALARM"
                disagreements.append(
                    f"- {kind} {q.id} {item.name}\n    Julian: {julian}; {item.notes() or '(no words)'}\n"
                    f"    Judge ({row['verdict']}, {row['on_what']}, {row['when'] or '-'}): {row['sees']}"
                )
        print(
            "  "
            + line(q.id, counted(pairs))
            + f" | doesn't apply {doesnt_apply}, not judged {unjudged}, no question label {no_label}"
        )
    print("\nPer item (fails when any chosen question fails):")
    by_level: dict[str, list[tuple[str, str]]] = defaultdict(list)
    skipped = 0
    for item in items:
        asked = [q for q in questions if applies(q, item)]
        if not asked:
            continue
        rows = [newest.get((item.key, q.id)) for q in asked]
        if any(r is None for r in rows):
            skipped += 1
            continue
        verdict = "fail" if any(r["verdict"] == "fail" for r in rows if r) else "pass"
        by_level[item.level].append((item.label, verdict))
        if (
            item.label == "fail"
            and verdict == "pass"
            and all(each["question"] is None for each in item.labels if each["label"] == "fail")
        ):
            disagreements.append(
                f"- MISS (item, no question) {item.name}\n    Julian: fail; {item.notes() or '(no words)'}"
            )
    for level, pairs in sorted(by_level.items()):
        print("  " + line(level, counted(pairs)))
    print(
        "  "
        + line("all", counted([p for ps in by_level.values() for p in ps]))
        + f" | items not fully judged {skipped}"
    )
    print(f"\nDisagreements ({len(disagreements)}):")
    print("\n".join(disagreements) or "  none")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.judge_prototype", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--questions", help="comma-separated ids, e.g. P1,C5 (default: all 12)")
    parser.add_argument("--split", choices=["tune", "score"], required=True)
    parser.add_argument("--limit", type=int, help="only the first N items of the split")
    parser.add_argument("--cases", type=Path, default=cases_module.CASES)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--score", action="store_true", help="score instead of run")
    parser.add_argument(
        "--judge", nargs="*", default=[], help="score these judge ids instead of the current ones"
    )
    parser.add_argument("--fake", action="store_true", help="fake model, free")
    args = parser.parse_args(argv)
    questions = parse(args.questions)
    items = [i for i in cases_module.load(args.cases) if i.split == args.split]
    items = [i for i in items if any(applies(q, i) for q in questions)]
    if args.limit:
        items = items[: args.limit]
    if args.score:
        judges = {q.id: judge_id(q) for q in questions}
        for given in args.judge:
            judges[given.split(":")[0]] = given
        return score(items, questions, args.results, judges)
    if args.fake:
        from gateway.fake import FakeModel
        from gateway.gateway import use_model

        model = FakeModel()
        for purpose in (
            "eval_judge_picture",
            "eval_judge_clip",
            "eval_judge_ad",
            "eval_judge_presenter",
        ):
            model.answer_unscripted(
                purpose,
                lambda request: {
                    "sees": "fake: nothing wrong",
                    "when": "",
                    "on_what": "nothing",
                    "verdict": "pass",
                },
            )
        with use_model(model):
            return run(items, questions, args.results, fake=True)
    return run(items, questions, args.results, fake=False)


if __name__ == "__main__":
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    # Frames and watched cuts are kept here, out of the repo and the project files.
    os.environ.setdefault("MEDIA_ROOT", str(Path.home() / ".cache/judge-prototype-media"))
    django.setup()
    sys.exit(main())
