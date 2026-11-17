"""Explicitly ask the eval judge on a finished run or saved cases; never automatic."""

import argparse
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from evals.regression.items import GradedItem, graded_items
from evals.regression.questions import Question, judge_questions
from evals.regression.store import Store

from .ask import Shown, ask_question, complete, failed_answer, show, start_answer
from .inputs import JudgeInput, inputs_for_run_item
from .records import JudgeAnswer, answer_cost

RUN_STAGES = ("pictures", "clips", "ad")


def main(argv: Sequence[str] | None = None, *, store: Store | None = None) -> int:
    import django
    from django.apps import apps

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    if not apps.ready:
        django.setup()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?")
    from evals.cases import FOLDER

    parser.add_argument(
        "--cases", nargs="?", const=FOLDER, type=Path, help="Judge saved case files"
    )
    supported = [q.id for q in judge_questions() if q.stage in RUN_STAGES]
    parser.add_argument("--questions", nargs="+", choices=supported)
    parser.add_argument("--ids", nargs="+", help="Limit saved-case mode to these case ids")
    args = parser.parse_args(argv)
    from adforge import monitoring
    from adforge.paid_runs import missing_keys
    from evals.regression.preflight import refuse
    from jobs import notices

    if refuse(missing := missing_keys("judge")):
        monitoring.failed(notices.EVAL_JUDGE_UNAVAILABLE.name, "; ".join(missing))
        return 1
    if args.cases is not None:
        if args.run is not None:
            parser.error("Choose a run or --cases, not both")
        from .saved_cases import main as judge_cases

        return judge_cases(args.cases, questions=args.questions, ids=args.ids)
    if args.run is None:
        parser.error("Choose a run or --cases")
    if args.ids is not None:
        parser.error("--ids requires --cases")
    if store is None:
        store = Store()
    record = store.read_run(args.run)
    if not record or not record.get("finished_at"):
        print(f"{args.run}: choose a finished saved run", file=sys.stderr)
        return 1
    answers = store.read_judge_answers(args.run)
    lock = threading.Lock()
    try:
        items = [
            (scenario, item)
            for scenario in record["scenarios"]
            for item in graded_items(store, args.run, scenario)
            if item.stage in RUN_STAGES
        ]
    except (OSError, ValueError) as error:
        from adforge import monitoring
        from jobs import notices

        monitoring.failed(notices.EVAL_JUDGE_UNAVAILABLE.name, str(error), run=args.run)
        print(f"{args.run}: not judged: {error}", file=sys.stderr)
        return 1
    before = judge_cost(answers)

    def judge_item(entry: tuple[str, GradedItem]) -> bool:
        scenario, item = entry
        stored = answers.setdefault(scenario, {})
        questions = [
            question
            for question in judge_questions(item.stage)
            if args.questions is None or question.id in args.questions
        ]

        def save(question: Question, answer: JudgeAnswer) -> None:
            with lock:
                stored.setdefault(question.id, {})[item.item] = answer.model_dump(
                    mode="json", exclude_none=True
                )
                store.write_judge_answers(args.run, answers)

        return ask_item(
            questions,
            lambda: inputs_for_run_item(store, args.run, scenario, item),
            stored,
            item.item,
            save,
            f"{scenario} {item.item}",
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        failures = list(pool.map(judge_item, items))
    cost = judge_cost(answers)
    print(f"Judge cost: ${cost - before:.6f} this command; ${cost:.6f} for this run.")
    return int(any(failures))


def judge_cost(answers: dict[str, Any]) -> float:
    return sum(
        answer_cost(answer)
        for questions in answers.values()
        for items in questions.values()
        for answer in items.values()
    )


def ask_item(
    questions: Sequence[Question],
    inputs: Callable[[], JudgeInput],
    stored: Mapping[str, Mapping[str, Any]],
    item: str,
    save: Callable[[Question, JudgeAnswer], None],
    name: str,
) -> bool:
    """Ask each unfinished question on one item, preparing its inputs and images once.

    `stored` holds the item's saved answers by question id then item. Returns whether any
    question was left unjudged; each one is named on stderr.
    """
    failed = False
    prepared: JudgeInput | None = None
    preparation_error: Exception | None = None
    shown: dict[tuple[int | None, bool], Shown] = {}
    for question in questions:
        old = stored.get(question.id, {}).get(item)
        previous = JudgeAnswer.model_validate(old) if old is not None else None
        continuation = start_answer(question, previous)
        if complete(continuation):
            continue

        def saved(answer: JudgeAnswer, selected: Question = question) -> None:
            save(selected, answer)

        if prepared is None and preparation_error is None:
            try:
                prepared = inputs()
            except (OSError, ValueError) as error:
                preparation_error = error
        if preparation_error is not None:
            result = continuation
            failed_answer(question, result, preparation_error)
            saved(result)
        else:
            assert prepared is not None

            def prepare(given: JudgeInput = prepared, selected: Question = question) -> Shown:
                policy = selected.frames_per_second, selected.page_photos
                if policy not in shown:
                    shown[policy] = show(given, selected)
                return shown[policy]

            result = ask_question(question, prepared, previous=previous, shown=prepare, save=saved)
        if result.error:
            failed = True
            print(f"{name} {question.id}: not judged: {result.error}", file=sys.stderr)
    return failed
