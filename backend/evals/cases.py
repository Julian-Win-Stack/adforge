"""The eval case format (issue #386): one file per question, `evals/cases/<question>.jsonl`,
one case per line. A question is one yes/no a check answers; a case is one thing Julian graded
for it, with what the check was given and every verdict the check has said. The format and how
to add a case are in `evals/README.md`.

`load` refuses a whole file when any case in it is malformed, naming the file and line, so a
count is never made on half-read cases.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).parent / "cases"

LABELS = ("pass", "fail")
VERDICTS = ("pass", "fail", "unsure")
KINDS = ("capability", "regression")
HALVES = ("tune", "score")
# Where a picture or clip a case points at lives: none of them are in git.
PLACES = ("project-files:", "azure:", "mac:")

FIELDS = ("id", "kind", "half", "inputs", "label", "reason", "source", "verdicts")
SOURCE_FIELDS = ("round", "ad", "graded_at", "from")
VERDICT_FIELDS = ("verdict", "reason", "judge", "model", "at")


class BadCase(ValueError):
    """A case file that can't be counted: its message is `<file>:<line>: <what is wrong>`."""


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    judge: str
    model: str
    at: str


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    half: str
    label: str
    reason: str
    inputs: dict[str, Any]
    source: dict[str, Any]
    verdicts: tuple[Verdict, ...]


def half_for(case_id: str) -> str:
    """The half a case belongs to, fixed by its id so it is never picked by hand: a check's
    prompt is tuned on `tune` cases only and scored on `score` cases."""
    return HALVES[hashlib.sha256(case_id.encode()).digest()[0] % 2]


def questions(folder: Path = FOLDER) -> list[str]:
    """Every question with a case file in `folder`, by name."""
    return sorted(path.stem for path in folder.glob("*.jsonl"))


def load(path: Path) -> list[Case]:
    """Every case in the file at `path`, in file order; blank lines are skipped."""
    cases: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = _case(line)
            if case.id in seen:
                raise ValueError(f"id {case.id!r} is used twice")
        except ValueError as wrong:
            raise BadCase(f"{path}:{number}: {wrong}") from None
        seen.add(case.id)
        cases.append(case)
    return cases


def _case(line: str) -> Case:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as wrong:
        raise ValueError(f"not JSON ({wrong.msg})") from None
    if not isinstance(raw, dict):
        raise ValueError("a case must be a JSON object")
    _fields(raw, FIELDS, "the case")

    case_id = _text(raw, "id", "the case", empty=False)
    kind = _one_of(raw, "kind", KINDS)
    half = _one_of(raw, "half", HALVES)
    if half != half_for(case_id):
        raise ValueError(f"half is {half!r}, but id {case_id!r} belongs in {half_for(case_id)!r}")
    label = _one_of(raw, "label", LABELS)
    reason = _text(raw, "reason", "the case")

    inputs = _object(raw["inputs"], "inputs")
    _fields(inputs, ("handoff", "files"), "inputs", exact=False)
    _object(inputs["handoff"], "inputs.handoff")
    if not isinstance(inputs["files"], list):
        raise ValueError("inputs.files must be a list")
    for file in inputs["files"]:
        if not isinstance(file, str) or not any(
            file.startswith(place) and len(file) > len(place) for place in PLACES
        ):
            raise ValueError(
                f"inputs.files has {file!r}: a path must start with one of {', '.join(PLACES)}"
            )

    source = _object(raw["source"], "source")
    _fields(source, SOURCE_FIELDS, "source", exact=False)
    for field in SOURCE_FIELDS:
        _text(source, field, "source")

    if not isinstance(raw["verdicts"], list):
        raise ValueError("verdicts must be a list")
    verdicts = tuple(_verdict(each, n) for n, each in enumerate(raw["verdicts"], start=1))

    return Case(case_id, kind, half, label, reason, inputs, source, verdicts)


def _verdict(raw: Any, number: int) -> Verdict:
    where = f"verdict {number}"
    said = _object(raw, where)
    _fields(said, VERDICT_FIELDS, where)
    try:
        _one_of(said, "verdict", VERDICTS)
    except ValueError as wrong:
        raise ValueError(f"{where}: {wrong}") from None
    return Verdict(*(_text(said, field, where) for field in VERDICT_FIELDS))


def _fields(raw: dict[str, Any], fields: tuple[str, ...], where: str, exact: bool = True) -> None:
    if missing := [field for field in fields if field not in raw]:
        raise ValueError(f"{where} is missing {', '.join(missing)}")
    if exact and (unknown := sorted(set(raw) - set(fields))):
        raise ValueError(f"{where} has unknown field {', '.join(unknown)}")


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a JSON object")
    return value


def _text(raw: dict[str, Any], field: str, where: str, empty: bool = True) -> str:
    value = raw[field]
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{where}: {field} must be {'text' if empty else 'non-empty text'}")
    return value


def _one_of(raw: dict[str, Any], field: str, allowed: tuple[str, ...]) -> str:
    value = raw[field]
    if value not in allowed:
        raise ValueError(f"{field} is {value!r}, not one of {', '.join(allowed)}")
    return str(value)
