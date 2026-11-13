"""The labeled cases the judge is tuned and scored on: one JSON line per item and question,
written by the data thread (prototype-brief.md, "Thread B: data").

    {id, split: tune|score, level: picture|clip|ad, question: "P1".."A12"|null,
     label: pass|fail, note, round, scenario, scene, take, media, talking, page_photos,
     line, ad_stills}

Lines that show the same media at the same level are one item: a picture or clip Julian
failed for two reasons has two lines. A pass line has question null (every question passed).
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_FILES = Path(os.environ.get("PROJECT_FILES", "/mnt/project-files"))
CASES = PROJECT_FILES / "eval-store/judge-cases/cases.jsonl"


@dataclass
class Item:
    """One picture, clip or finished ad, with every label Julian gave it."""

    key: str
    split: str
    level: str
    media: Path | None
    talking: bool
    page_photos: list[Path]
    line: str
    ad_stills: list[Path]
    # Ad level: the talking clips (hook included) and each one's spoken line, for A12.
    talking_clips: list[Path]
    talking_lines: list[str]
    scenario: str
    round: str
    scene: Any
    take: Any
    # The product's name and the shop's own description of it (empty when not collected).
    product_name: str = ""
    product_text: str = ""
    # Each line: {id, question, label, note}.
    labels: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        """A short name to print: scenario, scene, take, level, and the case ids."""
        where = f" s{self.scene}" if self.scene not in (None, "") else ""
        take = f" t{self.take}" if self.take not in (None, "") else ""
        ids = ",".join(each["id"] for each in self.labels)
        return f"r{self.round} {self.scenario}{where}{take} {self.level} [{ids}]"

    @property
    def label(self) -> str:
        """Julian's label for the whole item: fail when any line fails."""
        return "fail" if any(each["label"] == "fail" for each in self.labels) else "pass"

    def label_for(self, question: str) -> str | None:
        """Julian's label for one question: fail when a line names it; pass when he passed the
        item, or failed it only for other named questions (his rule: what he didn't complain
        about passed); None when a fail of his names no question (item level only)."""
        if any(each["question"] == question and each["label"] == "fail" for each in self.labels):
            return "fail"
        if any(each["label"] == "fail" and each["question"] is None for each in self.labels):
            return None
        return "pass"

    def notes(self) -> str:
        return " | ".join(
            f"{each['question'] or '-'}: {each['note']}" for each in self.labels if each["note"]
        )


def resolve(path: str | None, base: Path) -> Path | None:
    """A media path as the data thread wrote it: absolute, `project-files:...`, or relative
    to the cases file's folder."""
    if not path:
        return None
    if path.startswith("project-files:"):
        return PROJECT_FILES / path.removeprefix("project-files:")
    if ":" in path.split("/")[0]:
        raise ValueError(f"{path!r} can't be read here (only project-files or local paths)")
    given = Path(path)
    return given if given.is_absolute() else base / given


def load(path: Path = CASES) -> list[Item]:
    """Every item in the cases file, in file order."""
    items: dict[str, Item] = {}
    base = path.parent
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        raw = json.loads(text)
        level = raw["level"]
        if level not in {"picture", "clip", "ad"}:
            raise ValueError(f"{path}:{number}: level {level!r}")
        if level == "ad":
            # A11 and A12 lines of one ad are one item.
            key = f"{raw['split']}|ad|r{raw.get('round')}|{raw.get('scenario') or raw['id']}"
        else:
            key = f"{raw['split']}|{level}|{raw.get('media') or raw['id']}"
        item = items.get(key)
        if item is not None:
            # Fields one line of an ad has and another lacks are taken from whichever has them.
            for name in ("ad_stills", "page_photos", "talking_clips"):
                if not getattr(item, name) and raw.get(name):
                    setattr(item, name, [p for p in (resolve(x, base) for x in raw[name]) if p])
            if not item.talking_lines and raw.get("talking_lines"):
                item.talking_lines = list(raw["talking_lines"])
        if item is None:
            item = items[key] = Item(
                key=key,
                split=raw["split"],
                level=level,
                media=resolve(raw.get("media"), base),
                talking=bool(raw.get("talking")),
                page_photos=[
                    p for p in (resolve(x, base) for x in raw.get("page_photos") or []) if p
                ],
                line=raw.get("line") or "",
                ad_stills=[p for p in (resolve(x, base) for x in raw.get("ad_stills") or []) if p],
                talking_clips=[
                    p for p in (resolve(x, base) for x in raw.get("talking_clips") or []) if p
                ],
                talking_lines=list(raw.get("talking_lines") or []),
                scenario=raw.get("scenario") or "",
                round=str(raw.get("round") or ""),
                scene=raw.get("scene"),
                take=raw.get("take"),
                product_name=raw.get("product_name") or "",
                product_text=raw.get("product_text") or "",
            )
        item.labels.append(
            {
                "id": raw["id"],
                "question": raw.get("question"),
                "label": raw["label"],
                "note": raw.get("note") or "",
            }
        )
    return list(items.values())
