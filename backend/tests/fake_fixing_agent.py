"""A scripted fixing agent for door-2 tests (fixing agent build, T3): each try it is given
is made as the agent's try would be, a clip of the step judged as scripted, and the outcome
given back in the shape of spec section 11. The agent itself (T1, agents/fixing_agent.py) is
built in parallel: until it is merged, this module stands in for it, with only the names
door 2 reads (`Failure`, `Outcome`, `fix_scene`); once it is, only `fix_scene` is replaced.
T4 removes the stand-in half."""

import importlib
import sys
import types
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

import agents
from adforge import file_store
from jobs import work
from jobs.models import ProducedItem, SceneStep


@dataclass(frozen=True)
class ScriptedTry:
    """One try: whether the clip judge passes it, and whether it finds the shop owner's
    change made, with what is still wrong when not."""

    judge: str = "pass"
    change_made: bool = True
    still_wrong: str = "The tablet still lies on the table."


@dataclass
class FakeAgent:
    """What the agent is scripted to do, and what it was handed."""

    tries: list[ScriptedTry] = field(default_factory=list)
    gave_up: str = ""
    handed: list[dict[str, Any]] = field(default_factory=list)

    def fix_scene(
        self, step: SceneStep, *, door: str, failure: Any, goal: str | None, cap: int = 6
    ) -> Any:
        module = sys.modules["agents.fixing_agent"]
        self.handed.append({"step": step.pk, "door": door, "failure": failure, "goal": goal})
        before = step.shown_before(ProducedItem.Kind.CLIP)
        assert before is not None, "a fix row's scene has a clip"
        made = []
        for number, scripted in enumerate(self.tries, start=1):
            clip = ProducedItem.objects.create(
                job=step.scene.job,
                scene=step.scene,
                step=step,
                kind=ProducedItem.Kind.CLIP,
                version=work._next_version(step.scene, ProducedItem.Kind.CLIP),
                file=file_store.save(
                    f"scene{step.scene.number}_try{number}.mp4", file_store.read(before.file)
                ),
                seconds=before.seconds,
                made_from=step.made_from,
                picture=step.picture,
                remake=number,
                change=f"try {number}",
                ruling=_ruling(scripted, before),
            )
            made.append(clip)
            # As the agent does: a try passes only when the judge passed it and found the
            # change made.
            if scripted.judge == "pass" and not work.change_not_made(clip):
                return module.Outcome(
                    passed=clip, tries=made, gave_up=None, cost_usd=Decimal("1.65") * number
                )
        return module.Outcome(
            passed=None,
            tries=made,
            gave_up=self.gave_up or "it couldn't film the change well.",
            cost_usd=Decimal("1.65") * len(made),
        )


def _ruling(scripted: ScriptedTry, before: ProducedItem) -> dict[str, Any]:
    owner_change = (
        {"verdict": "fixed", "still_wrong": None}
        if scripted.change_made
        else {"verdict": "not fixed", "still_wrong": scripted.still_wrong}
    )
    failures = (
        [] if scripted.judge == "pass" else [{"check": "motion", "problem": "The hand jumps."}]
    )
    return {
        "verdict": scripted.judge,
        "checks": {},
        "failed_answers": len(failures),
        "failures": failures,
        "compared_with": before.pk,
        "answers": {
            "sides": [],
            "motion": [{"verdict": scripted.judge, "owner_change": owner_change}] * 3,
        },
    }


@dataclass(frozen=True)
class _Failure:
    note: str
    goal: str | None
    ruling: dict[str, Any]


@dataclass(frozen=True)
class _Outcome:
    passed: ProducedItem | None
    tries: list[ProducedItem]
    gave_up: str | None
    cost_usd: Decimal


def install(monkeypatch: pytest.MonkeyPatch, agent: FakeAgent) -> FakeAgent:
    """Have `agent` stand in for the fixing agent's `fix_scene`."""
    try:
        module = importlib.import_module("agents.fixing_agent")
    except ModuleNotFoundError:
        module = types.ModuleType("agents.fixing_agent")
        module.Failure = _Failure  # type: ignore[attr-defined]
        module.Outcome = _Outcome  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "agents.fixing_agent", module)
        monkeypatch.setattr(agents, "fixing_agent", module, raising=False)
    monkeypatch.setattr(module, "fix_scene", agent.fix_scene, raising=False)
    return agent
