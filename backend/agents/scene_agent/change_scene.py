"""A scene's row changes independently of drawing and filming."""

from typing import ClassVar, Literal

from django.db import transaction
from pydantic import Field, StrictInt, field_validator

from agents.loop import Refused, Tool
from agents.models import ToolCall
from jobs import plan_edits, work
from jobs.models import QUALITY_FIELDS, AgentFix, Job, Scene
from jobs.plan_rules import WOULD_BREAK, ProductState, TalkingStyle, newly_broken
from jobs.planning import (
    ChatMessage,
    brand_dropped,
    check_opening_kind,
    packaging_photo_number,
    same_action,
)

from . import cohesion, lifecycle

# change_line also clears the old action's selected pictures and video piece.
SNAPSHOT = (*lifecycle.KEPT, "scene_pictures", "piece", "kind", "kind_why", "second_state")
REMINDER = (
    "The action changes but the line stays. Decide whether the line still fits the new "
    "action: if it says something the clip won't show, call change_scene again with a new "
    "line; if it still fits, call it again with the same line. Nothing was changed."
)


class ChangeScene(Tool):
    """Change your scene's row, drawing and filming nothing. An action may show only the
    end result. A changed scene is held to the plan's rules, and a changed action is checked
    against the other scenes: the same product, size and shared objects. Changed words are
    fact checked against the page and checked owner quotes; Scenes can change kind: explain
    why her hands cannot show the line when choosing B-roll. When the line stays but the
    action changes, the first call shows you the line to decide whether it fits: call again
    to confirm."""

    name: ClassVar[str] = "change_scene"
    line: str = Field(description="The scene's exact line, kept or a new true one.")
    shows: str = Field(description="The one action, or only the end result, in one sentence.")
    usage: str = Field(description="How the product is used; empty when not needed.")
    result: str = Field(description='The result for "does a job"; empty otherwise.')
    broll_kind: Literal["does a job", "showcase", "shows the problem"]
    person_shown: Literal["no face", "has face"]
    kind: Literal["talking", "broll"] | None = Field(
        default=None,
        description="For an H3-voiced ad: talking on camera or B-roll with her face out of frame.",
    )
    kind_why: str | None = Field(
        default=None,
        description="For B-roll, what her hands cannot show at arm's length; kept when omitted.",
    )
    talking_style: TalkingStyle | None = Field(
        default=None, description="For a talking scene, how she is filmed; kept when omitted."
    )
    product_state: ProductState | None = Field(
        default=None, description="For a talking scene, where the product is; kept when omitted."
    )
    shows_to_lens: bool | None = Field(
        default=None,
        description="Whether the action only shows the product to the lens; kept when omitted, "
        "false when omitted with a new action.",
    )
    packaging_photo: StrictInt | None = Field(
        default=None,
        description="The library number of the packaging photo, for a box state; kept when "
        "omitted.",
    )

    @field_validator("packaging_photo")
    @classmethod
    def _a_library_number(cls, number: int | None) -> int | None:
        return packaging_photo_number(number)

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
        kept = {field for field in QUALITY_FIELDS if getattr(self, field) is None}
        if "shows_to_lens" in kept and self.shows.strip() != scene.shows:
            # The old action's flag would skip the lens rule for the new one.
            kept.remove("shows_to_lens")
        wanted = self.model_dump(exclude={"kind", "kind_why", *kept})
        if wanted.get("shows_to_lens") is None and "shows_to_lens" not in kept:
            wanted["shows_to_lens"] = False
        kind = self.kind or scene.kind
        try:
            check_opening_kind(scene.number, kind)
        except ValueError as error:
            raise Refused(str(error)) from None
        why = (self.kind_why if self.kind_why is not None else scene.kind_why).strip()
        if kind == Scene.Kind.BROLL and not why:
            raise Refused("say why her hands cannot show this line at arm's length for B-roll.")
        wanted.update(
            kind=kind,
            kind_why=why if kind == Scene.Kind.BROLL else "",
            person_shown=work.person_shown_for(kind),
        )
        lifecycle._refuse_a_missing_own_place(scene, wanted["person_shown"])
        wanted["line"] = " ".join(self.line.split())
        if not wanted["line"]:
            raise Refused("the scene's line cannot be blank.")
        lifecycle._validate_line(wanted["line"])
        for field in ("shows", "usage", "result"):
            wanted[field] = wanted[field].strip()
        _different_action(scene, wanted["shows"])
        if (
            lifecycle._same_words(wanted["line"], scene.line)
            and same_action(wanted["shows"]) != same_action(scene.shows)
            and not any(
                same_action(earlier.arguments.get("shows", "")) == same_action(wanted["shows"])
                and lifecycle._same_words(earlier.arguments.get("line", ""), scene.line)
                for earlier in lifecycle._calls(fixing).filter(
                    tool=self.name, created_at__gte=run.created_at, result__contains=REMINDER
                )
            )
        ):
            return f'The scene\'s line says: "{scene.line}" {REMINDER}'
        changed = [field for field in wanted if wanted[field] != getattr(scene, field)]
        if not changed:
            return "Nothing changed: the scene row is already this."
        old_action = scene.shows
        scene.change_line(
            wanted["line"],
            wanted["shows"],
            broll={
                field: wanted[field] for field in ("usage", "result", "broll_kind", "person_shown")
            },
        )
        scene.kind = wanted["kind"]
        scene.kind_why = wanted["kind_why"]
        for field in QUALITY_FIELDS:
            if field in wanted:
                setattr(scene, field, wanted[field])
        if scene.kind == Scene.Kind.BROLL:
            scene.clear_talking_fields()
        _keeps_the_plan_rules(scene)
        mismatch = (
            cohesion.checked(scene, old_action, run)
            if {"shows", "usage", "result"} & set(changed)
            else None
        )
        if {"kind", "person_shown"} & set(changed):
            scene.scene_pictures = []
            scene.piece = None
            scene.status = Scene.Status.PLANNED
        if {"line", "shows", "usage", "result"} & set(changed):
            _fact_checked(scene, fixing)
        # Recheck under the ad lock: two scene agents may change actions concurrently.
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=scene.job_id)
            _different_action(scene, wanted["shows"])
            _keeps_the_plan_rules(scene)
            scene.save()
            also = plan_edits.keep_second_states(scene.job)
            scene.refresh_from_db(fields=["second_state"])
        if mismatch is not None:
            cohesion.let_through(scene, mismatch)
        return (
            f"The scene row changed: {', '.join(changed)}. Nothing was drawn or filmed. "
            + " ".join(also)
        ).rstrip()


