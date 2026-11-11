"""A scene's row changes independently of drawing and filming."""

from typing import ClassVar, Literal

from django.db import transaction
from pydantic import Field, StrictInt, field_validator

from agents.loop import Refused, Tool
from agents.models import ToolCall
from jobs import plan_edits, work
from jobs.models import QUALITY_FIELDS, Job, Scene
from jobs.owner_direction import (
    is_owner_line,
    keeps_exact_line,
    owner_steering,
    remember_line,
    tell_line_change,
    words_to_keep,
)
from jobs.plan_rules import WOULD_BREAK, ProductState, TalkingStyle, a_scene_change_breaks
from jobs.planning import (
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
    fact checked against the page and all owner messages, whose stated facts win. Scenes can
    change kind: explain
    why her hands cannot show the line when choosing B-roll. When the line stays but the
    action changes, the first call shows you the line to decide whether it fits: call again
    to confirm."""

    name: ClassVar[str] = "change_scene"
    line: str = Field(description="The scene's exact line, kept or a new true one.")
    line_change_reason: str = Field(
        default="",
        description="When checked owner words must change for a fact problem or video-model "
        "limit, explain that specific reason; the owner is told the old and new lines.",
    )
    shows: str = Field(description="The one action, or only the end result, in one sentence.")
    usage: str = Field(description="How the product is used; empty when not needed.")
    result: str = Field(description='The result for "does a job"; empty otherwise.')
    broll_kind: Literal["does a job", "showcase", "shows the problem"]
    person_shown: Literal["no face", "has face"]
    kind: Literal["talking", "broll"] | None = Field(
        default=None,
        description="For an H3-voiced ad: talking on camera or B-roll, her face out of frame "
        "unless the B-roll is a one-sided brush on her hair.",
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
        wanted = self.model_dump(exclude={"kind", "kind_why", "line_change_reason", *kept})
        if wanted.get("shows_to_lens") is None and "shows_to_lens" not in kept:
            wanted["shows_to_lens"] = False
        kind = self.kind or scene.kind
        try:
            if kind != scene.kind and not owner_steering(scene.job, lifecycle.owner_asked(run)):
                check_opening_kind(scene.number, kind)
        except ValueError as error:
            raise Refused(str(error)) from None
        why = (self.kind_why if self.kind_why is not None else scene.kind_why).strip()
        if kind == Scene.Kind.BROLL and not why:
            raise Refused("say why her hands cannot show this line at arm's length for B-roll.")
        wanted.update(
            kind=kind,
            kind_why=why if kind == Scene.Kind.BROLL else "",
            person_shown=work.face_kept(scene.kind, scene.person_shown, kind, self.person_shown),
        )
        lifecycle._refuse_a_missing_own_place(scene, wanted["person_shown"])
        wanted["line"] = " ".join(self.line.split())
        if not wanted["line"]:
            raise Refused("the scene's line cannot be blank.")
        lifecycle._validate_line(
            wanted["line"], owner_exact=keeps_exact_line(scene, wanted["line"])
        )
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
        if (
            owner_steering(scene.job, True)
            and wanted["line"] != scene.line
            and (scene.owner_words or is_owner_line(scene, scene.line))
            and not is_owner_line(scene, wanted["line"])
            and not self.line_change_reason.strip()
        ):
            raise Refused(
                "keep the owner’s exact line, or give line_change_reason naming the fact "
                "problem or video-model limit that requires changing it."
            )
        old_line = scene.line
        remember_line(scene)
        owner_words = words_to_keep(scene)
        old_action = scene.shows
        to_talking = scene.kind == Scene.Kind.BROLL and wanted["kind"] == Scene.Kind.TALKING
        scene.change_line(
            wanted["line"],
            wanted["shows"],
            broll={
                field: wanted[field] for field in ("usage", "result", "broll_kind", "person_shown")
            },
        )
        owner_replacement = is_owner_line(scene, wanted["line"]) and old_line != wanted["line"]
        if owner_replacement:
            remember_line(scene, replace=True)
        scene.kind = wanted["kind"]
        scene.kind_why = wanted["kind_why"]
        for field in QUALITY_FIELDS:
            if field in wanted:
                setattr(scene, field, wanted[field])
        if scene.kind == Scene.Kind.BROLL:
            scene.clear_talking_fields()
        _keeps_the_plan_rules(scene, owner_asked=lifecycle.owner_asked(run))
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
            _fact_checked(scene)
        # Recheck under the ad lock: two scene agents may change actions concurrently.
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=scene.job_id)
            _different_action(scene, wanted["shows"])
            broken = _keeps_the_plan_rules(scene, owner_asked=lifecycle.owner_asked(run))
            scene.save()
            plan_edits.let_through(scene.job, scene.number, "The scene agent", broken)
            also = plan_edits.keep_second_states(scene.job)
            scene.refresh_from_db(fields=["second_state"])
            if {"shows", "usage", "result", "kind"} & set(changed):
                also = [*also, lifecycle.more_picture_tries(run)]
            if to_talking:
                also = [*also, lifecycle.switched_to_talking(run)]
            also = [said for said in also if said]
        tell_line_change(
            scene,
            old_line,
            scene.line,
            self.line_change_reason.strip()
            or "the scene’s line needed to match the checked facts and video-model limits.",
            [] if owner_replacement else owner_words,
        )
        if mismatch is not None:
            cohesion.let_through(scene, mismatch)
        return (
            f"The scene row changed: {', '.join(changed)}. Nothing was drawn or filmed. "
            + " ".join(also)
        ).rstrip()


def _keeps_the_plan_rules(scene: Scene, *, owner_asked: bool = False) -> str | None:
    """Refused when the unsaved `scene` would have the plan newly break a whole-plan rule a
    change to one scene is still refused for (jobs.plan_rules.STILL_REFUSE_A_SCENE_CHANGE),
    or, as the last scene, drop the brand its line says, as a plan edit is refused. The other
    rules it newly breaks, said, to be let through, or None."""
    missing = plan_edits.packaging_missing(scene)
    if missing is not None:
        raise Refused(missing)
    before = plan_edits.plan_scenes(scene.job)
    after = [scene.plan_scene() if each.number == scene.number else each for each in before]
    steering = owner_steering(scene.job, owner_asked)
    breaks = a_scene_change_breaks(before, after)
    if breaks.refused is not None:
        raise Refused(f"{WOULD_BREAK} {breaks.refused}")
    if not steering and scene.number == max(each.number for each in before):
        was = Scene.objects.get(pk=scene.pk).line
        dropped = brand_dropped(was, scene.line, scene.job.brand)
        if dropped is not None:
            raise Refused(dropped)
    return breaks.let_through


def _different_action(scene: Scene, shows: str) -> None:
    for other in scene.job.scenes.exclude(pk=scene.pk).exclude(shows=""):
        if same_action(other.shows) == same_action(shows):
            raise Refused(f"scene {other.number} already films this action.")


def _fact_checked(scene: Scene) -> None:
    sent = work._to_check(scene)
    passed = lifecycle._passed(scene.job, scene.number)
    already = {
        side: lifecycle._side(sent.model_dump(), side) in passed[side] for side in lifecycle.CHECKED
    }
    if not all(already.values()):
        checked = work._ask_fact_check(
            scene.job,
            [sent],
            work._conversation(scene.job),
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
