"""Fix plans (fix redesign, decision 19): recording what the decider plans to change in each
scene from the shop owner's notes of one round, and what the chat's plan notice shows of it
(flow step 6). Nothing here checks a plan's rules or runs it."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, TypedDict

from django.db import transaction

from chat.models import Session

from .models import FixPlan, FixPlanScene, Job


class PlanPhoto(TypedDict):
    """An owner photo a plan's scene uses, as the plan notice shows it."""

    photo: int
    thing: str
    in_picture: bool
    during_action: bool


class PlanNoticeScene(TypedDict):
    scene: int
    action: str
    change: str
    line: str
    go_back_to: int | None
    remakes_clip: bool
    cant: str
    estimate_usd: str
    estimate_up_to_usd: str
    photos: list[PlanPhoto]


class PlanCant(TypedDict):
    """Something the shop owner asked that can't be done and isn't about one scene."""

    kind: str
    text: str


class PlanNotice(TypedDict):
    """What the chat's plan notice shows: per scene what changes, what it costs and any
    can't, the one question, and the plan's total. Amounts are strings, as the chat API
    gives them, so nothing is rounded on the way."""

    plan: int
    state: str
    question: str
    estimate_usd: str
    estimate_up_to_usd: str
    cants: list[PlanCant]
    scenes: list[PlanNoticeScene]


def propose(
    job: Job,
    notes: Sequence[dict[str, Any]],
    scenes: Sequence[FixPlanScene],
    question: str = "",
    cants: Sequence[PlanCant] = (),
) -> FixPlan:
    """Record a plan from the shop owner's notes of this round, with `scenes` (unsaved rows,
    one per scene that changes or can't) and the `cants` that aren't about one scene, waiting
    for their answer. A plan still waiting in the chat is replaced by it. Refuses a row that
    doesn't fit its action or isn't this ad's, and a can't of no known kind, before anything is
    written."""
    assert job.session is not None, "a fix plan is shown in the job's chat"
    for cant in cants:
        if set(cant) != {"kind", "text"} or cant["kind"] not in FixPlan.Cant.values:
            raise ValueError(f"{cant['kind']!r} isn't a kind of can't a plan has")
    for scene in scenes:
        _check(job, scene)
    with transaction.atomic():
        # The chat is locked, so two plans proposed at once can't both stay open.
        session = Session.objects.select_for_update().get(pk=job.session.pk)
        FixPlan.objects.filter(session=session, state=FixPlan.State.PROPOSED).update(
            state=FixPlan.State.REPLACED
        )
        plan = FixPlan.objects.create(
            job=job, session=session, notes=list(notes), question=question, cants=list(cants)
        )
        for scene in scenes:
            scene.plan = plan
        FixPlanScene.objects.bulk_create(scenes)
    return plan


PHOTO_KEYS = frozenset({"photo", "file", "thing", "in_picture", "during_action"})


def _check(job: Job, row: FixPlanScene) -> None:
    """A row is one of this ad's scenes, carries the new line only for a new line and the
    version only for a go back, and each photo in the shape the notice and the steps read."""
    number = row.scene.number
    if row.scene.job_id != job.pk:
        raise ValueError(f"Scene {number} is another ad's.")
    if row.action not in FixPlanScene.Action.values:
        raise ValueError(f"Scene {number}: no such action {row.action!r}.")
    if (row.action == FixPlanScene.Action.NEW_LINE) != bool(row.line):
        raise ValueError(f"Scene {number}: a new line, and only a new line, carries the line.")
    version = row.go_back_to
    if (row.action == FixPlanScene.Action.GO_BACK) != (version is not None) or (
        version is not None and version < 1
    ):
        raise ValueError(f"Scene {number}: a go back, and only a go back, names a version from 1.")
    for photo in row.photos:
        # The decider also says whether the photo is clear enough to copy its thing's look.
        if set(photo) - {"clear"} != PHOTO_KEYS:
            raise ValueError(
                f"Scene {number}: a photo needs exactly {sorted(PHOTO_KEYS)}, and may say clear."
            )


def open_plan(session: Session) -> FixPlan | None:
    """The plan waiting for the shop owner's answer in the chat, if any."""
    return FixPlan.objects.filter(session=session, state=FixPlan.State.PROPOSED).first()


def notice(plan: FixPlan) -> PlanNotice:
    """What the chat shows of `plan`."""
    scenes = list(plan.scenes.select_related("scene"))
    return {
        "plan": plan.pk,
        "state": plan.state,
        "question": plan.question,
        "estimate_usd": _amount(plan.estimate_usd()),
        "estimate_up_to_usd": _amount(plan.estimate_up_to_usd()),
        "cants": [{"kind": cant["kind"], "text": cant["text"]} for cant in plan.cants],
        "scenes": [_scene(row) for row in scenes],
    }


def _scene(row: FixPlanScene) -> PlanNoticeScene:
    return {
        "scene": row.scene.number,
        "action": row.action,
        "change": row.change,
        "line": row.line,
        "go_back_to": row.go_back_to,
        "remakes_clip": row.remakes_clip,
        "cant": row.cant,
        "estimate_usd": _amount(row.estimate_usd),
        "estimate_up_to_usd": _amount(row.estimate_up_to_usd),
        "photos": [
            {
                "photo": photo["photo"],
                "thing": photo["thing"],
                "in_picture": photo["in_picture"],
                "during_action": photo["during_action"],
            }
            for photo in row.photos
        ],
    }


def _amount(usd: Decimal) -> str:
    """An amount to the millionth of a dollar, as the database keeps it."""
    return str(Decimal(usd).quantize(Decimal("0.000001")))
