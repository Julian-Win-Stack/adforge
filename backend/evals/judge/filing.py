"""Save Julian's judge answers as cases, without assigning him the judge's passes."""

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from evals.cases import load
from evals.regression.cases import make_case
from evals.regression.items import GradedItem, export_base, graded_items
from evals.regression.questions import Question, judge_questions
from evals.regression.store import Store, read_json, write_json

from . import trust
from .records import TAP_WORDS, Tap, tap_doc_id
from .visibility import item_is_shown


def case_verdict(answer: dict[str, Any]) -> dict[str, Any]:
    asks = answer["asks"]
    counted = next((ask for ask in reversed(asks) if ask["verdict"] == answer["verdict"]), asks[-1])
    return {
        "verdict": answer["verdict"],
        "reason": counted["sees"],
        "judge": answer["judge"],
        "model": counted["model"],
        "at": counted["at"],
        "asks": [{"verdict": ask["verdict"], "reason": ask["sees"]} for ask in asks],
    }


def judge_destination(
    store: Store,
    source: dict[str, Any],
    question: Question,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """A moved complaint keeps the destination question's inputs and verdict history."""
    from evals.regression.filing import media_reference

    from .inputs import inputs_for_run_item

    run, scenario = source["source"]["round"], source["source"]["ad"]
    item = source["inputs"]["handoff"]["item"]
    moved = deepcopy(existing if existing is not None else source)
    handoff = moved["inputs"]["handoff"]
    if not {"product_name", "product_text", "page_photo_labels"} <= handoff.keys():
        saved = next(
            (
                row
                for row in graded_items(store, run, scenario)
                if row.stage == question.stage and row.item == item
            ),
            None,
        )
        if saved is None:
            raise ValueError(f"No saved {question.stage} output for {run}/{scenario}/{item}")
        inputs = inputs_for_run_item(store, run, scenario, saved)
        place = (
            source["inputs"]["files"][0].split(":", 1)[0]
            if source["inputs"]["files"]
            else "project-files"
        )
        moved["inputs"] = {
            "handoff": dict(inputs.case_handoff()),
            "files": [media_reference(path, place) for path in inputs.case_files()],
        }
    # A source question's verdict says nothing about the destination question.
    verdicts = list(existing["verdicts"]) if existing is not None else []
    answer = store.read_judge_answers(run).get(scenario, {}).get(question.id, {}).get(item, {})
    if answer.get("verdict") is not None and not answer.get("error"):
        if answer["judge"] not in {verdict["judge"] for verdict in verdicts}:
            verdicts.append(case_verdict(answer))
    moved["verdicts"] = verdicts
    return moved


def _presenter(store: Store, run: str, scenario: str, item: GradedItem) -> bool:
    if item.stage not in ("pictures", "clips"):
        return False
    base = export_base(store, run, scenario)
    rich = read_json(base / "grading-records.json")
    number = int(item.item[1:])
    scene: dict[str, Any] = next((s for s in rich.get("scenes", []) if s["number"] == number), {})
    if "kind" in scene:
        return bool(scene["kind"] == "talking")
    inputs = base / f"scene-{number}" / "inputs.txt"
    return inputs.is_file() and "KIND: talking" in inputs.read_text(encoding="utf-8").splitlines()


def _case(
    store: Store,
    run: str,
    scenario: str,
    item: GradedItem,
    question: Question,
    answer: dict[str, Any],
    grade: dict[str, Any],
    tap: dict[str, Any] | None,
    failed: set[str],
    media_place: str,
) -> dict[str, Any]:
    from evals.regression.filing import media_reference

    from .inputs import inputs_for_run_item

    inputs = inputs_for_run_item(store, run, scenario, item)
    if tap is not None:
        value = cast(Tap, tap["tap"])
        label = "pass" if value == "not_a_flaw" else "fail"
        reason = TAP_WORDS[value] + (f": {tap['note']}" if tap["note"] else "")
    else:
        label = "fail" if question.id in failed else "pass"
        reason = grade.get("note", "") if label == "fail" else ""
    case = make_case(
        run=run,
        scenario=scenario,
        question=question.id,
        item=item.item,
        label=label,
        reason=reason,
        handoff={**inputs.case_handoff(), "question": question.words},
        files=[media_reference(path, media_place) for path in inputs.case_files()],
        graded_at=(tap or grade).get("at", ""),
        source=str(store.run_dir(run) / "grades.json"),
    )
    if tap is not None:
        case["source"]["tap"] = tap["tap"]
    case["verdicts"] = [case_verdict(answer)]
    return case


def file_judge_grades(
    store: Store,
    run: str,
    *,
    cases_dir: Path,
    media_place: str,
    complaints: dict[str, Any],
    scenarios: list[str] | None = None,
    item_grades: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Return (new cases, unresolved fails), journaling every decision before appending.

    A note run passes its case ids as `scenarios` and the item grades its own taps
    imply as `item_grades`; there an untapped judge answer files only with a grade,
    since Julian's page shows every remade clip but grades the case, not the clip.
    """
    from evals.regression.filing import append_case

    answers = store.read_judge_answers(run)
    if not answers:
        return 0, 0
    taps = store.read_judge_taps(run)
    grades = store.read_grades(run) if item_grades is None else item_grades
    trusted = trust.trusted_questions(cases_dir=cases_dir)
    journal_path = store.run_dir(run) / "filed-judge-grades.json"
    journal = read_json(journal_path)
    added = unresolved = 0
    reached: set[str] = set()
    for scenario in store.read_run(run)["scenarios"] if scenarios is None else scenarios:
        scenario_answers = answers.get(scenario, {})
        for item in graded_items(store, run, scenario):
            shown = item_grades is None and item_is_shown(
                scenario_answers,
                item.stage,
                item.item,
                trusted,
                presenter=_presenter(store, run, scenario, item),
            )
            grade = grades.get(item.doc_id, {})
            failed = set(complaints.get(item.doc_id, {}).get("judge_failed", []))
            for question in judge_questions(item.stage):
                answer = scenario_answers.get(question.id, {}).get(item.item)
                if answer is None or answer.get("verdict") is None or answer.get("error"):
                    continue
                doc_id = tap_doc_id(scenario, item.stage, item.item, question.id)
                reached.add(doc_id)
                tap = taps.get(doc_id)
                if answer["verdict"] == "fail" and tap is None:
                    print(
                        f"{doc_id} needs Julian's judge tap before it can be filed.",
                        file=sys.stderr,
                    )
                    unresolved += 1
                    continue
                if doc_id not in journal:
                    if tap is None and (
                        (not shown and not grade)
                        or (grade.get("grade") == "fail" and not grade.get("note", "").strip())
                    ):
                        continue
                    journal[doc_id] = _case(
                        store,
                        run,
                        scenario,
                        item,
                        question,
                        answer,
                        grade,
                        tap,
                        failed,
                        media_place,
                    )
                    write_json(journal_path, journal)
                added += append_case(cases_dir / f"{question.id}.jsonl", journal[doc_id])
    for doc_id in taps.keys() - reached:
        print(f"{doc_id} has no judged output; its tap was not filed.", file=sys.stderr)
        unresolved += 1
    return added, unresolved


def missing_judge_fails(store: Store, run: str, *, cases_dir: Path) -> list[str]:
    """An untapped or unfiled judge fail must never become a promoted baseline."""
    answers = store.read_judge_answers(run)
    if not answers:
        return []
    taps = store.read_judge_taps(run)
    questions = {question.id: question for question in judge_questions()}
    filed: dict[str, set[tuple[str, str, str]]] = {}
    missing = []
    for scenario, judged in answers.items():
        for question, items in judged.items():
            for item, answer in items.items():
                if answer.get("verdict") != "fail":
                    continue
                doc_id = tap_doc_id(scenario, questions[question].stage, item, question)
                if doc_id not in taps:
                    missing.append(f"{doc_id}: untapped judge fail")
                    continue
                if question not in filed:
                    path = cases_dir / f"{question}.jsonl"
                    filed[question] = (
                        {
                            (
                                case.source["round"],
                                case.source["ad"],
                                case.inputs["handoff"]["item"],
                            )
                            for case in load(path)
                        }
                        if path.is_file()
                        else set()
                    )
                if (run, scenario, item) not in filed[question]:
                    missing.append(f"{doc_id}: unfiled judge fail")
    return missing
