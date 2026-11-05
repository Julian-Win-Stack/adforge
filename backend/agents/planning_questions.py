"""The planner's one question round: durable waiting, answers, and approved defaults."""

import re
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message, Session
from jobs import notices
from jobs.models import Job
from jobs.planning_questions import QuestionKind, default_for

from .models import PlannerQuestion, SystemNote, ToolCall

if TYPE_CHECKING:
    from jobs.planning import ProducerDecision


def record_question(call: ToolCall, decision: ProducerDecision) -> None:
    """Keep the round before its result is handed to the producer; replay keeps its clock."""
    PlannerQuestion.objects.get_or_create(
        plan_call=call,
        defaults={
            "question": decision.question or "",
            "items": [item.model_dump() for item in decision.question_items],
        },
    )


def _open_for_session(call: ToolCall) -> PlannerQuestion | None:
    question = (
        PlannerQuestion.objects.filter(
            plan_call__session_id=call.session_id,
            answered_at__isnull=True,
            timed_out_at__isnull=True,
            plan_call__created_at__lte=call.created_at,
        )
        .select_related("shown_message", "plan_call__session")
        .order_by("plan_call__created_at")
        .last()
    )
    if question is not None and _settle_answer(question):
        return None
    return question


def question_to_show(call: ToolCall, proposed: str) -> str:
    """Show the planner's numbered questions with every default, even if paraphrased."""
    question = _open_for_session(call)
    return question.question if question is not None else proposed


def already_shown(call: ToolCall) -> Message | None:
    """A duplicate ask_owner call must not show the same round a second time."""
    question = _open_for_session(call)
    return question.shown_message if question is not None else None


def question_shown(call: ToolCall, message: Message) -> None:
    """Start the clock only for the unanswered planning round this ask_owner shows."""
    with transaction.atomic():
        Session.objects.select_for_update().get(pk=call.session_id)
        round = (
            PlannerQuestion.objects.filter(
                plan_call__session_id=call.session_id,
                shown_message__isnull=True,
                answered_at__isnull=True,
                timed_out_at__isnull=True,
                plan_call__created_at__lte=call.created_at,
            )
            .order_by("plan_call__created_at")
            .last()
        )
        if round is None:
            return
        round.shown_message = message
        round.deadline_at = message.created_at + timedelta(
            seconds=settings.PLANNER_QUESTION_TIMEOUT_SECONDS
        )
        round.save(update_fields=["shown_message", "deadline_at"])


def _latest(job: Job) -> PlannerQuestion | None:
    return (
        PlannerQuestion.objects.filter(plan_call__job=job)
        .select_related("shown_message", "plan_call__session")
        .order_by("plan_call__created_at")
        .last()
    )


def _answer(round: PlannerQuestion) -> Message | None:
    if round.shown_message is None:
        return None
    return (
        round.plan_call.session.messages.filter(
            role=Message.Role.USER, seq__gt=round.shown_message.seq
        )
        .order_by("seq")
        .first()
    )


def _settle_answer(round: PlannerQuestion) -> bool:
    if round.answered_at is not None:
        return True
    answer = _answer(round)
    if answer is None:
        return False
    round.answered_at = answer.created_at
    round.save(update_fields=["answered_at"])
    return True


def may_plan(job: Job, session: Session) -> None:
    """Refuse a second planning call until this round has an answer or has expired."""
    from .loop import Refused

    round = _latest(job)
    if round is None or round.timed_out_at is not None or _settle_answer(round):
        return
    raise Refused(
        "the planner's questions are still waiting for the shop owner. Show them once with "
        "ask_owner and wait for their answer or the approved timeout defaults."
    )


def question_resolved(job: Job) -> bool:
    """Whether the one round was answered or timed out, including after a worker restart."""
    round = _latest(job)
    return round is not None and (round.timed_out_at is not None or _settle_answer(round))


def planning_defaults(job: Job) -> str:
    """Tell the planner how to handle the items its single question left unanswered."""
    round = _latest(job)
    if round is None or not question_resolved(job):
        return ""
    defaults = _defaults(round)
    if round.timed_out_at is not None:
        return (
            "No owner answer in 10 minutes. Apply every stated default; never ask again. "
            + " ".join(defaults)
        )
    return (
        "The owner answered the single question round. Apply their answers; any items they "
        "didn't answer take these stated defaults. Never ask a second round. " + " ".join(defaults)
    )


def _defaults(round: PlannerQuestion) -> list[str]:
    return [
        fallback
        for item in round.items
        if (fallback := default_for(cast(QuestionKind, item["kind"]))) is not None
    ]


# A currency amount in either notation, including prices spoken in words.
_PRICE = re.compile(
    r"[$€£¥₹₩₽]\s*\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP|CAD|AUD|NZD|JPY|INR|CHF)\b"
    r"|\b(?:dollars?|euros?|pounds?|yen|rupees?|cents?)\b",
    re.IGNORECASE,
)


def check_plan_defaults(job: Job, decision: ProducerDecision) -> None:
    """Keep a timed-out size/price default out of both spoken lines and overlays."""
    round = _latest(job)
    if round is None or round.timed_out_at is None or decision.plan is None:
        return
    kinds = {item["kind"] for item in round.items}
    if kinds & {"size", "price"}:
        if any(
            _PRICE.search(scene.line + " " + (scene.overlay or ""))
            for scene in decision.plan.scenes
        ):
            raise ValueError(
                "The size or price question timed out: no line or overlay may state a price. "
                "Pick the main size or pack on the page."
            )


def expire_waiting_questions() -> None:
    """Claim each overdue round under the chat lock, then wake its producer exactly once."""
    from .tasks import wake_producer

    due = PlannerQuestion.objects.filter(
        deadline_at__lte=timezone.now(), answered_at__isnull=True, timed_out_at__isnull=True
    )
    for round_id, session_id in due.values_list("pk", "plan_call__session_id"):
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=session_id)
            round = PlannerQuestion.objects.select_related("plan_call", "shown_message").get(
                pk=round_id
            )
            if round.timed_out_at is not None or _settle_answer(round):
                continue
            # Legacy unstructured questions and stock have no approved automatic outcome.
            if not round.items or any(default_for(item["kind"]) is None for item in round.items):
                continue
            if session.paused_at is not None:
                continue
            round.timed_out_at = timezone.now()
            round.save(update_fields=["timed_out_at"])
            defaults = " ".join(_defaults(round))
            messages.add(
                session,
                role=Message.Role.NOTICE,
                level=Message.Level.INFO,
                text=(
                    "I went ahead without your answer: "
                    + defaults
                    + " You can change any of it with a note."
                ),
            )
            SystemNote.objects.create(
                session=session,
                kind=SystemNote.Kind.PLANNER_TIMEOUT,
                text=(
                    "No owner answer in 10 minutes. Plan with the defaults stated in the "
                    "question: " + defaults + " The owner was told; don't repeat the notice "
                    "or ask again. Call plan_ad now."
                ),
            )
            notices.record(
                session,
                notices.PLANNER_QUESTION_TIMEOUT,
                "No owner answer in 10 minutes; the planner's stated defaults were taken: "
                + defaults,
                Message.Level.INFO,
            )
            wake_producer(str(session_id))
