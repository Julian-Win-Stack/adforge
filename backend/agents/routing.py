"""Code routes the shop owner's messages once the ad has a clip (fix redesign, decisions 4, 15
and 20; flow steps 2, 7 and 9). Before the first clip every message is the producer's, about
making the ad. After it, each message goes first to the decider, which sorts it: a note is
planned and code shows the plan, but a note on a scene with no clip yet is the producer's,
which still makes that scene; yes or no to the plan waiting is settled by code, and a yes
runs it; go ahead to a failed fix check carries its scenes on; anything else is the
producer's to answer. A tap of the plan's yes button needs no decider: it names its plan.

The producer decides and starts nothing here. It answers what is passed to it, and reads
what happened in the system notes code writes."""

import logging

from django.db import transaction
from django.db.models import Max, QuerySet
from django.utils import timezone

from chat.models import Message, Session
from jobs import fix_plans
from jobs.models import FixPlan, Job, ProducedItem

from . import decider, run_plan
from .models import SystemNote

logger = logging.getLogger(__name__)

# What the producer is told when the shop owner says no to a plan, or taps yes on a plan a
# newer one replaced.
DECLINED = "The shop owner said no to the plan, so nothing in it was changed."
TOO_LATE = (
    "The shop owner tapped yes on a plan a newer one had replaced, so nothing was run. The "
    "newer plan is waiting for their answer."
)
# What it is told when they tap yes on a plan they had already said no to in words.
TAPPED_AFTER_NO = (
    "The shop owner tapped yes on a plan they had already said no to, so nothing was run."
)


def route(session: Session) -> bool:
    """Send each message the shop owner sent since the ad's first clip, and not yet routed,
    to whoever answers it, oldest first. Whether the producer has something to answer: one of
    them was passed to it, or none needed routing."""
    unrouted = list(unrouted_messages(session))
    if not unrouted:
        return True
    job = Job.objects.get(session=session)
    producer = False
    for message in unrouted:
        producer |= _route(job, message)
    return producer


def unrouted_messages(session: Session) -> QuerySet[Message]:
    """The shop owner's messages since the ad's first clip that code hasn't routed yet,
    oldest first."""
    job = Job.objects.filter(session=session).first()
    first_clip = (
        None
        if job is None
        else job.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("created_at").first()
    )
    if first_clip is None:
        return Message.objects.none()
    return session.messages.filter(
        role=Message.Role.USER, created_at__gt=first_clip.created_at, routed_at__isnull=True
    ).order_by("seq")


def last_seq(session: Session) -> int:
    """The number of the session's newest message."""
    newest: int | None = session.messages.aggregate(newest=Max("seq"))["newest"]
    return newest or 0


def _route(job: Job, message: Message) -> bool:
    """Route one message. Whether the producer is to answer it."""
    if message.plan_event == Message.PlanEvent.YES:
        return _tapped(message)
    said = [each for each in decider.this_round(job) if each.seq <= message.seq]
    if not said or said[-1].pk != message.pk:
        # Not in a round the decider reads, as a message from before the last yes.
        return _routed(message, producer=True)
    try:
        outcome = decider.read(job, said)
    except decider.NoDecision as stopped:
        # The decider never talks in the chat, so the producer answers instead.
        logger.warning("The decider gave no answer to message %s: %s", message.pk, stopped)
        return _routed(message, producer=True)
    with transaction.atomic():
        match outcome:
            case decider.NotAFix():
                return _routed(message, producer=True)
            case decider.Planned(plan=plan):
                # A note on a scene with no clip yet is the producer's: it still makes the
                # scene for the first time. A plan of only such scenes isn't shown.
                making = run_plan.first_making(plan)
                if (
                    making
                    and len(making) == plan.scenes.count()
                    and not (plan.question or plan.cants)
                ):
                    FixPlan.objects.filter(pk=plan.pk, state=FixPlan.State.PROPOSED).update(
                        state=FixPlan.State.REPLACED
                    )
                    plan.system_notes.filter(kind=SystemNote.Kind.PLAN_SHOWN).delete()
                    return _routed(message, producer=True)
                # The plan replaces any plan waiting, whose yes button the notice takes away.
                if not plan.messages.filter(plan_event=Message.PlanEvent.SHOWN).exists():
                    fix_plans.show(plan)
                return _routed(message, producer=bool(making))
            case decider.Yes(plan=plan):
                shown = plan.messages.filter(
                    plan_event=Message.PlanEvent.SHOWN, seq__lt=message.seq
                ).exists()
                if not shown or not run_plan.runs_something(plan) or not _still_waiting(plan):
                    # A typed yes can't approve a plan the shop owner hadn't been shown, or
                    # one with nothing to run (sort_message refuses that yes too).
                    return _routed(message, producer=True)
                _run(plan, message)
                return _routed(message, producer=False)
            case decider.No(plan=plan):
                if _still_waiting(plan):
                    fix_plans.settle(plan, FixPlan.State.DECLINED)
                    _note(plan, SystemNote.Kind.PLAN_DECLINED, DECLINED)
                return _routed(message, producer=True)
            case decider.GoAhead(rows=rows):
                for row in rows:
                    run_plan.go_ahead(row)
                return _routed(message, producer=False)


def _tapped(message: Message) -> bool:
    """A tap of a plan's yes button: the plan runs, unless it was answered since the tap was
    kept. A newer plan replaced it: the producer tells the shop owner to look at the newer
    one. A yes typed before it already runs it: the tap adds nothing. A no typed before it
    dropped it: the producer asks which they meant."""
    plan = message.fix_plan
    with transaction.atomic():
        if plan is None:
            return _routed(message, producer=True)
        if _still_waiting(plan):
            _run(plan, message)
            return _routed(message, producer=False)
        if plan.state == FixPlan.State.CONFIRMED:
            return _routed(message, producer=False)
        if plan.state == FixPlan.State.DECLINED:
            _note(plan, SystemNote.Kind.YES_TOO_LATE, TAPPED_AFTER_NO)
        else:
            _note(plan, SystemNote.Kind.YES_TOO_LATE, TOO_LATE)
        return _routed(message, producer=True)


def _still_waiting(plan: FixPlan) -> bool:
    """Whether `plan` still waits for the shop owner's answer, read under the locks every
    writer of a plan takes, in their order (the job, the chat, then the plan), so it can't be
    replaced or settled before this transaction acts on the answer."""
    Job.objects.select_for_update().get(pk=plan.job_id)
    Session.objects.select_for_update().get(pk=plan.session_id)
    plan.state = FixPlan.objects.select_for_update().values_list("state", flat=True).get(pk=plan.pk)
    return plan.state == FixPlan.State.PROPOSED


def _run(plan: FixPlan, said_yes: Message) -> None:
    """Confirm `plan` with the shop owner's yes, and run it, in the transaction that routes
    the yes: a worker that stops in between leaves both undone, for the next to do."""
    fix_plans.settle(plan, FixPlan.State.CONFIRMED, by=said_yes)
    run_plan.run_after_yes(plan)


def _routed(message: Message, *, producer: bool) -> bool:
    Message.objects.filter(pk=message.pk).update(routed_at=timezone.now())
    return producer


def _note(plan: FixPlan, kind: SystemNote.Kind, text: str) -> None:
    assert plan.session_id is not None
    SystemNote.objects.create(session_id=plan.session_id, plan=plan, kind=kind, text=text)
