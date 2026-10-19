"""A scripted fixing agent for door-2 tests (fixing agent build, T3): each try it is given
is kept as the agent keeps its tries, its clip judged as scripted, and the outcome given back
as the agent gives it."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from django.utils import timezone

from adforge import file_store
from agents import fixing_agent
from agents.models import ToolCall
from jobs import work
from jobs.models import AgentFixTry, ProducedItem, SceneStep


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
        self,
        step: SceneStep,
        *,
        door: str,
        failure: fixing_agent.Failure,
        goal: str | None,
        cap: int = 6,
    ) -> fixing_agent.Outcome:
        self.handed.append({"step": step.pk, "door": door, "failure": failure, "goal": goal})
        before = step.shown_before(ProducedItem.Kind.CLIP)
        assert before is not None, "a fix row's scene has a clip"
        # Kept as the real agent keeps its work: one AgentFix, each try in a clip step of its
        # own, charged to its own film_again call.
        fixing = fixing_agent._started(step, door, failure, goal, cap)
        for number, scripted in enumerate(self.tries, start=1):
            call = ToolCall.objects.create(
                session=step.tool_call.session,
                job=step.scene.job,
                agent=fixing_agent.AGENT,
                agent_fix=fixing,
                tool="film_again",
                call_id=f"fake-try-{fixing.pk}-{number}",
                arguments={},
                finished_at=timezone.now(),
            )
            tried = fixing_agent._try_step(
                fixing,
                call,
                SceneStep.Kind.CLIP,
                made_from=before.made_from,
                picture=before.picture,
                picture_step=before.picture.step if before.picture else None,
            )
            clip = ProducedItem.objects.create(
                job=step.scene.job,
                scene=step.scene,
                step=tried,
                kind=ProducedItem.Kind.CLIP,
                version=work._next_version(step.scene, ProducedItem.Kind.CLIP),
                file=file_store.save(
                    f"scene{step.scene.number}_try{number}.mp4", file_store.read(before.file)
                ),
                seconds=before.seconds,
                made_from=before.made_from,
                picture=before.picture,
                remake=number,
                change=f"try {number}",
                ruling=_ruling(scripted, before),
            )
            # As the agent does: a try passes only when the judge passed it and found the
            # change made.
            passed = scripted.judge == "pass" and not work.change_not_made(clip)
            AgentFixTry.objects.create(
                agent_fix=fixing,
                number=number,
                tool_call=call,
                step=tried,
                picture=before.picture,
                clip=clip,
                scene_row=fixing_agent._row(step.scene),
                passed=passed,
            )
            if passed:
                fixing.passed = clip
                fixing.save(update_fields=["passed"])
                fixing_agent._finish(fixing)
                return fixing_agent.outcome(fixing)
        fixing_agent.give_up(fixing, self.gave_up or "it couldn't film the change well.")
        return fixing_agent.outcome(fixing)


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


def install(monkeypatch: pytest.MonkeyPatch, agent: FakeAgent) -> FakeAgent:
    """Have `agent` stand in for the fixing agent's `fix_scene`."""
    monkeypatch.setattr(fixing_agent, "fix_scene", agent.fix_scene)
    return agent
