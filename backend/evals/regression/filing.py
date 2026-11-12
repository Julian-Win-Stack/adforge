"""File the run's saved grades into the eval's append-only ground-truth case files."""

import argparse
import copy
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.cases import FOLDER, half_for, load

from .cases import case_id, make_case
from .items import graded_items, scenario_context
from .questions import Question, Stage, all_questions, julian_questions
from .store import Store, atomic_write_text, flush_to_disk, read_json, write_json

PURPOSE = "file_regression_complaint"
INSTRUCTIONS = """File the owner's words under the offered questions for this stage.
Do not grade the output yourself. Select only questions the owner's complaint fails.
Praise or words with no complaint fail none. If a complaint fits no offered question,
name one new question from the owner's words: a snake_case id under 40 characters and
a one-line yes/no question. Otherwise new_question is null. Do not invent a complaint.
"""


class NewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,38}$")
    question: str = Field(min_length=1)

    @model_validator(mode="after")
    def one_line(self) -> Self:
        if not self.question.strip() or "\n" in self.question or "\r" in self.question:
            raise ValueError("A new question needs one non-empty line")
        return self


class FiledComplaint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    questions: list[str]
    new_question: NewQuestion | None


def classify(
    stage: Stage,
    item: str,
    words: str,
    questions: Sequence[Question],
    *,
    reserved: set[str],
    require_complaint: bool,
) -> FiledComplaint:
    import django
    from django.apps import apps

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    if not apps.ready:
        django.setup()
    from gateway import catalog
    from gateway.gateway import ask_unrecorded
    from gateway.types import Handoff, ModelRequest

    class Complaint(Handoff):
        stage: str
        item: str
        words: str
        questions: list[dict[str, str]]

    offered = {q.id for q in questions}

    class Answer(FiledComplaint):
        @model_validator(mode="after")
        def offered_questions_only(self) -> Self:
            if set(self.questions) - offered:
                raise ValueError("Choose only the offered Julian-answered questions")
            if len(set(self.questions)) != len(self.questions):
                raise ValueError("Do not repeat a question")
            if self.new_question is not None:
                if self.questions:
                    raise ValueError("Start a question only when no offered question fits")
                if self.new_question.id in reserved or self.new_question.id == "clip_product":
                    raise ValueError("That question id already exists; use an offered question")
            elif require_complaint and not self.questions:
                raise ValueError("The owner marked fail: identify its question or start one")
            return self

    return ask_unrecorded(
        ModelRequest(
            purpose=PURPOSE,
            model=catalog.MODEL_FOR_PURPOSE[PURPOSE],
            instructions=INSTRUCTIONS,
            handoff=Complaint(
                stage=stage,
                item=item,
                words=words,
                questions=[{"id": q.id, "question": q.words} for q in questions],
            ),
            output=Answer,
        )
    )


def media_reference(path: Path, place: str = "project-files") -> str:
    path = path.resolve()
    if place == "project-files":
        project_files = Path(os.environ.get("PROJECT_FILES", "/mnt/project-files")).resolve()
        if path.is_relative_to(project_files):
            return f"{place}:{path.relative_to(project_files)}"
    return f"{place}:{path}"


