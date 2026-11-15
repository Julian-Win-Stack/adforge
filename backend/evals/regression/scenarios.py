"""The fixed product and situation replayed by each regression scenario."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

CATALOGUE = Path(__file__).with_name("scenarios.json")


@dataclass(frozen=True)
class Scenario:
    key: str
    link: str | None
    situation: str
    graded_round: str
    graded_ad: str | None
    owner_words: str = ""
    owner_notes: list[str] = field(default_factory=list)

    @property
    def first_message(self) -> str:
        if not self.link:
            raise ValueError(f"{self.key}: no product link in the saved grading records")
        return self.link + (f"\n{self.owner_words}" if self.owner_words else "")


def load_scenarios(path: Path = CATALOGUE) -> list[Scenario]:
    scenarios = [Scenario(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
    keys: set[str] = set()
    for scenario in scenarios:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scenario.key):
            raise ValueError(f"Invalid scenario key: {scenario.key}")
        if scenario.key in keys:
            raise ValueError(f"Repeated scenario key: {scenario.key}")
        keys.add(scenario.key)
    return scenarios


def select_scenarios(keys: Sequence[str] | None = None) -> list[Scenario]:
    scenarios = load_scenarios()
    if not keys:
        return scenarios
    unknown = set(keys) - {scenario.key for scenario in scenarios}
    if unknown:
        raise ValueError(f"Unknown scenarios: {', '.join(sorted(unknown))}")
    return [scenario for scenario in scenarios if scenario.key in keys]
