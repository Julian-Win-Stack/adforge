"""Edits of the plan (spec #247 point 5): any field of any scene, a scene added, removed or
moved, as the producer decides. Each edit keeps the plan whole by the planner's rules for a
scene (its B-roll labels as its kind needs, an overlay a few words, a different action in
each B-roll scene), puts a changed line or what a scene shows through the planning checks
again (`fact_checked` cleared, as a new plan's lines are), and says which steps it made
stale (jobs.stale). Nothing is made here: the producer makes what it decides to with the
scene tools. Scenes are numbered 1, 2, 3... in the order they play, one scene per number,
and the chat's scene cards follow their scene to its new number."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Case, PositiveSmallIntegerField, Value, When
from django.db.models.functions import Cast
from django.utils import timezone
from pydantic import ValidationError

from chat.models import Attachment, Message

from . import notices, stale
from .models import (
    BROLL_FIELDS,
    PLANNED_FOR_THE_ACTION,
    QUALITY_FIELDS,
    AgentFix,
    Job,
    Scene,
    SceneStep,
)
from .notices import post_notice
from .owner_direction import (
    keeps_exact_line,
    owner_steering,
    remember_line,
    tell_line_change,
    words_to_keep,
)
from .plan_rules import WOULD_BREAK, PlanScene, a_scene_change_breaks, newly_broken
from .planning import (
    SAME_ACTION,
    H3PlannedScene,
    brand_dropped,
    check_opening_kind,
    repeated_action,
)
from .work import face_kept


class CantEdit(Exception):
    """The edit would break the plan, or what it edits is busy, and nothing was changed.
    Says why, for the producer."""


# What a scene's edit can change, each None to leave as it is. `shows` blank makes the
# person say the line to camera; `usage`, `result` and `overlay` blank are none.
EDITABLE = (
    "kind",
    "kind_why",
    "line",
    "shows",
    "broll_kind",
    "person_shown",
    "usage",
    "result",
    "part",
    "overlay",
    *QUALITY_FIELDS,
)
# What the planning checks check: a change to any of them has the scene checked again.
CHECKED = ("kind", "kind_why", "line", "shows", "broll_kind", "person_shown", "usage", "result")
# The fields an edit saves.
_SAVED = [
    "kind",
    "kind_why",
    "line",
    "shortened_from",
    "owner_words",
    "shows",
    *BROLL_FIELDS,
    *PLANNED_FOR_THE_ACTION,
    "part",
    "overlay",
    *QUALITY_FIELDS,
    "second_state",
    "status",
    "fact_checked",
    "fact_problems",
]


@dataclass
class Edited:
    """What an edit did, for the tool's answer: the fields it changed in each scene, what it
    made stale, and anything else code changed to keep the plan whole."""

    changed: dict[int, list[str]] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    to_check: list[int] = field(default_factory=list)
    also: list[str] = field(default_factory=list)


def edit_scene(
    scene: Scene,
    changes: dict[str, Any],
    *,
    owner_asked: bool = False,
    line_change_reason: str = "",
) -> Edited:
    """Change the fields of `scene` named in `changes` (EDITABLE; None leaves a field as it
    is). CantEdit, with nothing changed, when the scene that results breaks the plan, or
    while the fixing agent films the scene again: it changes the scene itself, and would
    overwrite the edit."""
    changes = {key: value for key, value in changes.items() if value is not None}
    with transaction.atomic():
        # The whole plan is locked, as a scene agent's change_scene locks it, so two changes
        # at once can't each keep the rules and together break them.
        Job.objects.select_for_update().get(pk=scene.job_id)
        scene = Scene.objects.select_for_update().get(pk=scene.pk)
        if (
            set(changes) - {"overlay"}
            and scene.steps.filter(
                kind=SceneStep.Kind.SCENE_AGENT, status=SceneStep.Status.RUNNING
            ).exists()
        ):
            raise CantEdit(
                f"scene {scene.number}'s agent is working; pass_note sends it the owner's change."
            )
        if (
            set(changes) - {"overlay"}
            and AgentFix.objects.filter(
                step__scene=scene,
                step__status=SceneStep.Status.RUNNING,
                finished_at__isnull=True,
            ).exists()
        ):
            raise CantEdit(
                f"the fixing agent is filming scene {scene.number} again. You'll be told when "
                "it's done; edit the scene then."
            )
        before = Before.of(scene.job)
        old_line = scene.line
        old_kind = scene.kind
        remember_line(scene)
        checked_words = words_to_keep(scene)
        new_line = " ".join(str(changes.get("line", old_line)).split())
        new_owner_script = "line" in changes and keeps_exact_line(scene, new_line)
        if (
            new_line != old_line
            and scene.owner_words
            and not new_owner_script
            and not line_change_reason.strip()
        ):
            raise CantEdit(
                "keep the owner's exact script, or give line_change_reason naming the "
                "unsupported fact or video-model maximum that requires changing it."
            )
        changed = _apply(scene, changes)
        if new_owner_script:
            remember_line(scene, replace=True)
        edited = Edited()
        if not changed:
            if "line" in changes:
                scene.save(update_fields=["owner_words"])
            return edited
        edited.changed[scene.number] = changed
        if scene.second_state and (scene.kind == Scene.Kind.TALKING or not scene.shows):
            scene.second_state = False
        _check(scene, owner_asked=owner_asked, previous_kind=old_kind)
        scene.save(update_fields=_SAVED)
        edited.also += keep_second_states(scene.job)
        _check_the_actions(scene.job)
        broken = _check_the_scene_change(scene.job, before, owner_asked=owner_asked)
        _told(edited, scene)
        let_through(scene.job, scene.number, "The producer's edit_scene", broken)
        if not new_owner_script and "line" in changed and line_change_reason.strip():
            tell_line_change(scene, old_line, scene.line, line_change_reason.strip(), checked_words)
    return edited


def add_scene(job: Job, at: int, fields: dict[str, Any], *, owner_asked: bool = False) -> Edited:
    """Add a scene to the plan as scene `at` (1 to one past the last), the scenes from there
    on moved one later. CantEdit, with nothing changed, when it breaks the plan."""
    with transaction.atomic():
        scenes = list(Job.objects.select_for_update().get(pk=job.pk).scenes.order_by("number"))
        before = Before.of(job)
        if not 1 <= at <= len(scenes) + 1:
            raise CantEdit(f"a new scene goes at 1 to {len(scenes) + 1}, so there is no {at}.")
        line = " ".join(str(fields.get("line") or "").split())
        scene = Scene(job=job, number=0, line=line)
        _apply(scene, {key: value for key, value in fields.items() if value is not None})
        if owner_steering(job, owner_asked):
            remember_line(scene, replace=True)
        _check(scene, owner_asked=owner_asked)
        # Its number is given once the others have moved, so two never share one.
        scene.number = len(scenes) + 1
        scene.save()
        renumber(job, [*scenes[: at - 1], scene, *scenes[at - 1 :]], owner_asked=owner_asked)
        scene.refresh_from_db()
        edited = Edited(changed={scene.number: ["added"]}, to_check=[scene.number])
        edited.also += keep_second_states(job)
        _check_the_actions(job)
        _check_the_plan(job, before, owner_asked=owner_asked)
    return edited


def remove_scene(scene: Scene, *, owner_asked: bool = False) -> Edited:
    """Take `scene` out of the plan, the scenes after it moved one earlier. What was made and
    paid for it is kept. CantEdit while one of its steps is being made, or when it is the
    plan's only scene."""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=scene.job_id)
        scene = Scene.objects.get(pk=scene.pk)
        if job.scenes.count() == 1:
            raise CantEdit("it is the ad's only scene, and an ad has at least one.")
        running = scene.steps.filter(status=SceneStep.Status.RUNNING).first()
        if running is not None:
            raise CantEdit(
                f"scene {scene.number}'s {stale.STEP_NAMES[SceneStep.Kind(running.kind)]} is "
                "still being made. You'll be told when it's ready; remove the scene then."
            )
        number = scene.number
        before = Before.of(job)
        Scene.objects.filter(pk=scene.pk).update(removed_at=timezone.now())
        # Its chat files are no longer a scene's: their card would take the number of the
        # scene that moves up into its place.
        _move_chat_scenes(job, {number: None})
        renumber(job, list(job.scenes.order_by("number")), owner_asked=owner_asked)
        edited = Edited(changed={number: ["removed"]})
        edited.also += keep_second_states(job)
        _check_the_plan(job, before, owner_asked=owner_asked)
    return edited