def file_grades(
    store: Store,
    run: str,
    *,
    cases_dir: Path = FOLDER,
    media_place: str = "project-files",
) -> int:
    grades_path = store.run_dir(run) / "grades.json"
    if not grades_path.is_file():
        print(
            f"No exported grades at {grades_path}; export Julian's grades first.", file=sys.stderr
        )
        return 1
    grades = store.read_grades(run)
    journal_path = store.run_dir(run) / "filed-grades.json"
    journal = read_json(journal_path)
    added = 0
    unresolved = 0
    reached: set[str] = set()
    for scenario in store.read_run(run)["scenarios"]:
        context = scenario_context(store, run, scenario)
        for item in graded_items(store, run, scenario):
            reached.add(item.doc_id)
            if item.doc_id in journal:
                planned = journal[item.doc_id]
                for question_id, case in planned["cases"].items():
                    added += append_case(cases_dir / f"{question_id}.jsonl", case)
                if planned["description"]:
                    print(planned["description"])
                continue
            grade = grades.get(item.doc_id, {})
            questions = [
                q
                for q in julian_questions(item.stage, cases_dir=cases_dir)
                if q.id != "clip_product"
            ]
            words = grade.get("note", "")
            if grade.get("grade") == "fail" and not words.strip():
                print(
                    f"{item.doc_id} needs words before its failed grade can be filed.",
                    file=sys.stderr,
                )
                unresolved += 1
                continue
            failed: set[str] = set()
            new_id = None
            if words.strip():
                reserved = {q.id for q in all_questions(cases_dir=cases_dir)} | {
                    path.stem for path in cases_dir.glob("*.jsonl")
                }
                answer = classify(
                    item.stage,
                    item.item,
                    words,
                    questions,
                    reserved=reserved,
                    require_complaint=grade.get("grade") == "fail",
                )
                failed.update(answer.questions)
                if answer.new_question:
                    new = answer.new_question
                    questions.append(Question(new.id, item.stage, "julian", new.question))
                    failed.add(new.id)
                    new_id = new.id
            description = ""
            if failed:
                targets = ", ".join(
                    f"{q.id} (new)" if q.id == new_id else q.id for q in questions if q.id in failed
                )
                description = (
                    f"{scenario}, {item.item} {item.stage}: {json.dumps(words)} → {targets}"
                )
                print(description)
            planned_cases = {}
            for question in questions:
                handoff: dict[str, Any] = {
                    "question": question.words,
                    "scenario": scenario,
                    "situation": context.get("situation", ""),
                    "stage": item.stage,
                    "item": item.item,
                }
                if item.item.startswith("s") and item.item[1:].isdigit():
                    handoff.update(line=item.line, shows=item.shows)
                if item.note:
                    handoff["note"] = item.note
                case = make_case(
                    run=run,
                    scenario=scenario,
                    question=question.id,
                    item=item.item,
                    label="fail" if question.id in failed else "pass",
                    reason=words if not failed or question.id in failed else "",
                    handoff=handoff,
                    files=[media_reference(path, media_place) for path in item.files],
                    graded_at=grade.get("at", ""),
                    source=str(store.run_dir(run) / "grades.json"),
                )
                planned_cases[question.id] = case
            # Keep the filing decision before appending: a stopped command resumes its
            # missing lines without paying to classify the same complaint again.
            journal[item.doc_id] = {"cases": planned_cases, "description": description}
            write_json(journal_path, journal)
            for question_id, case in planned_cases.items():
                added += append_case(cases_dir / f"{question_id}.jsonl", case)
    added += _backfill_questions(journal_path, cases_dir, journal, reached)
    for doc_id in grades:
        archived_only = (
            doc_id == "round__notes"
            or doc_id.endswith("__no-worse")
            or "__photo-" in doc_id
            or re.search(r"__s[1-9]\d*__take[1-9]\d*$", doc_id) is not None
        )
        if doc_id not in reached and not archived_only:
            print(f"{doc_id} has no saved graded output; its grade was not filed.", file=sys.stderr)
            unresolved += 1
    print(f"{added} cases added.")
    return 1 if unresolved else 0


def _backfill_questions(
    journal_path: Path, cases_dir: Path, journal: dict[str, Any], reached: set[str]
) -> int:
    """A question discovered late still gets the earlier items' implicit passes."""
    questions = [q for q in julian_questions(cases_dir=cases_dir) if q.id != "clip_product"]
    pending: list[tuple[Path, dict[str, Any]]] = []
    for doc_id in sorted(reached):
        # Unresolved failed taps have no filing decision: never turn them into
        # implicit passes just because another item introduced a question.
        if doc_id not in journal or not journal[doc_id]["cases"]:
            continue
        planned = journal[doc_id]["cases"]
        original = next(iter(planned.values()))
        handoff = original["inputs"]["handoff"]
        source = original["source"]
        for question in questions:
            if question.stage != handoff["stage"] or question.id in planned:
                continue
            path = cases_dir / f"{question.id}.jsonl"
            identifier = case_id(source["round"], source["ad"], question.id, handoff["item"])
            existing = (
                next((case for case in load(path) if case.id == identifier), None)
                if path.is_file()
                else None
            )
            case = (
                asdict(existing)
                if existing is not None
                else make_case(
                    run=source["round"],
                    scenario=source["ad"],
                    question=question.id,
                    item=handoff["item"],
                    label="pass",
                    reason="",
                    handoff={**handoff, "question": question.words},
                    files=original["inputs"]["files"],
                    graded_at=source["graded_at"],
                    source=source["from"],
                )
            )
            planned[question.id] = case
            pending.append((path, case))
    if pending:
        write_json(journal_path, journal)
    return sum(append_case(path, case) for path, case in pending)


