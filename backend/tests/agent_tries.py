"""A fake fixing agent for door-1 tests, standing in for `agents.fixing_agent.fix_scene`
(the real one asks a model what to film): it films each scripted try with the app's own clip maker,
has the app's clip judge (faked at the gateway) rule on it, and stops on the first try the
judge passes, or gives up once the script runs out, as the real agent ends."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from django.utils import timezone

from agents import fixing_agent
from agents.models import ToolCall
from jobs import work
from jobs.models import AgentFixTry, ProducedItem, SceneStep


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
        cap: int = fixing_agent.MOST_TRIES,
    ) -> fixing_agent.Outcome:
        self.handed.append((step, door, failure, cap))
        # Kept as the real agent keeps its work: one AgentFix, each try filmed in a clip step
        # of its own, charged to its own film_again call.
        fixing = fixing_agent._started(step, door, failure, goal, cap)
        for number, motion in enumerate(self.tries, start=1):
            call = ToolCall.objects.create(
                session=step.tool_call.session,
                job=step.scene.job,
                agent=fixing_agent.AGENT,
                agent_fix=fixing,
                tool="film_again",
                call_id=f"fake-try-{fixing.pk}-{number}",
                arguments={"video_prompt": motion},
            )
            picture, audio = fixing_agent._current(fixing)
            tried = fixing_agent._try_step(
                fixing,
                call,
                SceneStep.Kind.CLIP,
                made_from=audio,
                picture=picture,
                picture_step=picture.step if picture is not None else None,
                motion_prompt=motion,
            )
            clip = work._make_clip(tried, motion=motion)
            ProducedItem.objects.filter(pk=clip.pk).update(remake=number, change=f"try {number}")
            clip.refresh_from_db()
            work._judge_clip(tried, clip)
            clip.refresh_from_db()
            passed = (clip.ruling or {}).get("verdict") != "fail"
            AgentFixTry.objects.create(
                agent_fix=fixing,
                number=number,
                tool_call=call,
                step=tried,
                picture=picture,
                clip=clip,
                scene_row=fixing_agent._row(step.scene),
                video_prompt=motion,
                passed=passed,
            )
            call.finished_at = timezone.now()
            call.save(update_fields=["finished_at"])
            if passed:
                fixing.passed = clip
                fixing.save(update_fields=["passed"])
                fixing_agent._finish(fixing)
                return fixing_agent.outcome(fixing)
        fixing.picked = self.picks
        fixing.save(update_fields=["picked"])
        fixing_agent.give_up(fixing, self.reason)
        return fixing_agent.outcome(fixing)


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