def reorder(job: Job, order: Sequence[int], *, owner_asked: bool = False) -> Edited:
    """Play the scenes in `order`, each scene by its number now: every scene once."""
    with transaction.atomic():
        scenes = {
            scene.number: scene
            for scene in Job.objects.select_for_update().get(pk=job.pk).scenes.all()
        }
        if sorted(order) != sorted(scenes):
            raise CantEdit(
                f"the new order names each scene once: the ad's scenes are 1 to {len(scenes)}, "
                f"and the order given was {', '.join(str(number) for number in order)}."
            )
        before = Before.of(job)
        moved = [number for position, number in enumerate(order, start=1) if position != number]
        renumber(job, [scenes[number] for number in order], owner_asked=owner_asked)
        edited = Edited(changed={number: ["moved"] for number in moved})
        edited.also += keep_second_states(job)
        _check_the_plan(job, before, owner_asked=owner_asked)
    return edited


def renumber(job: Job, in_order: Sequence[Scene], *, owner_asked: bool = False) -> None:
    """Number the plan's scenes 1, 2, 3... in the order `in_order` gives them, one scene per
    number, and move each scene's chat files to its new number."""
    if in_order and not owner_steering(job, owner_asked):
        try:
            check_opening_kind(1, in_order[0].kind)
        except ValueError as error:
            raise CantEdit(str(error)) from None
    old = {scene.pk: scene.number for scene in in_order}
    new = {scene.pk: position for position, scene in enumerate(in_order, start=1)}
    if old == new:
        return
    # Out of the way first, so no two scenes share a number on the way.
    offset = max([*old.values(), len(in_order)]) + 1
    for pk, number in new.items():
        Scene.objects.filter(pk=pk).update(number=offset + number)
    for pk, number in new.items():
        Scene.objects.filter(pk=pk).update(number=number)
    _move_chat_scenes(job, {old[pk]: new[pk] for pk in new if old[pk] != new[pk]})