def _keeps_the_plan_rules(scene: Scene) -> None:
    """Refused when the unsaved `scene` would have the plan break a whole-plan rule it keeps
    now (jobs.plan_rules), or, as the last scene, drop the brand its line says, as a plan
    edit is refused."""
    missing = plan_edits.packaging_missing(scene)
    if missing is not None:
        raise Refused(missing)
    before = plan_edits.plan_scenes(scene.job)
    after = [scene.plan_scene() if each.number == scene.number else each for each in before]
    broken = newly_broken(before, after)
    if broken is not None:
        raise Refused(f"{WOULD_BREAK} {broken}")
    if scene.number == max(each.number for each in before):
        was = Scene.objects.get(pk=scene.pk).line
        dropped = brand_dropped(was, scene.line, scene.job.brand)
        if dropped is not None:
            raise Refused(dropped)


def _different_action(scene: Scene, shows: str) -> None:
    for other in scene.job.scenes.exclude(pk=scene.pk).exclude(shows=""):
        if same_action(other.shows) == same_action(shows):
            raise Refused(f"scene {other.number} already films this action.")


def _fact_checked(scene: Scene, fixing: AgentFix) -> None:
    from . import current_run

    sent = work._to_check(scene)
    passed = lifecycle._passed(scene.job, scene.number)
    already = {
        side: lifecycle._side(sent.model_dump(), side) in passed[side] for side in lifecycle.CHECKED
    }
    if not all(already.values()):
        quotes = current_run(fixing).quotes
        checked = work._ask_fact_check(
            scene.job,
            [sent],
            [ChatMessage(by="user", text=quote["text"]) for quote in quotes],
            pay_once=True,
        )
        verdict = checked.lines[0]
        blamed = ("line", "shows") if verdict.wrong == "both" else (verdict.wrong,)
        if verdict.verdict != "ok" and not all(already.get(side or "", False) for side in blamed):
            raise Refused(
                f"the fact check failed it: {verdict.problem} The page says: {verdict.page_says}"
            )
    scene.fact_checked = True
    scene.fact_problems = []
