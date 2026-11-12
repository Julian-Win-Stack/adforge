"""Graded outputs in a scenario's saved export, independent of its live database."""

import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .questions import Stage
from .store import Store, read_json

SCENE_MEDIA: tuple[tuple[str, Stage], ...] = (("picture", "pictures"), ("clip", "clips"))


@dataclass(frozen=True)
class GradedItem:
    doc_id: str
    item: str
    stage: Stage
    line: str = ""
    shows: str = ""
    files: tuple[Path, ...] = ()
    note: str = ""


def export_base(store: Store, run: str, scenario: str) -> Path:
    directory = store.export_dir(run, scenario)
    nested = directory / scenario
    return nested if nested.is_dir() else directory


def scenario_context(store: Store, run: str, scenario: str) -> dict[str, Any]:
    base = export_base(store, run, scenario)
    record = read_json(base / "regression.json")
    ad = read_json(base / "ad.json")
    return {"key": scenario, "link": ad.get("product_url", ""), **record.get("scenario", {})}


def graded_items(store: Store, run: str, scenario: str) -> list[GradedItem]:
    base = export_base(store, run, scenario)
    path = base / "regression.json"
    record = read_json(path) if path.is_file() else _legacy_record(base, scenario)
    items = []
    if record.get("page_files"):
        items.append(
            GradedItem(
                f"{scenario}__page", "page", "page", files=_files(base, record["page_files"])
            )
        )
    if record.get("scenes"):
        plans = (
            (path,)
            if path.is_file()
            else tuple(sorted(base.glob("scene-*/inputs.txt"))) or (base.parent / "results.json",)
        )
        items.append(GradedItem(f"{scenario}__plan", "plan", "plan", files=plans))
    for scene in record.get("scenes", []):
        number = int(scene["number"])
        for field, stage in SCENE_MEDIA:
            if scene.get(field):
                items.append(
                    GradedItem(
                        f"{scenario}__s{number}__{field}",
                        f"s{number}",
                        stage,
                        line=scene.get("line", ""),
                        shows=scene.get("shows", ""),
                        files=_files(base, [scene[field]]),
                    )
                )
    ad = next(
        (base / name for name in ("ad-latest.mp4", "ad.mp4") if (base / name).is_file()), None
    )
    if ad is not None:
        items.append(GradedItem(f"{scenario}__ad", "ad", "ad", files=(ad,)))
    for number, note in enumerate(record.get("notes", []), 1):
        number = int(note.get("number", number))
        if note.get("before") and note.get("after"):
            items.append(
                GradedItem(
                    f"{scenario}__note{number}",
                    f"note{number}",
                    "note",
                    files=_files(base, [note["before"], note["after"]]),
                    note=note.get("text", ""),
                )
            )
    return items


def _files(base: Path, names: list[str]) -> tuple[Path, ...]:
    files = tuple(base / name for name in names)
    for file in files:
        if not file.is_file():
            raise ValueError(f"Saved graded output is missing: {file}")
    return files


def _legacy_record(base: Path, scenario: str) -> dict[str, Any]:
    results = read_json(base.parent / "results.json")
    numbers = {
        int(path.name[6:])
        for path in base.glob("scene-*")
        if path.is_dir() and path.name[6:].isdigit()
    }
    numbers.update(
        int(row["scene"])
        for key, row in results.items()
        if row.get("ad_folder") == scenario or key.startswith(f"{scenario}:")
    )
    scenes = []
    for number in sorted(numbers):
        folder = base / f"scene-{number}"
        inputs = _input_fields(folder / "inputs.txt")
        result = results.get(f"{scenario}:{number}", {})
        scene = {
            "number": number,
            "line": result.get("line", inputs.get("LINE", "")),
            "shows": result.get("shows", inputs.get("SHOWS", "")),
        }
        if pictures := sorted(folder.glob("start-picture.*")):
            scene["picture"] = str(pictures[0].relative_to(base))
        if result.get("kept") is not None:
            from evals.round_export import clip_name

            name = clip_name(result["kept"].get("remake"))
            scene["clip"] = f"scene-{number}/{name}.mp4"
        scenes.append(scene)
    return {"scenes": scenes, "notes": _legacy_notes(base)}


def _legacy_notes(base: Path) -> list[dict[str, Any]]:
    rows = read_json(base / "notes.json", [])
    notes = [
        {
            "number": note.get("number", number),
            "text": note.get("text", note.get("note", "")),
            "before": note.get("before"),
            "after": note.get("after"),
        }
        for number, note in enumerate(rows, 1)
    ]
    if not notes:
        return []
    notes[0]["before"] = notes[0]["before"] or "ad.mp4"
    notes[-1]["after"] = notes[-1]["after"] or "ad-latest.mp4"
    # Adjacent notes share one finished version. Only use a boundary named in
    # the records; the original and latest ads cannot stand for a missing middle.
    for previous, current in pairwise(notes):
        previous["after"] = previous["after"] or current["before"]
        current["before"] = current["before"] or previous["after"]
    missing = [
        f"note{note['number']} {side}"
        for note in notes
        for side in ("before", "after")
        if not note[side]
    ]
    if missing:
        raise ValueError(
            f"Legacy owner-note media unavailable: {', '.join(missing)}. "
            "The first and latest ads do not name the intermediate versions; "
            "add their before/after paths to notes.json."
        )
    return notes


def _input_fields(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    fields: dict[str, str] = {}
    key = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^([A-Z][A-Z_ ]+): ?(.*)$", line):
            key, value = match.groups()
            fields[key] = value
        elif key:
            fields[key] += "\n" + line
    return fields