def append_case(path: Path, case: dict[str, Any]) -> int:
    if path.stem == "clip_product":
        raise ValueError(
            "clip_product.jsonl is maintained by its own eval and cannot be changed here"
        )
    existing = {c.id for c in load(path)} if path.exists() else set()
    if case["id"] in existing:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as file:
            file.seek(-1, os.SEEK_END)
            prefix = "" if file.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8") as file:
        file.write(prefix + json.dumps(case, ensure_ascii=False) + "\n")
        file.flush()
        flush_to_disk(file.fileno())
    return 1


def main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression file-grades")
    parser.add_argument("run")
    parser.add_argument("grades_file", nargs="?", type=Path)
    parser.add_argument("--cases-dir", type=Path, default=FOLDER)
    parser.add_argument(
        "--media-place", choices=("project-files", "azure", "mac"), default="project-files"
    )
    args = parser.parse_args(argv)
    try:
        if args.grades_file is not None:
            if not args.grades_file.is_file():
                raise ValueError(f"No grades file at {args.grades_file}")
            store.write_grades(args.run, read_json(args.grades_file))
        return file_grades(store, args.run, cases_dir=args.cases_dir, media_place=args.media_place)
    except Exception as error:
        print(f"Could not file {args.run}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _replace_case(path: Path, identifier: str, replacement: dict[str, Any]) -> None:
    # Split on LF only: Julian's words can legally contain a raw Unicode line
    # separator inside JSON. Unrelated lines, including their CRs, stay verbatim.
    lines = path.read_bytes().decode("utf-8").split("\n") if path.exists() else []
    for number, line in enumerate(lines):
        if line.strip() and json.loads(line)["id"] == identifier:
            lines[number] = json.dumps(replacement, ensure_ascii=False)
            atomic_write_text(path, "\n".join(lines))
            return
    append_case(path, replacement)


def move_case(identifier: str, target: str, *, store: Store, cases_dir: Path = FOLDER) -> None:
    available = {q.id: q for q in julian_questions(cases_dir=cases_dir)}
    if target not in available or target == "clip_product":
        raise ValueError(f"Not a Julian-answered regression question: {target}")
    found = [
        (path, case)
        for path in cases_dir.glob("*.jsonl")
        if path.stem != "clip_product"
        for case in load(path)
        if case.id == identifier
    ]
    if len(found) != 1:
        raise ValueError(f"Expected one case named {identifier}; found {len(found)}")
    source_path, case = found[0]
    if case.label != "fail":
        raise ValueError(f"{identifier} is not a failed complaint")
    question = available[target]
    if question.stage != case.inputs["handoff"].get("stage"):
        raise ValueError(f"{target} is not a question at this item's stage")
    if source_path.stem == target:
        print(f"{identifier} is already filed under {target}.")
        return
    raw = next(
        json.loads(line)
        for line in source_path.read_bytes().decode("utf-8").split("\n")
        if line.strip() and json.loads(line)["id"] == identifier
    )
    run, scenario = raw["source"]["round"], raw["source"]["ad"]
    item = raw["inputs"]["handoff"]["item"]
    moved = copy.deepcopy(raw)
    moved["id"] = case_id(run, scenario, target, item)
    moved["half"] = half_for(moved["id"])
    moved["inputs"]["handoff"]["question"] = question.words
    moved["verdicts"] = []
    passed = copy.deepcopy(raw)
    passed.update(label="pass", kind="regression", reason="")
    # This explicit correction is the exception to append-only filing. Keep the
    # complaint at its destination before clearing the old question's failed label.
    destination = cases_dir / f"{target}.jsonl"
    if destination.exists():
        load(destination)
    _replace_case(destination, moved["id"], moved)
    _replace_case(source_path, identifier, passed)
    journal_path = store.run_dir(run) / "filed-grades.json"
    journal = read_json(journal_path)
    for planned in journal.values():
        if identifier in {c["id"] for c in planned["cases"].values()}:
            planned["cases"][source_path.stem] = passed
            planned["cases"][target] = moved
            planned["description"] = (
                f"{scenario}, {item}: {json.dumps(moved['reason'])} → {target} (corrected)"
            )
    if journal:
        write_json(journal_path, journal)
    print(f"{source_path.stem} → pass; {target} → fail: {json.dumps(moved['reason'])}")


def move_main(argv: Sequence[str], *, store: Store) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression move-case")
    parser.add_argument("case_id")
    parser.add_argument("question")
    parser.add_argument("--cases-dir", type=Path, default=FOLDER)
    args = parser.parse_args(argv)
    try:
        move_case(args.case_id, args.question, store=store, cases_dir=args.cases_dir)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
