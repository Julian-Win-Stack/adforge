"""STUB for ticket T2 (door 1): the fixing agent's interface as spec section 11 fixes it, so
door 1 can be built and tested while T1 builds the agent itself. T4 replaces this file with
T1's `fixing_agent.py` when the tickets merge; nothing here is the agent."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from jobs.models import ProducedItem, SceneStep


@dataclass(frozen=True)
class Failure:
    """What the agent is handed as what went wrong: the judge's ruling of try 0 (door 1), or
    the shop owner's note and goal (door 2)."""

    ruling: dict[str, Any] | None = None
    note: str = ""


@dataclass(frozen=True)
class Try:
    """One film_again by the agent."""

    clip: ProducedItem
    # The scene row as filmed: line, shows, result, broll_kind, person_shown.
    scene_row: dict[str, Any] = field(default_factory=dict)
    picked: bool = False
    why: str = ""


@dataclass(frozen=True)
class Outcome:
    passed: ProducedItem | None
    tries: list[Try]
    gave_up: str | None
    cost_usd: Decimal = Decimal(0)


def fix_scene(
    step: SceneStep,
    *,
    door: Literal["judge", "owner"],
    failure: Failure,
    goal: str | None,
    cap: int = 6,
) -> Outcome:
    raise NotImplementedError("the fixing agent is built in ticket T1")
