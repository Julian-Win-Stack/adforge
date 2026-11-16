"""Eval judge prototype (spec #644). From backend/:

    uv run python -m evals.judge_prototype --split tune [--questions P1,C5] [--limit N] [--fake]
    uv run python -m evals.judge_prototype --split tune --score
    uv run python -m evals.judge_prototype --counts --results THIS.jsonl [--last LAST.jsonl]

Run: asks each chosen question (default: the spec's 11) of each item of the split it applies
to, one call per question, and appends one line per question and item to the results file. A
fail is asked REASKS more times and counts only when most of the asks fail (Julian, Nov 16).
Resumes: an item already answered by the same judge id (question + prompt hash) is not asked
again; a fail answered by an older run that asked only once is asked the REASKS more times.
--fake answers with the gateway's fake model and prints what each call would be sent (free).

Score: per question and per item (an item fails when any question fails) against Julian's
labels, with counts, recall, precision, kappa and the trust bar, then every disagreement.
Scores the judge ids of the prompt files as they are now, unless --judge names others.

Counts: flaws per question, last run then this run ("clip: hand: 3 clips, then 1"), with the
items failed this run (Julian, Nov 16). Only answers by the current judge ids count, so a
question changed or added since the last run must be asked on the last run's items first.
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
# A fail is asked this many more times and counts only when most of the asks fail (Julian,
# Nov 16): one-off false alarms drop out. Regression-3's 3 such alarms passed every re-ask.
REASKS = 2
_write = threading.Lock()


def _rows(results: Path) -> list[dict[str, Any]]:
    if not results.exists():
        return []
    return [
        json.loads(text)
        for text in results.read_text(encoding="utf-8").splitlines()
        if text.strip()
    ]


def _done(results: Path) -> tuple[set[tuple[str, str]], dict[tuple[str, str], dict[str, Any]]]:
    """What's answered, and the fails an older run asked only once (to be asked again)."""
    done: set[tuple[str, str]] = set()
    asked_once: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _rows(results):
        if not row.get("verdict"):
            continue
        key = (row["item"], row["judge"])
        if row["verdict"] == "fail" and not row.get("asks"):
            asked_once[key] = row
            done.discard(key)
        else:
            done.add(key)
            asked_once.pop(key, None)
    return done, asked_once


def _append(results: Path, row: dict[str, Any]) -> None:
    with _write:
        results.parent.mkdir(parents=True, exist_ok=True)
        with results.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


ANSWER_FIELDS = ("sees", "when", "on_what", "verdict")


def _answer(asked: Any) -> dict[str, Any]:
    """One ask's answer (or the error that stopped it) as a plain dict."""
    answer = asked.answer
    on_what = ""
    if answer is not None:
        # The hand form has no on_what: its problem is always on the hand.
        on_what = getattr(answer, "on_what", None) or (
            "hand" if answer.verdict == "fail" else "nothing"
        )
    return {
        "model": asked.model,
        "sees": answer.sees if answer else "",
        "when": answer.when if answer else "",
        "on_what": on_what,
        "verdict": answer.verdict if answer else None,
        "form": {k: v for k, v in answer.model_dump().items() if k not in ANSWER_FIELDS}
        if answer
        else {},
        "input_tokens": asked.input_tokens,
        "cached_tokens": asked.cached_tokens,
        "cache_write_tokens": asked.cache_write_tokens,
        "output_tokens": asked.output_tokens,
        "cost_usd": float(asked.cost_usd),
        "pictures": asked.pictures,
        "error": asked.error,
    }


def _earlier(row: dict[str, Any]) -> dict[str, Any]:
    """An older run's single fail, as the first of its asks (its cost was counted then)."""
    return {
        **{k: row.get(k, "") for k in ("model", "sees", "when", "on_what", "verdict", "error")},
        "form": row.get("form") or {},
        "pictures": row.get("pictures", 0),
        "earlier": True,
    }


