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
    for their answer. A plan still waiting in the chat is replaced by it."""
    assert job.session is not None, "a fix plan is shown in the job's chat"
    for cant in cants:
        if cant["kind"] not in FixPlan.Cant.values:
            raise ValueError(f"{cant['kind']!r} isn't a kind of can't a plan has")
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