def _apply(scene: Scene, changes: dict[str, Any]) -> list[str]:
    """Set `changes` on the unsaved scene; the names of the fields that changed."""
    before = {name: getattr(scene, name) for name in EDITABLE}
    line = " ".join(str(changes.get("line", scene.line)).split())
    shows = " ".join(str(changes["shows"]).split()) if "shows" in changes else None
    broll: dict[str, Any] = {
        name: str(changes[name]).strip()
        for name in ("broll_kind", "person_shown", "usage", "result")
        if name in changes
    }
    scene.change_line(line, shows, broll=broll)
    for name in ("kind", "kind_why"):
        if name in changes:
            setattr(scene, name, str(changes[name]).strip())
    if scene.kind == Scene.Kind.TALKING:
        scene.kind_why = ""
    if "kind" in changes:
        asked = str(changes.get("person_shown", before["person_shown"]))
        scene.person_shown = face_kept(before["kind"], before["person_shown"], scene.kind, asked)
    if "part" in changes:
        scene.part = str(changes["part"])
    if "overlay" in changes:
        scene.overlay = " ".join(str(changes["overlay"]).split())
    for name in ("talking_style", "product_state"):
        if name in changes:
            setattr(scene, name, " ".join(str(changes[name]).split()))
    if "shows_to_lens" in changes:
        scene.shows_to_lens = bool(changes["shows_to_lens"])
    if "packaging_photo" in changes:
        scene.packaging_photo = changes["packaging_photo"]
    if scene.kind == Scene.Kind.BROLL:
        # A B-roll scene has no talking style, product state or packaging photo, so a scene
        # turned into B-roll keeps none of its talking ones.
        scene.clear_talking_fields()
    changed = [name for name in EDITABLE if getattr(scene, name) != before[name]]
    if any(name in CHECKED for name in changed):
        scene.fact_checked = False
        scene.fact_problems = []
    return changed


