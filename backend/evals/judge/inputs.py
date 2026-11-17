"""The judge's allowed inputs, shared by run judging and case filing.

Finished-ad media are the kept scene clips, in scene order. Asking A11 cuts one still
from each; saved cases may instead hold those stills. In a case's files, item media
come first, followed by the page photos. The number of page_photo_labels fixes that
boundary, including an empty label for every photo whose description was not saved.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from evals.regression.items import GradedItem, export_base, graded_items
from evals.regression.questions import Stage
from evals.regression.store import Store, read_json

from .records import JudgeCaseHandoff

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


@dataclass(frozen=True)
class JudgeInput:
    stage: Stage
    item: str
    files: tuple[Path, ...]
    line: str = ""
    shows: str = ""
    product_name: str = ""
    product_text: str = ""
    page_photos: tuple[Path, ...] = ()
    page_photo_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.stage not in ("pictures", "clips", "ad"):
            raise ValueError(f"No judge questions at stage {self.stage!r}")
        if len(self.page_photo_labels) != len(self.page_photos):
            raise ValueError("Every page photo needs a label (empty when none was saved)")

    def case_handoff(self) -> JudgeCaseHandoff:
        return {
            "stage": self.stage,
            "item": self.item,
            "line": self.line,
            "shows": self.shows,
            "product_name": self.product_name,
            "product_text": self.product_text,
            "page_photo_labels": list(self.page_photo_labels),
        }

    def case_files(self) -> tuple[Path, ...]:
        return (*self.files, *self.page_photos)


@dataclass(frozen=True)
class PageFacts:
    product_name: str
    product_text: str
    page_photos: tuple[Path, ...]
    page_photo_labels: tuple[str, ...]


def page_facts(store: Store, run: str, scenario: str) -> PageFacts:
    """Read only product facts and photo labels from a saved export.

    Rich records contain the app's checks and prompts too. None of those fields is
    copied: this adapter deliberately picks each allowed field instead of forwarding
    the record or any handoff from an app model call.
    """
    base = export_base(store, run, scenario)
    record = read_json(base / "regression.json")
    rich = read_json(base / "grading-records.json")
    page_files = record.get("page_files", [])
    page_photos = tuple(
        base / name for name in page_files if Path(name).suffix.lower() in IMAGE_SUFFIXES
    )
    if not page_files:
        page_photos = tuple(sorted(base.glob("page-photo-*")))
    library = {row["number"]: row for row in rich.get("library", [])}
    labels = []
    for photo in page_photos:
        if not photo.is_file():
            raise ValueError(f"Saved product photo is missing: {photo}")
        match = re.fullmatch(r"page-photo-(\d+)\.[^.]+", photo.name)
        row = library.get(int(match[1]), {}) if match else {}
        labels.append(str(row.get("label", "")))
    text_path = base / "page.txt"
    product_text = str(rich.get("page_text", ""))
    if not product_text and text_path.is_file():
        product_text = text_path.read_text(encoding="utf-8")
    return PageFacts(str(rich.get("product", "")), product_text, page_photos, tuple(labels))


def inputs_for_run_item(store: Store, run: str, scenario: str, item: GradedItem) -> JudgeInput:
    """The page facts plus the scene's words and media; nothing else from the records."""
    facts = page_facts(store, run, scenario)
    files = item.files
    if item.stage == "ad":
        files = tuple(
            path
            for scene in graded_items(store, run, scenario)
            if scene.stage == "clips"
            for path in scene.files
        )
        if not files:
            raise ValueError(f"{run}/{scenario}: no saved kept scene clips for A11")
    return JudgeInput(
        stage=item.stage,
        item=item.item,
        files=files,
        line=item.line,
        shows=item.shows,
        product_name=facts.product_name,
        product_text=facts.product_text,
        page_photos=facts.page_photos,
        page_photo_labels=facts.page_photo_labels,
    )
