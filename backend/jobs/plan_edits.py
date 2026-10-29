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

from . import stale
from .models import BROLL_FIELDS, PLANNED_FOR_THE_ACTION, Job, Scene, SceneStep
from .planning import PlannedScene, same_action


class CantEdit(Exception):
    """The edit would break the plan, or what it edits is busy, and nothing was changed.
    Says why, for the producer."""


# What a scene's edit can change, each None to leave as it is. `shows` blank makes the
# person say the line to camera; `usage`, `result` and `overlay` blank are none.
EDITABLE = ("line", "shows", "broll_kind", "person_shown", "usage", "result", "part", "overlay")
# What the planning checks check: a change to any of them has the scene checked again.
CHECKED = ("line", "shows", "broll_kind", "person_shown", "usage", "result")
# The fields an edit saves.
_SAVED = [
    "line",
    "shortened_from",
    "shows",
    *BROLL_FIELDS,
    *PLANNED_FOR_THE_ACTION,
    "part",
    "overlay",
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


def edit_scene(scene: Scene, changes: dict[str, Any]) -> Edited:
    """Change the fields of `scene` named in `changes` (EDITABLE; None leaves a field as it
    is). CantEdit, with nothing changed, when the scene that results breaks the plan."""
    changes = {key: value for key, value in changes.items() if value is not None}
    with transaction.atomic():
        scene = Scene.objects.select_for_update().get(pk=scene.pk)
        changed = _apply(scene, changes)
        edited = Edited()
        if not changed:
            return edited
        edited.changed[scene.number] = changed
        if not scene.shows and scene.second_state:
            scene.second_state = False
        _check(scene)
        scene.save(update_fields=_SAVED)
        edited.also += _second_states_kept(scene.job)
        _check_the_actions(scene.job)
        _told(edited, scene)
    return edited


def add_scene(job: Job, at: int, fields: dict[str, Any]) -> Edited:
    """Add a scene to the plan as scene `at` (1 to one past the last), the scenes from there
    on moved one later. CantEdit, with nothing changed, when it breaks the plan."""
    with transaction.atomic():
        scenes = list(Job.objects.select_for_update().get(pk=job.pk).scenes.order_by("number"))
        if not 1 <= at <= len(scenes) + 1:
            raise CantEdit(f"a new scene goes at 1 to {len(scenes) + 1}, so there is no {at}.")
        line = " ".join(str(fields.get("line") or "").split())
        scene = Scene(job=job, number=0, line=line)
        _apply(scene, {key: value for key, value in fields.items() if value is not None})
        _check(scene)
        # Its number is given once the others have moved, so two never share one.
        scene.number = len(scenes) + 1
        scene.save()
        renumber(job, [*scenes[: at - 1], scene, *scenes[at - 1 :]])
        scene.refresh_from_db()
        edited = Edited(changed={scene.number: ["added"]}, to_check=[scene.number])
        edited.also += _second_states_kept(job)
        _check_the_actions(job)
    return edited


def remove_scene(scene: Scene) -> Edited:
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
        Scene.objects.filter(pk=scene.pk).update(removed_at=timezone.now())
        # Its chat files are no longer a scene's: their card would take the number of the
        # scene that moves up into its place.
        _move_chat_scenes(job, {number: None})
        renumber(job, list(job.scenes.order_by("number")))
        edited = Edited(changed={number: ["removed"]})
        edited.also += _second_states_kept(job)
    return edited


def reorder(job: Job, order: Sequence[int]) -> Edited:
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
        moved = [number for position, number in enumerate(order, start=1) if position != number]
        renumber(job, [scenes[number] for number in order])
        edited = Edited(changed={number: ["moved"] for number in moved})
        edited.also += _second_states_kept(job)
    return edited


def renumber(job: Job, in_order: Sequence[Scene]) -> None:
    """Number the plan's scenes 1, 2, 3... in the order `in_order` gives them, one scene per
    number, and move each scene's chat files to its new number."""
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


def change_line(scene: Scene, line: str, overlay: str = "") -> None:
    """Give a scene the line the shop owner said yes to, and the overlay the plan showed if
    any, saved. They know their product, so the line isn't fact checked (Julian, 2026-10-11
    22:13, #138)."""
    if scene.line != line:
        scene.change_line(line)
        scene.save(update_fields=["line", "shortened_from", "status"])
    if overlay and scene.overlay != overlay:
        scene.overlay = overlay
        scene.save(update_fields=["overlay"])


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
    if "part" in changes:
        scene.part = str(changes["part"])
    if "overlay" in changes:
        scene.overlay = " ".join(str(changes["overlay"]).split())
    changed = [name for name in EDITABLE if getattr(scene, name) != before[name]]
    if any(name in CHECKED for name in changed):
        scene.fact_checked = False
        scene.fact_problems = []
    return changed


def _check(scene: Scene) -> None:
    """CantEdit when the scene breaks the planner's rules for a scene."""
    try:
        PlannedScene.model_validate(
            {
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
            }
        )
    except ValidationError as error:
        raise CantEdit(
            f"scene {scene.number or 'new'} wouldn't be a scene the plan can hold: "
            + " ".join(_reason(each) for each in error.errors())
        ) from None


def _reason(error: Any) -> str:
    """One of pydantic's errors as the producer is told it."""
    message = str(error["msg"]).removeprefix("Value error, ")
    where = ".".join(str(part) for part in error["loc"])
    message = message if message.endswith(".") else f"{message}."
    return f"{where}: {message}" if where else message


def _check_the_actions(job: Job) -> None:
    """CantEdit when two B-roll scenes film the same action, as the planner is refused."""
    filmed: dict[str, int] = {}
    for scene in job.scenes.exclude(shows="").order_by("number"):
        first = filmed.setdefault(same_action(scene.shows), scene.number)
        if first != scene.number:
            raise CantEdit(
                f"scenes {first} and {scene.number} would film the same action: give each "
                "B-roll scene a different one."
            )


def _second_states_kept(job: Job) -> list[str]:
    """A scene showing a second state follows the B-roll scene showing the first: one that no
    longer does shows a state of its own from now on. What changed, said."""
    said: list[str] = []
    before: Scene | None = None
    for scene in job.scenes.order_by("number"):
        if scene.second_state and (
            not scene.shows or before is None or not before.shows or before.second_state
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
