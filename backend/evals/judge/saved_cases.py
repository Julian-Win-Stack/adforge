"""Ask the run judge on saved, human-labelled cases without rewriting other lines."""

import json
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from evals.cases import Case, load
from evals.regression.questions import Question, judge_questions
from evals.regression.store import atomic_write_text, read_json, write_json

from .ask import Shown, ask_question, counted_verdict, failed_answer, judge_id, show, start_answer
from .inputs import JudgeInput
from .records import JudgeAnswer, answer_cost

_CHECKPOINTS = TypeAdapter(dict[str, dict[str, JudgeAnswer]])


def _inputs(case: Case, question: Question) -> JudgeInput:
    handoff = case.inputs["handoff"]
    if handoff.get("stage") != question.stage:
        raise ValueError(f"Case stage must be {question.stage!r} for {question.id}")
    labels = handoff.get("page_photo_labels")
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("Case needs a list of page photo labels")
    files = []
    for reference in case.inputs["files"]:
        place, _, name = reference.partition(":")
        if place != "project-files":
            raise ValueError(f"Media cannot be read here: {reference}")
        path = Path(os.environ.get("PROJECT_FILES", "/mnt/project-files")) / name
        if not path.is_file():
            raise ValueError(f"Media is missing: {reference}")
        files.append(path)
    boundary = len(files) - len(labels)
    if boundary < 1:
        raise ValueError("Case needs item media before its page photos")
    text = {}
    for field in ("item", "line", "shows", "product_name", "product_text"):
        value = handoff.get(field)
        if not isinstance(value, str):
            raise ValueError(f"Case needs text for {field}")
        text[field] = value
    return JudgeInput(
        stage=question.stage,
        item=text["item"],
        files=tuple(files[:boundary]),
        line=text["line"],
        shows=text["shows"],
        product_name=text["product_name"],
        product_text=text["product_text"],
        page_photos=tuple(files[boundary:]),
        page_photo_labels=tuple(labels),
    )


def _append(path: Path, case_id: str, answer: JudgeAnswer) -> None:
    # Split only at actual JSONL newlines: U+2028 inside a JSON string is legal.
    lines = path.read_bytes().split(b"\n")
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw["id"] != case_id:
            continue
        if any(verdict["judge"] == answer.judge for verdict in raw["verdicts"]):
            return
        counted = next(
            (ask for ask in reversed(answer.asks) if ask.verdict == answer.verdict),
            answer.asks[-1],
        )
        raw["verdicts"].append(
            {
                "verdict": answer.verdict,
                "reason": counted.sees,
                "judge": answer.judge,
                "model": counted.model,
                "at": answer.asks[-1].at,
                "asks": [{"verdict": ask.verdict, "reason": ask.sees} for ask in answer.asks],
            }
        )
        ending = b"\r" if line.endswith(b"\r") else b""
        lines[number] = json.dumps(raw, ensure_ascii=False).encode("utf-8") + ending
        atomic_write_text(path, b"\n".join(lines).decode("utf-8"))
        return
    raise ValueError(f"{path}: case {case_id!r} disappeared before its verdict was saved")


def main(
    folder: Path,
    *,
    questions: Sequence[str] | None = None,
    ids: Sequence[str] | None = None,
) -> int:
    checkpoint_path = folder / ".judge-answers.json"
    try:
        if not folder.is_dir():
            raise ValueError(f"No case folder: {folder}")
        checkpoints = _CHECKPOINTS.validate_python(read_json(checkpoint_path), strict=True)
        selected = []
        already = 0
        for question in judge_questions():
            if questions is not None and question.id not in questions:
                continue
            path = folder / f"{question.id}.jsonl"
            for case in load(path) if path.is_file() else []:
                if ids is not None and case.id not in ids:
                    continue
                if any(verdict.judge == judge_id(question) for verdict in case.verdicts):
                    already += 1
                else:
                    selected.append((question, path, case))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    def cost() -> float:
        return sum(
            answer_cost(answer.model_dump())
            for cases in checkpoints.values()
            for answer in cases.values()
        )

    before = cost()
    asking = sum(
        counted_verdict(start_answer(question, checkpoints.get(question.id, {}).get(case.id)).asks)
        is None
        for question, _, case in selected
    )
    print(f"Selected {len(selected)} cases; will ask {asking}; {already} already judged.")
    grouped: dict[str, list[tuple[Question, Path, Case]]] = defaultdict(list)
    for question, path, case in selected:
        # The question differs between files, but all allowed item context must match.
        context = {k: v for k, v in case.inputs["handoff"].items() if k != "question"}
        key = json.dumps(
            [case.source["round"], case.source["ad"], context, case.inputs["files"]], sort_keys=True
        )
        grouped[key].append((question, path, case))
    failed = False
    for cases in grouped.values():
        shown: dict[tuple[int | None, bool], Shown] = {}
        for question, path, case in cases:
            previous = checkpoints.get(question.id, {}).get(case.id)

            def save(answer: JudgeAnswer, *, q: str = question.id, cid: str = case.id) -> None:
                checkpoints.setdefault(q, {})[cid] = answer
                write_json(
                    checkpoint_path,
                    {
                        q: {
                            cid: answer.model_dump(mode="json", exclude_none=True)
                            for cid, answer in rows.items()
                        }
                        for q, rows in checkpoints.items()
                    },
                )

            # A checkpoint may already contain enough asks after a crash before publication.
            result = start_answer(question, previous)
            try:
                if counted_verdict(result.asks) is not None:
                    result.verdict = counted_verdict(result.asks)
                    result.error = None
                    save(result)
                else:
                    item = _inputs(case, question)

                    def prepare(
                        given: JudgeInput = item,
                        selected: Question = question,
                        cache: dict[tuple[int | None, bool], Shown] = shown,
                    ) -> Shown:
                        policy = selected.frames_per_second, selected.page_photos
                        if policy not in cache:
                            cache[policy] = show(given, selected)
                        return cache[policy]

                    result = ask_question(
                        question, item, previous=previous, shown=prepare, save=save
                    )
                if result.error:
                    print(
                        f"{question.id} {case.id}: skipped, not judged: {result.error}",
                        file=sys.stderr,
                    )
                    failed = True
                    continue
                _append(path, case.id, result)
            except (OSError, ValueError) as error:
                failed_answer(question, result, error)
                save(result)
                print(f"{question.id} {case.id}: skipped, not judged: {error}", file=sys.stderr)
                failed = True
    total = cost()
    print(f"Judge cost: ${total - before:.6f} this command; ${total:.6f} saved for these cases.")
    return int(failed)
