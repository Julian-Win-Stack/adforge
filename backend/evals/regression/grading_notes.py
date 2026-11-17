"""Grading-page data for a note run (`run.json` `kind: notes`): one card per note case.

Reads only the saved note-run layout shared by #696-#699 (case id in the scenario's place)."""

import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from .store import Store, read_json

if TYPE_CHECKING:
    from .grading import Media

type NoteTap = Literal["gone", "no_new_break"]
NOTE_TAPS: tuple[NoteTap, ...] = ("gone", "no_new_break")
NOTE_TAP_CHOICES = {"yes": "Yes", "no": "No"}
# The change check's question id and item in the run's judge answers.
CHANGE_CHECK = "N1"
CHANGE_ITEM = "note"


def is_note_run(record: Mapping[str, Any]) -> bool:
    return record.get("kind") == "notes"


def run_keys(record: Mapping[str, Any]) -> list[str]:
    """A note run's cases, or a regression run's scenarios: what grade ids start with."""
    return list(record.get("cases" if is_note_run(record) else "scenarios", []))


def note_tap_id(case: str, tap: NoteTap) -> str:
    return f"{case}__note__{tap}"


def parse_note_tap_id(doc_id: str) -> tuple[str, NoteTap] | None:
    parts = doc_id.split("__")
    if len(parts) == 3 and parts[0] and parts[1] == "note" and parts[2] in NOTE_TAPS:
        return parts[0], parts[2]
    return None


def validate_note_tap(doc_id: str, doc: Any) -> dict[str, Any] | None:
    """A saved tap, or None for words saved before a tap (not a grade)."""
    if (
        not isinstance(doc, dict)
        or set(doc) != {"tap", "note", "at"}
        or doc["tap"] not in (None, *NOTE_TAP_CHOICES)
        or not isinstance(doc["note"], str)
        or not isinstance(doc["at"], str)
    ):
        raise ValueError(f"Grade {doc_id!r}: expected tap (yes/no/null), note and at")
    return dict(doc) if doc["tap"] is not None else None


def note_case(
    store: Store,
    run: str,
    case: str,
    media: Media,
    result: Mapping[str, Any],
    judge_answers: Mapping[str, Any],
) -> dict[str, Any]:
    """One card: the note and Julian's grade words, the answers so far, the before and after
    clips of every remade or expected scene, the whole ads, and the ids of its taps."""
    from evals.judge.records import tap_doc_id

    from .questions import judge_questions

    entry = read_json(store.scenario_dir(run, case) / "case.json")
    export = store.export_dir(run, case)
    base = export / case
    meta = read_json(base / "regression.json")
    note_meta = (meta.get("notes") or [{}])[0]
    layout = meta.get("note_case") or {}
    saved = store.read_answers(run, case)
    answers = saved.get("answers", {})
    expected = [int(n) for n in layout.get("expected_scenes", entry.get("scenes", []))]

    def asset(relative: str | None) -> str | None:
        return media.keep(base / relative, f"new/{case}/{relative}") if relative else None

    new_ad = answers.get("note_new_ad")
    # Given up only on a saved "no new ad" answer or a run that stopped or failed. Answers not
    # saved yet keep the taps and say so: the new ad may well be there.
    gave_up = (new_ad or {}).get("verdict") == "fail" or result.get("status") in (
        "failed",
        "stopped",
    )
    answers_missing = new_ad is None and not gave_up
    if answers_missing:
        print(
            f"{case}: no saved code answers yet; the page says so and keeps the taps",
            file=sys.stderr,
        )
    case_answers = judge_answers.get(case, {})
    taps = [] if gave_up else [note_tap_id(case, tap) for tap in NOTE_TAPS]
    scenes = []
    for scene in [] if gave_up else layout.get("scenes", []):
        number = int(scene["number"])
        changed = bool(scene.get("changed"))
        if not changed and number not in expected:
            continue
        shown: dict[str, Any] = {
            "n": number,
            "changed": changed,
            "expected": number in expected,
            "before": asset(scene.get("before_clip")),
            "after": asset(scene.get("after_clip")),
        }
        # Only remade clips are judged (a kept clip's old answer is not about this fix), and
        # only the judge's counted fails are shown, each with its three taps.
        if changed:
            item = f"s{number}"
            shown["judge"] = [
                {
                    "id": question.id,
                    "question": question.words,
                    "docId": tap_doc_id(case, "clips", item, question.id),
                    **case_answers[question.id][item],
                }
                for question in judge_questions("clips")
                if case_answers.get(question.id, {}).get(item, {}).get("verdict") == "fail"
            ]
            taps.extend(finding["docId"] for finding in shown["judge"])
        scenes.append(shown)
    change = case_answers.get(CHANGE_CHECK, {}).get(CHANGE_ITEM)
    if gave_up:
        why = (new_ad or {}).get("reason") or result.get("why") or "The case stopped."
    elif answers_missing:
        why = "Code answers not ready: the note run saved none for this case yet."
    else:
        why = ""
    return {
        "key": case,
        "run": run,
        "kind": "note",
        "scenario": entry.get("scenario", ""),
        "sourceRun": entry.get("source_run", ""),
        "note": note_meta.get("text") or entry.get("note", ""),
        "grade": entry.get("grade", ""),
        "gradedFrom": entry.get("graded_from", ""),
        "expected": expected,
        "status": result.get("status"),
        "gaveUp": gave_up,
        "why": why,
        "answersMissing": answers_missing,
        "code": answers,
        "cost": saved.get("cost_usd", result.get("cost_usd", 0)),
        "minutes": saved.get("minutes", result.get("minutes")),
        "change": change,
        "before": asset(note_meta.get("before")),
        "after": asset(note_meta.get("after")),
        "scenes": scenes,
        "tapIds": taps,
    }
