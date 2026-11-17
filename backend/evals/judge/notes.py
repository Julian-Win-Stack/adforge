"""Ask the change check (N1) and the eval judge's clip questions on a saved note run.

Run only when Julian asks: `judge-notes <run name> [--only <case id> ...]`. The change check
sees the owner's note and the whole first and new ads as frames, every scene, and never the
expected scenes or anything the app decided. The eval judge's clip questions are asked on
the new clip of every scene whose clip changed, and on no other scene.
"""

import argparse
import math
import os
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from evals.regression.items import export_base
from evals.regression.questions import Question, judge_questions, note_questions
from evals.regression.store import Store, read_json

from . import prompts
from .ask import (
    NoteForm,
    NoteHandoff,
    Shown,
    ask_with_reasks,
    failed_answer,
    frame_images,
    page_photos,
    purpose,
    start_note_answer,
)
from .command import ask_item, judge_cost
from .inputs import JudgeInput, PageFacts, page_facts
from .records import JudgeAnswer, NoteAnswer

CLIP_QUESTIONS = ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14")
SIDES = ("First ad", "New ad")


@dataclass(frozen=True)
class Scene:
    number: int
    clip: Path
    line: str = ""
    shows: str = ""


@dataclass(frozen=True)
class NoteCase:
    """What the checks may see of one saved note case: never its expected scenes."""

    note: str
    facts: PageFacts
    before: tuple[Scene, ...]
    after: tuple[Scene, ...]
    changed: tuple[Scene, ...]


def change_check() -> Question:
    return next(q for q in note_questions() if q.id == "N1")


def read_case(store: Store, run: str, case_id: str) -> NoteCase | None:
    """The case's note and both ads' clips, or None when the app made no new ad."""
    base = export_base(store, run, case_id)
    record = read_json(base / "regression.json")
    notes = record.get("notes") or []
    if len(notes) != 1:
        raise ValueError(f"{run}/{case_id}: expected one owner note, found {len(notes)}")
    if not notes[0].get("after"):
        return None
    note_case = record.get("note_case") or {}
    if not note_case.get("scenes"):
        raise ValueError(f"{run}/{case_id}: no scenes of the first ad saved")
    new_scenes = {int(scene["number"]): scene for scene in record.get("scenes", [])}

    def clip(name: Any, where: str) -> Path:
        if not name:
            raise ValueError(f"{run}/{case_id}: {where} has no saved clip")
        path = base / str(name)
        if not path.is_file():
            raise ValueError(f"{run}/{case_id}: {where} clip is missing: {path}")
        return path

    def scene(number: int, path: Path) -> Scene:
        words = new_scenes.get(number, {})
        return Scene(number, path, str(words.get("line", "")), str(words.get("shows", "")))

    before = tuple(
        Scene(int(row["number"]), clip(row.get("before_clip"), f"first ad scene {row['number']}"))
        for row in note_case["scenes"]
    )
    after = tuple(
        scene(number, clip(row.get("clip"), f"new ad scene {number}"))
        for number, row in sorted(new_scenes.items())
    )
    if not after:
        raise ValueError(f"{run}/{case_id}: no scenes of the new ad saved")
    changed = tuple(
        scene(int(row["number"]), clip(row.get("after_clip"), f"new ad scene {row['number']}"))
        for row in note_case["scenes"]
        if row.get("changed") is True
    )
    facts = page_facts(store, run, case_id)
    return NoteCase(str(notes[0].get("text", "")), facts, before, after, changed)


def note_images(case: NoteCase, question: Question) -> Shown:
    """Shop photos, then every frame of every scene of both ads, labelled, never skipped."""
    from jobs.clip_judge import MOST_PICTURES, cut_frames

    if question.frames_per_second is None:
        raise ValueError("The change check needs a frame rate")
    photos = page_photos(
        JudgeInput(
            stage="ad",
            item="ad",
            files=(),
            page_photos=case.facts.page_photos,
            page_photo_labels=case.facts.page_photo_labels,
        )
    )
    room = MOST_PICTURES - len(photos)
    with tempfile.TemporaryDirectory(prefix="eval-note-") as temporary:
        cut = []
        for side, scenes in zip(SIDES, (case.before, case.after), strict=True):
            for scene in scenes:
                folder = Path(temporary) / f"{side[0]}{scene.number}"
                frames = cut_frames(scene.clip, folder, question.frames_per_second)
                if not frames:
                    raise ValueError(f"{side} scene {scene.number} has no readable frames")
                cut.append((side, scene.number, frames))
        if len(cut) > room:
            raise ValueError(f"{len(cut)} scenes don't fit {room} pictures beside the photos")
        total = sum(len(frames) for _, _, frames in cut)
        together = max(1, math.ceil(total / room))
        # A scene's frames are joined only with its own, so rounding can need one more.
        while sum(math.ceil(len(frames) / together) for _, _, frames in cut) > room:
            together += 1
        images = [
            image
            for side, number, frames in cut
            for image in frame_images(frames, together, f"{side}, scene {number}:")
        ]
    said = (
        f"The first ad ({len(case.before)} scenes), then the new ad ({len(case.after)} scenes): "
        f"every frame of every scene at {question.frames_per_second} frames a second "
        f"({total} frames), in scene order."
    )
    if together > 1:
        said += " Consecutive frames of a scene are side by side, left to right."
    if photos:
        said = f"{len(case.facts.page_photos)} shop photos, then " + said
    return Shown((*photos, *images), said)