def _counted(answers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The answer that counts: a single ask stands alone; when a fail was asked again, fail
    counts only when most asks fail, else pass (or doesn't apply). None: errors left it open."""
    if len(answers) == 1:
        return answers[0] if answers[0]["verdict"] else None
    most = len(answers) // 2 + 1
    fails = [a for a in answers if a["verdict"] == "fail"]
    rest = [a for a in answers if a["verdict"] in ("pass", "doesnt_apply")]
    if len(fails) >= most:
        return fails[0]
    if len(rest) >= most:
        return next((a for a in rest if a["verdict"] == "pass"), rest[0])
    return None


def _judge_item(
    item: cases_module.Item,
    questions: list[Question],
    results: Path,
    fake: bool,
    asked_once: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    from evals.judge_prototype.ask import Shown, ask, shown_for

    rows = []
    # One cut per kind of input: questions shown the same pictures share them (prompt cache).
    shown_by: dict[tuple[str, bool], Shown] = {}
    for question in questions:
        presenter = question.purpose == "eval_judge_presenter"
        try:
            if not presenter and question.inputs not in shown_by:
                shown_by[question.inputs] = shown_for(item, question)
        except Exception as error:  # noqa: BLE001 - shown, never a pass
            row = _row(item, question, [], error=f"inputs: {type(error).__name__}: {error}")
            _append(results, row)
            rows.append(row)
            continue
        shown = Shown() if presenter else shown_by[question.inputs]
        if fake:
            if presenter:
                print(
                    f"  [fake] {item.name} {question.id}: {len(item.talking_clips)} talking clips"
                    " on Gemini with sound"
                )
            else:
                labels = [image.label for image in shown.images]
                print(
                    f"  [fake] {item.name} {question.id}: {len(labels)} pictures"
                    f" ({labels[0]} ... {labels[-1]}) | shown: {shown.said}"
                )
        earlier = asked_once.get((item.key, judge_id(question)))
        if earlier:
            answers = [_earlier(earlier)] + [
                _answer(ask(question, item, shown)) for _ in range(REASKS)
            ]
        else:
            answers = [_answer(ask(question, item, shown))]
            if answers[0]["verdict"] == "fail":
                answers += [_answer(ask(question, item, shown)) for _ in range(REASKS)]
        row = _row(item, question, answers)
        _append(results, row)
        rows.append(row)
        verdict = row["verdict"] or f"ERROR {row['error'][:120]}"
        tally = (
            " (asks: " + ", ".join(str(a["verdict"]) for a in row["asks"]) + ")"
            if row["asks"]
            else ""
        )
        print(
            f"{item.name} {question.id}: {verdict}{tally} (${row['cost_usd']:.4f},"
            f" cached {row['cached_tokens']}/{row['input_tokens']})"
        )
    return rows


def _row(
    item: cases_module.Item, question: Question, answers: list[dict[str, Any]], error: str = ""
) -> dict[str, Any]:
    counted = _counted(answers) if answers else None
    fresh = [a for a in answers if not a.get("earlier")]
    if counted is None and not error:
        error = "; ".join(a["error"] for a in answers if a["error"]) or "asks disagree, errors"
    return {
        "item": item.key,
        "split": item.split,
        "level": item.level,
        "scenario": item.scenario,
        "name": item.name,
        "question": question.id,
        "judge": judge_id(question),
        "model": answers[0]["model"] if answers else "",
        "sees": counted["sees"] if counted else "",
        "when": counted["when"] if counted else "",
        "on_what": counted["on_what"] if counted else "",
        "verdict": counted["verdict"] if counted else None,
        "form": counted["form"] if counted else {},
        # Every ask when a fail was asked again, oldest first.
        "asks": [{k: a.get(k, "") for k in ("verdict", "sees", "when", "error")} for a in answers]
        if len(answers) > 1
        else [],
        "input_tokens": sum(a["input_tokens"] for a in fresh),
        "cached_tokens": sum(a["cached_tokens"] for a in fresh),
        "cache_write_tokens": sum(a["cache_write_tokens"] for a in fresh),
        "output_tokens": sum(a["output_tokens"] for a in fresh),
        "cost_usd": sum(a["cost_usd"] for a in fresh),
        "pictures": answers[0]["pictures"] if answers else 0,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error": "" if counted else error,
    }


def run(
    items: list[cases_module.Item], questions: list[Question], results: Path, fake: bool
) -> int:
    done, asked_once = _done(results)
    work = []
    for item in items:
        todo = [q for q in questions if applies(q, item) and (item.key, judge_id(q)) not in done]
        if todo:
            work.append((item, todo))
    again = sum(1 for item, todo in work for q in todo if (item.key, judge_id(q)) in asked_once)
    print(
        f"{len(work)} items to judge, {sum(len(t) for _, t in work)} questions"
        f" ({again} of them earlier fails asked {REASKS} more times)."
    )
    with ThreadPoolExecutor(AT_ONCE) as pool:
        rows = [
            r
            for rs in pool.map(lambda w: _judge_item(w[0], w[1], results, fake, asked_once), work)
            for r in rs
        ]
    cost = sum(Decimal(str(r["cost_usd"])) for r in rows)
    errors = [r for r in rows if r["error"]]
    print(
        f"Done: {len(rows)} answers, {len(errors)} errors, ${cost:.2f}"
        " (cached input billed at 10%, assumed)."
    )
    for r in errors:
        print(f"  NOT JUDGED {r['name']} {r['question']}: {r['error']}", file=sys.stderr)
    return 1 if errors else 0


UNIT = {"picture": "pictures", "clip": "clips", "ad": "ads"}


def counts(questions: list[Question], results: Path, last: Path | None) -> int:
    """Flaws per question, last run then this run, by the current judge ids, with the ads
    behind each change (Julian, Nov 16): ads whose flaw went away, ads with a new one, and ads
    that have it in both runs. Ads are matched by scenario, since a re-made ad's pictures and
    clips are new files."""
    ids = {q.id: judge_id(q) for q in questions}

    def newest(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
        kept: dict[tuple[str, str], dict[str, Any]] = {}
        for row in _rows(path):
            if row.get("verdict") and ids.get(row["question"]) == row["judge"]:
                kept[(row["item"], row["question"])] = row
        return kept

    def applied(rows: list[dict[str, Any]]) -> int:
        return sum(1 for r in rows if r["verdict"] != "doesnt_apply")

    def failing(rows: list[dict[str, Any]]) -> set[str]:
        return {r["scenario"] or r["name"] for r in rows if r["verdict"] == "fail"}

    def names(ads: set[str]) -> str:
        return ", ".join(sorted(ads)) or "none"

    this = newest(results)
    before = newest(last) if last else {}
    print("Judges counted: " + ", ".join(ids.values()))
    print(f"This run: {results}" + (f"\nLast run: {last}" if last else ""))
    for q in questions:
        now = [r for (_, qid), r in this.items() if qid == q.id]
        was = [r for (_, qid), r in before.items() if qid == q.id]
        fails, fails_was = (
            sum(r["verdict"] == "fail" for r in now),
            sum(r["verdict"] == "fail" for r in was),
        )
        unit = UNIT[q.level]
        once = sum(1 for r in now if r["verdict"] == "fail" and not r.get("asks"))
        if not now:
            head = f"{q.name}: not asked this run"
        elif not last:
            head = f"{q.name}: {fails} of {applied(now)} {unit}"
        elif not was:
            head = f"{q.name}: not asked on the last run; this run {fails} of {applied(now)} {unit}"
        else:
            ads, ads_was = failing(now), failing(was)
            head = (
                f"{q.name}: {fails_was} of {applied(was)} {unit}, then {fails} of {applied(now)}"
                f" (gone: {names(ads_was - ads)}; new: {names(ads - ads_was)};"
                f" still: {names(ads & ads_was)})"
            )
        print(f"\n{head}" + (f"  [{once} fails asked only once]" if once else ""))
        for r in sorted(now, key=lambda r: r["name"]):
            if r["verdict"] == "fail":
                print(f"  - {r['name']} ({r['on_what']}, {r['when'] or '-'}): {r['sees'][:240]}")
    return 0


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
                    f"- {kind} {q.id} {item.name}\n"
                    f"    Julian: {julian}; {item.notes() or '(no words)'}\n"
                    f"    Judge ({row['verdict']}, {row['on_what']}, {row['when'] or '-'}):"
                    f" {row['sees']}"
                )
        print(
            "  "
            + line(q.id, counted(pairs))
            + f" | doesn't apply {doesnt_apply}, not judged {unjudged},"
            + f" no question label {no_label}"
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
                f"- MISS (item, no question) {item.name}\n"
                f"    Julian: fail; {item.notes() or '(no words)'}"
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
    parser.add_argument(
        "--questions", help="comma-separated ids, e.g. P1,C5 (default: the spec's 11)"
    )
    parser.add_argument("--split", choices=["tune", "score"])
    parser.add_argument("--limit", type=int, help="only the first N items of the split")
    parser.add_argument("--cases", type=Path, default=cases_module.CASES)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--score", action="store_true", help="score instead of run")
    parser.add_argument(
        "--judge", nargs="*", default=[], help="score these judge ids instead of the current ones"
    )
    parser.add_argument("--fake", action="store_true", help="fake model, free")
    parser.add_argument(
        "--counts", action="store_true", help="flaws per question, last run then this run"
    )
    parser.add_argument("--last", type=Path, help="with --counts: the last run's results file")
    args = parser.parse_args(argv)
    questions = parse(args.questions)
    if args.counts:
        return counts(questions, args.results, args.last)
    if not args.split:
        parser.error("--split is needed to run or score")
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
                    **(
                        {"photo_sides": [], "shown_sides": []}
                        if "photo_sides" in request.output.model_fields
                        else {}
                    ),
                    **({"rows": []} if "rows" in request.output.model_fields else {}),
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
