"""Explicitly ask the eval judge on a finished run; never invoked by the runner."""

import argparse
import os
import sys
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from evals.regression.items import GradedItem, graded_items
from evals.regression.questions import judge_questions
from evals.regression.store import Store

from .ask import ask_question, complete, failed_answer, start_answer
from .inputs import inputs_for_run_item
from .records import JudgeAnswer, answer_cost

RUN_STAGES = ("pictures", "ad")


def main(argv: Sequence[str] | None = None, *, store: Store) -> int:
    import django
    from django.apps import apps

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    if not apps.ready:
        django.setup()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    supported = [q.id for q in judge_questions() if q.stage in RUN_STAGES]
    parser.add_argument("--questions", nargs="+", choices=supported)
    args = parser.parse_args(argv)
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
    before = _cost(answers)

    def judge_item(entry: tuple[str, GradedItem]) -> bool:
        scenario, item = entry
        failed = False
        inputs = None
        preparation_error = None
        for question in judge_questions(item.stage):
            if args.questions is not None and question.id not in args.questions:
                continue
            old = answers.get(scenario, {}).get(question.id, {}).get(item.item)
            previous = JudgeAnswer.model_validate(old) if old is not None else None
            continuation = start_answer(question, previous)
            if complete(continuation):
                continue

            def save(answer: JudgeAnswer, *, question_id: str = question.id) -> None:
                with lock:
                    answers.setdefault(scenario, {}).setdefault(question_id, {})[item.item] = (
                        answer.model_dump(mode="json", exclude_none=True)
                    )
                    store.write_judge_answers(args.run, answers)

            if inputs is None and preparation_error is None:
                try:
                    inputs = inputs_for_run_item(store, args.run, scenario, item)
                except (OSError, ValueError) as error:
                    preparation_error = error
            if preparation_error is not None:
                result = continuation
                failed_answer(question, result, preparation_error)
                save(result)
            else:
                assert inputs is not None
                result = ask_question(question, inputs, previous=previous, save=save)
            if result.error:
                failed = True
                print(
                    f"{scenario} {item.item} {question.id}: not judged: {result.error}",
                    file=sys.stderr,
                )
        return failed

    with ThreadPoolExecutor(max_workers=3) as pool:
        failures = list(pool.map(judge_item, items))
    cost = _cost(answers)
    print(f"Judge cost: ${cost - before:.6f} this command; ${cost:.6f} for this run.")
    return int(any(failures))


def _cost(answers: dict[str, Any]) -> float:
    return sum(
        answer_cost(answer)
        for questions in answers.values()
        for items in questions.values()
        for answer in items.values()
    )
