"""A scene's row changes independently of drawing and filming."""

from typing import ClassVar, Literal

from django.db import transaction
from pydantic import Field

from agents import fixing_agent
from agents.loop import Refused, Tool
from agents.models import ToolCall
from jobs import work
from jobs.models import AgentFix, Job, Scene
from jobs.planning import ChatMessage, same_action

# change_line also clears the old action's selected pictures and video piece.
SNAPSHOT = (*fixing_agent.KEPT, "scene_pictures", "piece")
REMINDER = (
    "The action changes but the line stays. Decide whether the line still fits the new "
    "action: if it says something the clip won't show, call change_scene again with a new "
    "line; if it still fits, call it again with the same line. Nothing was changed."
)


class ChangeScene(Tool):
    """Change your scene's row, drawing and filming nothing. An action may show only the
    end result. Changed words are fact checked against the page and checked owner quotes;
    a changed line is voiced and heard first. When you keep the line but change the action,
    the first call shows you the line to decide whether it fits: call again to confirm."""

    name: ClassVar[str] = "change_scene"
    line: str = Field(description="The scene's exact line, kept or a new true one.")
    shows: str = Field(description="The one action, or only the end result, in one sentence.")
    usage: str = Field(description="How the product is used; empty when not needed.")
    result: str = Field(description='The result for "does a job"; empty otherwise.')
    broll_kind: Literal["does a job", "showcase", "shows the problem"]
    person_shown: Literal["no face", "has face"]

    def run(self, call: ToolCall) -> str:
        from . import current_run

        assert call.agent_fix is not None
        fixing = call.agent_fix
        run = current_run(fixing)
        if run.finished_at is not None:
            raise Refused("the scene's run has ended.")
        scene = fixing.step.scene
        scene.refresh_from_db()
        if not self.shows.strip():
            raise Refused("the scene must show an action or its result.")
        if self.broll_kind != "does a job" and self.result.strip():
            raise Refused('only a "does a job" scene has a result.')
        fixing_agent._refuse_a_missing_own_place(scene, self.person_shown)
        wanted = self.model_dump()
        wanted["line"] = " ".join(self.line.split())
        if not wanted["line"]:
            raise Refused("the scene's line cannot be blank.")
        for field in ("shows", "usage", "result"):
            wanted[field] = wanted[field].strip()
        _different_action(scene, wanted["shows"])
        if (
            fixing_agent._same_words(wanted["line"], scene.line)
            and same_action(wanted["shows"]) != same_action(scene.shows)
            and not any(
                same_action(earlier.arguments.get("shows", "")) == same_action(wanted["shows"])
                and fixing_agent._same_words(earlier.arguments.get("line", ""), scene.line)
                for earlier in fixing_agent._calls(fixing).filter(
                    tool=self.name, created_at__gte=run.created_at, result__contains=REMINDER
                )
            )
        ):
            return f'The scene\'s line says: "{scene.line}" {REMINDER}'
        changed = [field for field in wanted if wanted[field] != getattr(scene, field)]
        if not changed:
            return "Nothing changed: the scene row is already this."
        before = {field: getattr(scene, field) for field in SNAPSHOT}
        scene.change_line(
            wanted["line"],
            wanted["shows"],
            broll={
                field: wanted[field] for field in ("usage", "result", "broll_kind", "person_shown")
            },
        )
        if {"line", "shows", "usage", "result"} & set(changed):
            _fact_checked(scene, fixing)
        # Recheck under the ad lock: two scene agents may change actions concurrently.
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=scene.job_id)
            _different_action(scene, wanted["shows"])
            scene.save()
        try:
            audio = fixing_agent._voiced(fixing, call) if "line" in changed else None
        except BaseException:
            fixing_agent._put_back(scene, before)
            raise
        said = (
            f" The new line was voiced and heard; it takes {audio.seconds:g} s; "
            f"the clip is {work._broll_clip_seconds(audio.seconds or 0)} s."
            if audio is not None and audio.seconds is not None
            else ""
        )
        return f"The scene row changed: {', '.join(changed)}.{said} Nothing was drawn or filmed."


def _different_action(scene: Scene, shows: str) -> None:
    for other in scene.job.scenes.exclude(pk=scene.pk).exclude(shows=""):
        if same_action(other.shows) == same_action(shows):
            raise Refused(f"scene {other.number} already films this action.")


def _fact_checked(scene: Scene, fixing: AgentFix) -> None:
    from . import current_run

    sent = work._to_check(scene)
    passed = fixing_agent._passed(scene.job, scene.number)
    already = {
        side: fixing_agent._side(sent.model_dump(), side) in passed[side]
        for side in fixing_agent.CHECKED
    }
    if not all(already.values()):
        quotes = current_run(fixing).quotes
        checked = work._ask_fact_check(
            scene.job,
            [sent],
            [ChatMessage(by="user", text=quote["text"]) for quote in quotes],
            pay_once=True,
        )
        verdict = checked.lines[0] if checked.decision == "checked" and checked.lines else None
        if verdict is None:
            raise Refused(
                "the fact check couldn't tell whether it is true: "
                f"{checked.question or checked.reason}"
            )
        blamed = ("line", "shows") if verdict.wrong == "both" else (verdict.wrong,)
        if verdict.verdict != "ok" and not all(already.get(side or "", False) for side in blamed):
            raise Refused(
                f"the fact check failed it: {verdict.problem} The page says: {verdict.page_says}"
            )
    scene.fact_checked = True
    scene.fact_problems = []
