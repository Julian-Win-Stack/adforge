"""A fake fixing agent for door-1 tests, standing in for `agents.fixing_agent.fix_scene`
(ticket T1 builds the real one): it films each scripted try with the app's own clip maker,
has the app's clip judge (faked at the gateway) rule on it, and stops on the first try the
judge passes, or gives up once the script runs out, as the real agent ends."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from agents import fixing_agent
from jobs import work
from jobs.models import ProducedItem, SceneStep


@dataclass
class FakeAgent:
    # The video prompt of each try it films, in order.
    tries: list[str]
    # Why it gives up, said once the script runs out.
    reason: str = "Every try kept the same fault."
    # Which try it would pick on a give-up, from 1; None for none.
    picks: int | None = None
    # Each time it was handed a scene: the step, the door and what failed.
    handed: list[tuple[SceneStep, str, fixing_agent.Failure, int]] = field(default_factory=list)

    def fix_scene(
        self,
        step: SceneStep,
        *,
        door: str,
        failure: fixing_agent.Failure,
        goal: str | None,
        cap: int = 6,
    ) -> fixing_agent.Outcome:
        self.handed.append((step, door, failure, cap))
        row = {
            "line": step.line,
            "shows": step.shows,
            "result": step.broll_result,
            "broll_kind": step.broll_kind,
            "person_shown": step.person_shown,
        }
        made: list[fixing_agent.Try] = []
        for number, motion in enumerate(self.tries, start=1):
            clip = work._make_clip(step, remake=number, motion=motion)
            ProducedItem.objects.filter(pk=clip.pk).update(change=f"try {number}")
            clip.refresh_from_db()
            work._judge_clip(step, clip)
            clip.refresh_from_db()
            made.append(fixing_agent.Try(clip=clip, scene_row=row, picked=number == self.picks))
            if clip.ruling and clip.ruling["verdict"] != "fail":
                return fixing_agent.Outcome(passed=clip, tries=made, gave_up=None)
        return fixing_agent.Outcome(passed=None, tries=made, gave_up=self.reason)


def agent_films(
    monkeypatch: pytest.MonkeyPatch,
    *tries: str,
    reason: str = "Every try kept the same fault.",
    picks: int | None = None,
) -> FakeAgent:
    """Have the fixing agent film `tries`, each judged, until one passes or it gives up."""
    agent = FakeAgent(list(tries), reason, picks)
    monkeypatch.setattr(fixing_agent, "fix_scene", agent.fix_scene)
    return agent


def agent_stops(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Have the fixing agent stop with `error` before it films anything."""

    def stopped(*_: Any, **__: Any) -> fixing_agent.Outcome:
        raise error

    monkeypatch.setattr(fixing_agent, "fix_scene", stopped)
