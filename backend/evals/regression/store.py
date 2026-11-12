"""The regression eval's lasting files. JSON writes replace whole files atomically."""

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict

type Verdict = Literal["pass", "fail", "needs_label", "not_reached"]


class AnswerItem(TypedDict):
    item: str
    verdict: Verdict
    reason: str


class Answer(TypedDict):
    verdict: Verdict
    reason: str
    items: list[AnswerItem]


class FirstTry(TypedDict):
    scenes: int
    first_passed: int
    clips: int


class Answers(TypedDict):
    scenario: str
    run: str
    answers: dict[str, Answer]
    first_try: FirstTry
    cost_usd: float


RunRecord = TypedDict(
    "RunRecord",
    {
        "name": str,
        "app_commit": str,
        "to": Literal["plans", "pictures", "full"],
        "from": Literal["plan", "pictures", "clips", "notes"] | None,
        "started_at": str,
        "finished_at": str | None,
        "estimate_usd": float,
        "spend_usd": float,
        "scenarios": list[str],
    },
)


class Grade(TypedDict):
    grade: Literal["pass", "fail"] | None
    note: str
    at: str


class IntendedChange(TypedDict):
    scenario: str
    question: str
    why: str
    at: str


def read_json(path: Path, default: Any = None) -> Any:
    """Only a missing file is empty. Malformed files remain visible errors."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {} if default is None else default


def atomic_write_text(path: Path, text: str) -> None:
    """Flush a sibling temporary file, rename it, then flush the directory entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _key(value: str) -> str:
    if not value or value in (".", "..") or any(c in value for c in ("/", "\\", "\0")):
        raise ValueError(f"Not a store key: {value!r}")
    return value


class Store:
    """Named paths and file helpers shared by the runner, grading and report commands."""

    def __init__(self, root: Path | str | None = None) -> None:
        location = root if root is not None else os.environ.get("EVAL_STORE")
        if not location:
            raise ValueError("Choose the lasting store with --store <dir> or EVAL_STORE")
        self.root = Path(location).expanduser()

    def page_dir(self, scenario: str) -> Path:
        return self.root / "pages" / _key(scenario)

    def run_dir(self, run: str) -> Path:
        return self.root / "runs" / _key(run)

    def scenario_dir(self, run: str, scenario: str) -> Path:
        return self.run_dir(run) / _key(scenario)

    def dump_path(self, run: str, scenario: str) -> Path:
        return self.scenario_dir(run, scenario) / "db.dump"

    def media_dir(self, run: str, scenario: str) -> Path:
        return self.scenario_dir(run, scenario) / "media"

    def export_dir(self, run: str, scenario: str) -> Path:
        return self.scenario_dir(run, scenario) / "export"

    def read_run(self, run: str) -> dict[str, Any]:
        return dict(read_json(self.run_dir(run) / "run.json"))

    def write_run(self, run: str, value: Mapping[str, Any]) -> None:
        write_json(self.run_dir(run) / "run.json", value)

    def read_answers(self, run: str, scenario: str) -> dict[str, Any]:
        return dict(read_json(self.scenario_dir(run, scenario) / "answers.json"))

    def write_answers(self, run: str, scenario: str, value: Mapping[str, Any]) -> None:
        write_json(self.scenario_dir(run, scenario) / "answers.json", value)

    def read_grades(self, run: str) -> dict[str, Any]:
        return dict(read_json(self.run_dir(run) / "grades.json"))

    def write_grades(self, run: str, value: Mapping[str, Any]) -> None:
        write_json(self.run_dir(run) / "grades.json", value)

    def read_grades_markdown(self, run: str) -> str:
        try:
            return (self.run_dir(run) / "grades.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def write_grades_markdown(self, run: str, value: str) -> None:
        atomic_write_text(self.run_dir(run) / "grades.md", value)

    def read_baselines(self) -> dict[str, str]:
        return dict(read_json(self.root / "baseline.json"))

    def write_baselines(self, value: Mapping[str, str]) -> None:
        write_json(self.root / "baseline.json", value)

    def read_intended(self) -> list[dict[str, Any]]:
        return list(read_json(self.root / "intended.json", []))

    def write_intended(self, value: Sequence[Mapping[str, Any]]) -> None:
        write_json(self.root / "intended.json", value)

    def read_photo_labels(self) -> dict[str, dict[str, str]]:
        return dict(read_json(self.root / "photo-labels.json"))

    def write_photo_labels(self, value: Mapping[str, Mapping[str, str]]) -> None:
        write_json(self.root / "photo-labels.json", value)

    def scenario_done(self, run: str, scenario: str) -> bool:
        return (self.scenario_dir(run, scenario) / "done").is_file()

    def mark_done(self, run: str, scenario: str) -> None:
        """Call last, after the dump, media and answers have been saved."""
        atomic_write_text(self.scenario_dir(run, scenario) / "done", "done\n")