def packaging_missing(scene: Scene) -> str | None:
    """Why the scene's packaging photo isn't a picture its ad's library holds, or None."""
    if scene.packaging_photo is None or scene.kind == Scene.Kind.BROLL:
        return None
    if scene.job.library.filter(number=scene.packaging_photo).exists():
        return None
    return (
        f"scene {scene.number or 'new'} names picture {scene.packaging_photo} as its packaging "
        "photo: the library has no such picture."
    )


def _check(scene: Scene, *, owner_asked: bool = False, previous_kind: str | None = None) -> None:
    """Validate the scene fields and a newly selected opening kind."""
    missing = packaging_missing(scene)
    if missing is not None:
        raise CantEdit(missing)
    try:
        if scene.kind != previous_kind and not owner_steering(scene.job, owner_asked):
            check_opening_kind(scene.number, scene.kind)
        H3PlannedScene.model_validate(
            {
                "kind": scene.kind,
                "kind_why": scene.kind_why,
                "line": scene.line,
                "shows": scene.shows or None,
                "broll_kind": scene.broll_kind or None,
                "person_shown": scene.person_shown or None,
                "usage": scene.usage or None,
                "result": scene.result or None,
                "needs": scene.needs,
                "part": scene.part or None,
                "second_state": scene.second_state,
                "overlay": scene.overlay or None,
                "owner_part": scene.owner_part,
                "scene_pictures": scene.scene_pictures,
                "piece": scene.piece,
                "talking_style": scene.talking_style or None,
                "product_state": scene.product_state or None,
                "shows_to_lens": scene.shows_to_lens,
                "packaging_photo": scene.packaging_photo,
            }
        )
    except ValidationError as error:
        raise CantEdit(
            f"scene {scene.number or 'new'} wouldn't be a scene the plan can hold: "
            + " ".join(_reason(each) for each in error.errors())
        ) from None

    except ValueError as error:
        raise CantEdit(str(error)) from None


def _reason(error: Any) -> str:
    """One of pydantic's errors as the producer is told it."""
    message = str(error["msg"]).removeprefix("Value error, ")
    where = ".".join(str(part) for part in error["loc"])
    message = message if message.endswith(".") else f"{message}."
    return f"{where}: {message}" if where else message


def plan_scenes(job: Job) -> list[PlanScene]:
    """The job's scenes as the whole-plan rules read them (jobs.plan_rules)."""
    return [scene.plan_scene() for scene in job.scenes.order_by("number")]


def last_line(job: Job) -> str:
    """The line of the job's last scene, or blank for a plan with no scenes."""
    last = job.scenes.order_by("number").last()
    return last.line if last is not None else ""


@dataclass(frozen=True)
class Before:
    """What an edit's checks compare the plan with: its scenes and its last line before it."""

    scenes: list[PlanScene]
    last_line: str

    @staticmethod
    def of(job: Job) -> Before:
        return Before(plan_scenes(job), last_line(job))


def _check_the_plan(job: Job, before: Before, *, owner_asked: bool = False) -> None:
    """CantEdit when the plan now breaks a whole-plan rule it kept before the edit, as the
    planner is refused, or its last line no longer says the brand it said."""
    if owner_steering(job, owner_asked):
        broken = _check_the_scene_change(job, before, owner_asked=True)
        let_through(job, None, "The producer’s plan edit", broken)
        return
    broken = newly_broken(before.scenes, plan_scenes(job))
    if broken is not None:
        raise CantEdit(f"{WOULD_BREAK} {broken}")
    _check_the_brand(job, before)


