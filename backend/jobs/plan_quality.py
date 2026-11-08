"""The plan quality check (spec #401, #404, #437): after a plan passes the code rules, one text
model reads it and answers whether any claim is said twice. A "no" sends the plan back to the
planner with the check's reason, at most twice; after a third "no", planning goes on with the
plan that got the fewest "no" answers. No answer never blocks. Every verdict is recorded for
the developer, so Julian can grade the checker."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import call_model
from gateway.models import ModelCall
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    Judgement,
    UnusableReply,
)

from . import notices, page
from .models import Job
from .planning import ChatMessage, Plan, PlannedScene, ProducerDecision

logger = logging.getLogger(__name__)

# How many times a plan the check says "no" to is sent back to the planner: the check sees at
# most this many plans and one more (Julian, Nov 6 06:45).
MOST_SENT_BACK = 2

PLAN_QUALITY_INSTRUCTIONS = (
    "You check the plan of a short vertical video ad for one product before anything is "
    "made. You get the product page's text, what the product's videos say, the conversation "
    "with the shop owner, and the plan's scenes in the order they play, each with its part, "
    "its kind (talking: she says the line on camera; B-roll: any part of her body with the "
    "product but never her face, her voice off camera), its line and its action. Answer "
    "one question: is every claim said only once in the ad? A claim is something the lines "
    "say about the product, such as a benefit, a feature or a price. Saying the product's "
    "name again, or the call to action, "
    'is not a claim said twice. Answer "yes" or "no"; for "no", name the claim and the '
    "scenes that say it.\n"
    'Decide "pass" when the answer is "yes", and "fail" when it is "no". The reason says '
    "what to fix, in one sentence."
)


YesNo = Literal["yes", "no"]


class SceneToCheck(BaseModel):
    number: int
    part: str
    kind: str
    line: str
    shows: str


class PlanQualityHandoff(Handoff):
    page_text: str
    video_transcripts: list[str]
    conversation: list[ChatMessage]
    scenes: list[SceneToCheck]


class PlanQuality(Judgement):
    decision: Literal["pass", "fail"]
    claims_said_once: YesNo = Field(description='"yes" when every claim is said only once.')
    claims_why: str = Field(description='For "no", the claim said twice and its scenes.')

    @model_validator(mode="after")
    def _decision_matches_the_answer(self) -> Self:
        if (self.decision == "pass") != (self.noes() == 0):
            raise ValueError('Decide "pass" only when the answer is "yes".')
        return self

    def noes(self) -> int:
        """How many of its answers are "no"."""
        return int(self.claims_said_once == "no")

    def reasons(self) -> str:
        """What its "no" answers say, for the planner and the developer."""
        if self.claims_said_once == "no":
            return f"A claim is said twice: {self.claims_why}"
        return ""


def _to_check(number: int, scene: PlannedScene) -> SceneToCheck:
    """`scene` as the check is handed it."""
    return SceneToCheck(
        number=number,
        part=scene.part or "",
        kind=scene.kind or "talking",
        line=scene.line,
        shows=scene.shows or "",
    )


# What may stop the check from answering: none of it blocks the plan.
NO_ANSWER = (
    UnusableReply,
    OutsideServiceDown,
    AnswerTookTooLong,
    BlockedBySafetyFilter,
)


def check_plan(
    job: Job,
    plan: Plan,
    *,
    video_transcripts: list[str],
    conversation: list[ChatMessage],
) -> PlanQuality | None:
    """The plan check's answer for `plan`, or None when it gave none, recorded for the
    developer."""
    try:
        return call_model(
            job=job,
            purpose="check_plan_quality",
            instructions=PLAN_QUALITY_INSTRUCTIONS,
            handoff=PlanQualityHandoff(
                page_text=page.for_model(job.page_text),
                video_transcripts=video_transcripts,
                conversation=conversation,
                scenes=[
                    _to_check(number, scene) for number, scene in enumerate(plan.scenes, start=1)
                ],
            ),
            output=PlanQuality,
        )
    except NO_ANSWER as error:
        logger.warning("Job %s: the plan quality check didn't answer (%s)", job.pk, error)
        notices.post_notice(
            job,
            notices.PLAN_QUALITY_UNANSWERED,
            "The plan quality check didn't answer this time "
            f"({error.__class__.__name__}), so the plan goes on unchecked.",
            Message.Level.PROBLEM,
        )
        return None


@dataclass(frozen=True)
class _Checked:
    decision: ProducerDecision
    left_out: list[Image]
    verdict: PlanQuality


# What the planner is told, after its instructions, when its plan is sent back.
SENT_BACK = (
    "The plan quality check sent your last plan back: {reasons} Plan again, fixing what it "
    'says and keeping to every rule, and decide "plan". Your last plan was:\n{plan}'
)


def quality_checked(
    job: Job,
    decision: ProducerDecision,
    left_out: list[Image],
    *,
    plan_again: Callable[[str], tuple[ProducerDecision, list[Image]]],
    video_transcripts: list[str],
    conversation: list[ChatMessage],
) -> tuple[ProducerDecision, list[Image]]:
    """The plan `decision` to keep, with the pictures left out to get it, once the plan check
    has read it: a plan it passes or gives no answer for is kept; one it fails is sent back,
    with its reasons, through `plan_again` (handed what the planner is told after its
    instructions), at most MOST_SENT_BACK times. When no plan passes, the one with the fewest
    "no" answers is kept, the latest on a tie. A re-plan breaks the code rules as any plan
    does: told once, then the plan tool fails."""
    checked: list[_Checked] = []
    stopped = f"after {MOST_SENT_BACK} sends back"
    while True:
        assert decision.plan is not None
        verdict = check_plan(
            job, decision.plan, video_transcripts=video_transcripts, conversation=conversation
        )
        if verdict is None:
            return decision, left_out
        checked.append(_Checked(decision, left_out, verdict))
        last = verdict.decision == "pass" or len(checked) > MOST_SENT_BACK
        notices.post_notice(
            job,
            notices.PLAN_QUALITY_VERDICT,
            _verdict_words(len(checked), verdict)
            + (
                ""
                if verdict.decision == "pass"
                else " The plan goes on."
                if last
                else " Sent back to the planner."
            ),
            Message.Level.INFO,
        )
        if verdict.decision == "pass":
            return decision, left_out
        if last:
            break
        again, again_left_out = plan_again(
            SENT_BACK.format(
                reasons=verdict.reasons(), plan=decision.plan.model_dump_json(exclude_none=True)
            )
        )
        if again.plan is None:
            # The planner asked instead of planning again: the plans it made are all there is.
            stopped = "as the planner asked the shop owner a question instead of planning again"
            break
        decision, left_out = again, again_left_out
    kept = min(reversed(checked), key=lambda each: each.verdict.noes())
    number = checked.index(kept) + 1
    _mark_sent_back(job, plans=len(checked), kept=number)
    notices.post_notice(
        job,
        notices.PLAN_QUALITY_KEPT,
        f"No plan passed the plan quality check in {len(checked)} tries, stopping {stopped}, "
        f"so plan {number}, "
        f'with the fewest "no" answers ({kept.verdict.noes()}), goes on. '
        + " ".join(
            _verdict_words(tried, each.verdict) for tried, each in enumerate(checked, start=1)
        ),
        Message.Level.PROBLEM,
    )
    return kept.decision, kept.left_out


# What a plan_ad call's decision becomes once its plan is sent back and another is kept, so
# the kept plan's call is the last whose decision is "plan" (agents/producer.py reads its
# reason).
SENT_BACK_DECISION = "sent back"


def _mark_sent_back(job: Job, *, plans: int, kept: int) -> None:
    """Mark the planner's last `plans` plan calls but the `kept`th (from 1) as sent back."""
    calls = list(
        job.model_calls.filter(
            purpose="plan_ad", outcome=ModelCall.Outcome.SUCCEEDED, decision="plan"
        ).order_by("-created_at", "-id")[:plans]
    )[::-1]
    for number, call in enumerate(calls, start=1):
        if number != kept:
            call.decision = SENT_BACK_DECISION
            call.save(update_fields=["decision"])


def _verdict_words(number: int, verdict: PlanQuality) -> str:
    return (
        f"Plan {number}: {verdict.decision} ({verdict.noes()} no). "
        f"Every claim once: {verdict.claims_said_once}. {verdict.reasons() or verdict.reason}"
    )
