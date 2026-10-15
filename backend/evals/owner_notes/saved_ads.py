"""Round 4's ads as they were saved (runs/round-4 in the project's shared folder): each ad's
product, the shop owner's answers, and each scene's line, labels, prompts, starting picture
and clip. The owner-notes run makes each ad again from these, paying for no video."""

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROUND_4 = Path("/mnt/project-files/runs/round-4")


@dataclass(frozen=True)
class SavedScene:
    number: int
    line: str
    # Empty for a scene where the person talks to camera.
    shows: str = ""
    broll_kind: str = ""
    person_shown: str = ""
    usage: str = ""
    result: str = ""
    needs: list[dict[str, Any]] = field(default_factory=list)
    picture_prompt: str = ""
    prompt_reason: str = ""
    motion_prompt: str = ""
    folder: Path = Path()

    @property
    def talking(self) -> bool:
        return not self.shows

    @property
    def picture(self) -> bytes:
        return (self.folder / "start-picture.png").read_bytes()

    @property
    def clip(self) -> bytes:
        return (self.folder / "clip.mp4").read_bytes()


@dataclass(frozen=True)
class SavedAd:
    key: str
    folder: Path
    product_name: str
    url: str
    answers: list[str]
    scenes: list[SavedScene]

    def scene(self, number: int) -> SavedScene:
        return next(scene for scene in self.scenes if scene.number == number)

    @property
    def broll(self) -> list[SavedScene]:
        return [scene for scene in self.scenes if not scene.talking]


def saved_ads(round_4: Path = ROUND_4) -> dict[str, SavedAd]:
    """Every ad saved in `round_4`, by its key ("8", "N5"...)."""
    products = _products(round_4 / "grades.md")
    answers = _answers(round_4 / "owner-answers.txt")
    ads = {}
    for folder in sorted(round_4.iterdir()):
        if not folder.is_dir() or not re.match(r"^(\d+|N\d+)-", folder.name):
            continue
        key = folder.name.split("-", 1)[0]
        name, url = products[key]
        scenes = sorted(
            (_scene(scene) for scene in folder.iterdir() if re.match(r"scene-\d+", scene.name)),
            key=lambda scene: scene.number,
        )
        ads[key] = SavedAd(key, folder, name, url, answers.get(key, []), scenes)
    return ads


def _products(grades: Path) -> dict[str, tuple[str, str]]:
    """Each ad's key -> (product name, product link), from the grading sheet's headings, such
    as "## #8 Toilet cleaner: Molly's Suds Toilet Bowl Cleaner (https://...)"."""
    found = {}
    for line in grades.read_text().splitlines():
        match = re.match(r"## #?(\w+) [^:]*: (.+?) \((https?://\S+)\)\s*$", line)
        if match:
            found[match.group(1)] = (match.group(2), match.group(3))
    return found


def _answers(saved: Path) -> dict[str, list[str]]:
    """Each ad's key -> what the shop owner answered while it was made, in order."""
    answers: dict[str, list[str]] = {}
    for line in saved.read_text().splitlines():
        match = re.match(r"#(\w+) \[[^\]]*\] -> (.+)$", line)
        if match:
            answers.setdefault(match.group(1), []).append(match.group(2).strip())
    return answers


_FIELDS = {
    "LINE": "line",
    "SHOWS": "shows",
    "BROLL_KIND": "broll_kind",
    "PERSON_SHOWN": "person_shown",
    "USAGE": "usage",
    "RESULT": "result",
    "PICTURE PROMPT": "picture_prompt",
    "PROMPT REASON": "prompt_reason",
    "MOTION PROMPT": "motion_prompt",
}


def _scene(folder: Path) -> SavedScene:
    text = (folder / "inputs.txt").read_text()
    values: dict[str, Any] = {}
    for key, name in _FIELDS.items():
        match = re.search(rf"^{re.escape(key)}: ?(.*)$", text, re.MULTILINE)
        values[name] = match.group(1).strip() if match else ""
    needs = re.search(r"^NEEDS: ?(.*)$", text, re.MULTILINE)
    # Saved as Python's repr of a list of dicts.
    values["needs"] = ast.literal_eval(needs.group(1)) if needs and needs.group(1) else []
    number = int(re.match(r"scene-(\d+)", folder.name).group(1))  # type: ignore[union-attr]
    return SavedScene(number=number, folder=folder, **values)


def as_planned(scene: SavedScene, photo_count: int) -> dict[str, Any]:
    """`scene` as the planner plans a scene. A need's photo numbers the page read again
    doesn't have are left out, and a need left with none is dropped."""
    if scene.talking:
        return {"line": scene.line}
    needs = []
    for need in scene.needs:
        photos = [number for number in need["photos"] if 1 <= number <= photo_count]
        if photos:
            needs.append({"what": need["what"], "photos": photos})
    return {
        "line": scene.line,
        "shows": scene.shows,
        "broll_kind": scene.broll_kind,
        "person_shown": scene.person_shown,
        "usage": scene.usage or None,
        "result": scene.result or None,
        "needs": needs,
    }


def dumps(ad: SavedAd) -> str:
    """The ad, as plain text for a record."""
    return json.dumps(
        {
            "key": ad.key,
            "product": ad.product_name,
            "url": ad.url,
            "scenes": [
                {"number": scene.number, "line": scene.line, "shows": scene.shows}
                for scene in ad.scenes
            ],
        },
        indent=1,
    )