def _check_the_scene_change(job: Job, before: Before, *, owner_asked: bool = False) -> str | None:
    """CantEdit when an edit of one scene has the plan newly break a whole-plan rule such a
    change is still refused for, or its last line no longer says the brand it said. The other
    rules it newly breaks, said, for `let_through`, or None."""
    steering = owner_steering(job, owner_asked)
    breaks = a_scene_change_breaks(before.scenes, plan_scenes(job))
    if breaks.refused is not None:
        raise CantEdit(f"{WOULD_BREAK} {breaks.refused}")
    if not steering:
        _check_the_brand(job, before)
    return breaks.let_through


def _check_the_brand(job: Job, before: Before) -> None:
    dropped = brand_dropped(before.last_line, last_line(job), job.brand)
    if dropped is not None:
        raise CantEdit(dropped)


def let_through(job: Job, number: int | None, by: str, broken: str | None) -> None:
    """Record, as a developer notice, the whole-plan rules `broken` that a kept change to
    scene `number`, made `by` a step such as "The scene agent", newly breaks, if any."""
    if broken is None:
        return
    changed = f"scene {number}" if number is not None else "the plan"
    allowed = (
        "a change to one scene isn't refused for it"
        if number is not None
        else "the owner’s request overrides presentation defaults"
    )
    post_notice(
        job,
        notices.PLAN_RULE_LET_THROUGH,
        f"{by} changed {changed} and it went ahead though the plan now breaks a rule, as "
        f"{allowed}: {broken}",
        Message.Level.PROBLEM,
        scene=number,
    )


def _check_the_actions(job: Job) -> None:
    """CantEdit when a B-roll scene would have another scene's action, as the planner is
    refused."""
    repeated = repeated_action(
        (scene.number, scene.kind == Scene.Kind.BROLL, scene.shows)
        for scene in job.scenes.exclude(shows="").order_by("number")
    )
    if repeated is not None:
        first, again = repeated
        raise CantEdit(f"scenes {first} and {again} would {SAME_ACTION}")


def keep_second_states(job: Job) -> list[str]:
    """A scene showing a second state follows the B-roll scene showing the first: one that no
    longer does shows a state of its own from now on. What changed, said."""
    said: list[str] = []
    before: Scene | None = None
    for scene in job.scenes.order_by("number"):
        if scene.second_state and (
            scene.kind == Scene.Kind.TALKING
            or not scene.shows
            or before is None
            or before.kind == Scene.Kind.TALKING
            or not before.shows
            or before.second_state
        ):
            Scene.objects.filter(pk=scene.pk).update(second_state=False)
            scene.second_state = False
            said.append(
                f"Scene {scene.number} no longer follows the scene showing the first state of "
                "its claim, so it shows a state of its own."
            )
        before = scene
    return said


def _told(edited: Edited, scene: Scene) -> None:
    said = stale.after_edit(scene)
    if said:
        edited.stale.append(said)
    if not scene.fact_checked:
        edited.to_check.append(scene.number)


def _move_chat_scenes(job: Job, numbers: dict[int, int | None]) -> None:
    """Move the chat's messages and files in the job's session that name scene `old` by
    number to `numbers[old]`, all at once (None: no scene's)."""
    if not numbers:
        return

    def moved(field: str) -> Case:
        return Case(
            *[
                When(**{field: old}, then=Cast(Value(new), PositiveSmallIntegerField()))
                for old, new in numbers.items()
            ],
            output_field=PositiveSmallIntegerField(),
        )

    Attachment.objects.filter(message__session=job.session, scene__in=numbers).update(
        scene=moved("scene")
    )
    Message.objects.filter(session=job.session, step_scene__in=numbers).update(
        step_scene=moved("step_scene")
    )
    Message.objects.filter(session=job.session, suggested_note_scene__in=numbers).update(
        suggested_note_scene=moved("suggested_note_scene")
    )
