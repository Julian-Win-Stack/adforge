"""Fix plans (fix redesign, decision 19): recording what the decider plans to change in each
scene from the shop owner's notes of one round, what the chat's plan notice shows of it
(flow step 6), and telling the chat once it is settled (decision 4). Nothing here checks a
plan's rules, routes the shop owner's answer or runs it."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, TypedDict

from django.db import transaction

from chat import messages
from chat.models import Message, Session

from .models import FixPlan, FixPlanScene, Job
from .notices import post_notice

# What the chat says once a plan is settled, so its yes button goes away, and what the
# notice says of the plan.
SETTLED = {
    FixPlan.State.CONFIRMED: ("Going ahead with the plan.", Message.PlanEvent.CONFIRMED),
    FixPlan.State.DECLINED: (
        "Plan dropped: nothing in it will be changed.",
        Message.PlanEvent.DECLINED,
    ),
}


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
    overlay: str
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
    """A row is one of this ad's scenes, carries a new line for a new line, or for a new
    picture asked for with it, a new overlay only with a new line, the version only for a go
    back, and each photo in the shape the notice and the steps read."""
    number = row.scene.number
    if row.scene.job_id != job.pk:
        raise ValueError(f"Scene {number} is another ad's.")
    if row.action not in FixPlanScene.Action.values:
        raise ValueError(f"Scene {number}: no such action {row.action!r}.")
    takes_a_line = (FixPlanScene.Action.NEW_LINE, FixPlanScene.Action.NEW_PICTURE)
    if (row.action == FixPlanScene.Action.NEW_LINE and not row.line) or (
        row.line and row.action not in takes_a_line
    ):
        raise ValueError(
            f"Scene {number}: a new line carries the line, and only a new line or a new "
            "picture carries one."
        )
    if row.overlay and not row.line:
        raise ValueError(f"Scene {number}: a new overlay comes only with a new line.")
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


def show(plan: FixPlan) -> Message | None:
    """Post `plan` in the job's chat as a notice the shop owner can say yes to with one tap.
    The chat reads the plan's fields from the notice; its text says the same in words."""
    return post_notice(
        plan.job,
        _in_words(notice(plan)),
        Message.Level.INFO,
        plan=messages.PlanSignal(plan.pk, Message.PlanEvent.SHOWN),
    )


def settle(plan: FixPlan, state: FixPlan.State, by: Message | None = None) -> Message | None:
    """Confirm the waiting `plan`, with the message that said yes (`by`), or decline it, and
    tell the chat, so its yes button goes away whether the answer was tapped or typed."""
    assert state in SETTLED, "a plan is settled as confirmed or declined"
    text, event = SETTLED[state]
    with transaction.atomic():
        # Locked in the order every writer of a plan takes: the job (as the notice locks it),
        # the chat (as `propose` and the yes button lock it), then the plan. Taken the other
        # way round, two of them could each wait on the other.
        Job.objects.select_for_update().get(pk=plan.job_id)
        Session.objects.select_for_update().get(pk=plan.session_id)
        locked = FixPlan.objects.select_for_update().get(pk=plan.pk)
        assert locked.state == FixPlan.State.PROPOSED, "only a waiting plan is settled"
        locked.state = state
        locked.confirmed_by = by if state == FixPlan.State.CONFIRMED else None
        locked.save(update_fields=["state", "confirmed_by"])
        posted = post_notice(
            plan.job, text, Message.Level.INFO, plan=messages.PlanSignal(plan.pk, event)
        )
    plan.state, plan.confirmed_by = locked.state, locked.confirmed_by
    return posted


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
        "overlay": row.overlay,
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


ACTIONS_IN_WORDS = {
    FixPlanScene.Action.NEW_PICTURE: "new starting picture",
    FixPlanScene.Action.NEW_CLIP: "new clip",
    FixPlanScene.Action.NEW_LINE: "new line",
    FixPlanScene.Action.GO_BACK: "go back",
    FixPlanScene.Action.NO_CHANGE: "no change",
}


def _in_words(shown: PlanNotice) -> str:
    """The plan notice as text: what the chat shows where it can't draw the plan, and what
    the job's warnings keep. A plan a yes would run nothing of (only can'ts, scenes left as
    they are, or a question; as `run_plan.runs_something` says) asks for no yes and shows
    no costs: it says it can't be done yet, if anything was asked that can't."""
    runs = any(_doable(scene) for scene in shown["scenes"])
    asked_cant = any(scene["cant"] for scene in shown["scenes"]) or bool(shown["cants"])
    lines: list[str] = []
    if runs:
        lines.append("Here's the plan for your notes.")
    elif asked_cant:
        lines.append("We can't do that yet.")
    for scene in shown["scenes"]:
        what = ACTIONS_IN_WORDS[FixPlanScene.Action(scene["action"])]
        if scene["action"] == FixPlanScene.Action.GO_BACK:
            what = f"go back to version {scene['go_back_to']}"
        said = f"Scene {scene['scene']}, {what}"
        if scene["change"]:
            said += f": {scene['change']}"
        if scene["line"]:
            said += f' New line: "{scene["line"]}"'
        if scene["overlay"]:
            said += f' On screen: "{scene["overlay"]}"'
        # As the chat shows it: a can't, free, or what it costs.
        if scene["cant"]:
            said += f" Can't: {scene['cant']}"
        elif runs:
            said += _cost(scene)
        lines.append(said)
    lines.extend(f"Can't: {cant['text']}" for cant in shown["cants"])
    if shown["question"]:
        lines.append(shown["question"])
    if runs:
        lines.append("Tap Yes to go ahead, or type what to change.")
    return "\n".join(lines)


def _cost(scene: PlanNoticeScene) -> str:
    if Decimal(scene["estimate_usd"]) or Decimal(scene["estimate_up_to_usd"]):
        return (
            f" (about {_dollars(scene['estimate_usd'])}, "
            f"up to {_dollars(scene['estimate_up_to_usd'])})"
        )
    return " (free)"


def _doable(scene: PlanNoticeScene) -> bool:
    return scene["action"] != FixPlanScene.Action.NO_CHANGE and not scene["cant"]


def _dollars(amount: str) -> str:
    return f"${Decimal(amount):.2f}"