def ask_change_check(
    case: NoteCase,
    *,
    previous: NoteAnswer | None = None,
    save: Callable[[NoteAnswer], None] = lambda answer: None,
) -> NoteAnswer:
    from gateway import catalog
    from gateway.types import ModelRequest

    question = change_check()

    def request() -> ModelRequest[Any]:
        shown = note_images(case, question)
        return ModelRequest(
            purpose=purpose(question),
            model=catalog.MODEL_FOR_PURPOSE[purpose(question)],
            instructions=prompts.note_instructions(),
            handoff=NoteHandoff(
                product_name=case.facts.product_name,
                owner_note=case.note,
                shown=shown.said,
                question=prompts.question_text(question.id),
            ),
            output=NoteForm,
            images=shown.images,
        )

    answer = start_note_answer(question, previous)
    ask_with_reasks(question, answer, request, lambda raw: raw, save=save)
    return answer


def main(argv: Sequence[str] | None = None, *, store: Store | None = None) -> int:
    import django
    from django.apps import apps

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    if not apps.ready:
        django.setup()
    parser = argparse.ArgumentParser(prog="judge-notes", description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--only", nargs="+", metavar="CASE", help="Judge only these cases")
    args = parser.parse_args(argv)
    from adforge.paid_runs import missing_keys
    from evals.regression.preflight import refuse

    if refuse(missing_keys("judge")):
        return 1
    if store is None:
        store = Store()
    record = store.read_run(args.run)
    if record.get("kind") != "notes":
        print(f"{args.run}: choose a saved note run", file=sys.stderr)
        return 1
    cases = list(record.get("cases", []))
    if args.only is not None:
        unknown = sorted(set(args.only) - set(cases))
        if unknown:
            parser.error(f"Not cases of {args.run}: {', '.join(unknown)}")
        cases = [case for case in cases if case in args.only]
    answers = store.read_judge_answers(args.run)
    before = judge_cost(answers)
    lock = threading.Lock()
    clip_questions = [q for q in judge_questions("clips") if q.id in CLIP_QUESTIONS]

    def write(case_id: str, question: Question, item: str, answer: BaseModel) -> None:
        with lock:
            answers.setdefault(case_id, {}).setdefault(question.id, {})[item] = answer.model_dump(
                mode="json", exclude_none=True
            )
            store.write_judge_answers(args.run, answers)

    def judge_case(case_id: str) -> bool:
        if not store.scenario_done(args.run, case_id):
            print(f"{case_id}: not finished; skipped")
            return False
        question = change_check()
        stored = answers.setdefault(case_id, {})
        old = stored.get(question.id, {}).get("note")
        previous = NoteAnswer.model_validate(old) if old is not None else None

        def save_check(answer: NoteAnswer) -> None:
            write(case_id, question, "note", answer)

        try:
            case = read_case(store, args.run, case_id)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = start_note_answer(question, previous)
            failed_answer(question, result, error)
            save_check(result)
            print(f"{case_id} note N1: not judged: {result.error}", file=sys.stderr)
            return True
        if case is None:
            print(f"{case_id}: no new ad; skipped")
            return False
        failed = False
        result = ask_change_check(case, previous=previous, save=save_check)
        if result.error:
            failed = True
            print(f"{case_id} note N1: not judged: {result.error}", file=sys.stderr)
        for scene in case.changed:
            item = f"s{scene.number}"
            inputs = JudgeInput(
                stage="clips",
                item=item,
                files=(scene.clip,),
                line=scene.line,
                shows=scene.shows,
                product_name=case.facts.product_name,
                product_text=case.facts.product_text,
                page_photos=case.facts.page_photos,
                page_photo_labels=case.facts.page_photo_labels,
            )

            def save_clip(selected: Question, answer: JudgeAnswer, scene_item: str = item) -> None:
                write(case_id, selected, scene_item, answer)

            def prepared(given: JudgeInput = inputs) -> JudgeInput:
                return given

            failed |= ask_item(
                clip_questions,
                prepared,
                stored,
                item,
                save_clip,
                f"{case_id} {item}",
            )
        return failed

    with ThreadPoolExecutor(max_workers=3) as pool:
        failures = list(pool.map(judge_case, cases))
    cost = judge_cost(answers)
    print(f"Judge cost: ${cost - before:.6f} this command; ${cost:.6f} for this run.")
    return int(any(failures))
