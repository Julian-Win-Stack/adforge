"""Regression grades use the existing eval case format and its fixed split."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from evals.cases import half_for


def case_id(run: str, scenario: str, question: str, item: str) -> str:
    if not re.fullmatch(r"page|plan|s[1-9]\d*|ad|note[1-9]\d*", item):
        raise ValueError(f"Unknown graded item: {item!r}")
    return f"reg-{run}-{scenario}-{question}-{item}"


def kind_for(label: str) -> str:
    if label not in ("pass", "fail"):
        raise ValueError(f"Unknown grade label: {label!r}")
    return "regression" if label == "pass" else "capability"


def make_case(
    *,
    run: str,
    scenario: str,
    question: str,
    item: str,
    label: str,
    reason: str,
    handoff: Mapping[str, Any],
    files: Sequence[str],
    graded_at: str,
    source: str,
) -> dict[str, Any]:
    """A serializable case. Files retain their location prefix; no media enters git."""
    identifier = case_id(run, scenario, question, item)
    if label == "fail" and not reason.strip():
        raise ValueError("A failed grade needs Julian's words")
    return {
        "id": identifier,
        "kind": kind_for(label),
        "half": half_for(identifier),
        "inputs": {"handoff": dict(handoff), "files": list(files)},
        "label": label,
        "reason": reason,
        "source": {"round": run, "ad": scenario, "graded_at": graded_at, "from": source},
        "verdicts": [],
    }
