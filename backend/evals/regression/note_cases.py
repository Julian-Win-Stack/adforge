"""Owner-note cases: a frozen finished ad from a saved run plus one note an owner would write."""

import json
import re
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .scenarios import load_scenarios

CATALOGUE = Path(__file__).with_name("note_cases.json")
FIELDS = ("id", "scenario", "source_run", "scenes", "note", "grade", "graded_from")
_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True)
class NoteCase:
    id: str
    scenario: str
    # The run named here, never "the current baseline": promotion must not swap the ad.
    source_run: str
    # Scene numbers in the source ad that the problem is in, from Julian's grade.
    scenes: tuple[int, ...]
    note: str
    grade: str
    graded_from: str

    def entry(self) -> dict[str, Any]:
        return {**asdict(self), "scenes": list(self.scenes)}


def _case(row: Any, number: int, scenarios: Collection[str]) -> NoteCase:
    name = row.get("id") if isinstance(row, dict) and isinstance(row.get("id"), str) else None
    where = f"note case {name!r}" if name else f"note case #{number}"

    def refuse(why: str) -> ValueError:
        return ValueError(f"{where}: {why}")

    if not isinstance(row, dict):
        raise refuse("must be a JSON object")
    if set(row) != set(FIELDS):
        raise refuse(f"needs exactly the fields {', '.join(FIELDS)}")
    if not isinstance(row["id"], str) or not _KEY.fullmatch(row["id"]):
        raise refuse("id must be a lowercase kebab-case name")
    if row["scenario"] not in scenarios:
        raise refuse(f"unknown scenario {row['scenario']!r}")
    run = row["source_run"]
    if not isinstance(run, str) or not run or run in (".", "..") or "/" in run or "\\" in run:
        raise refuse("source_run must name a saved run")
    scenes = row["scenes"]
    if (
        not isinstance(scenes, list)
        or not scenes
        or any(type(scene) is not int or scene < 1 for scene in scenes)
        or len(set(scenes)) != len(scenes)
    ):
        raise refuse("scenes must be a non-empty list of different positive scene numbers")
    if not isinstance(row["note"], str) or not row["note"].strip():
        raise refuse("note must be the non-empty text sent to the chat")
    for field in ("grade", "graded_from"):
        if not isinstance(row[field], str):
            raise refuse(f"{field} must be text")
    return NoteCase(**{**row, "scenes": tuple(scenes)})


def load_note_cases(path: Path = CATALOGUE) -> list[NoteCase]:
    """Refuse a malformed catalogue, naming the entry, before any paid work."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path.name}: must be a JSON list of note cases")
    scenarios = {scenario.key for scenario in load_scenarios()}
    cases: list[NoteCase] = []
    for number, row in enumerate(rows, 1):
        case = _case(row, number, scenarios)
        if any(case.id == earlier.id for earlier in cases):
            raise ValueError(f"note case {case.id!r}: repeated id")
        cases.append(case)
    return cases


def select_note_cases(ids: Sequence[str] | None = None, path: Path = CATALOGUE) -> list[NoteCase]:
    cases = load_note_cases(path)
    if not ids:
        return cases
    unknown = set(ids) - {case.id for case in cases}
    if unknown:
        raise ValueError(f"Unknown note cases: {', '.join(sorted(unknown))}")
    return [case for case in cases if case.id in ids]
