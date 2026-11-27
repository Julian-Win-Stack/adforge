"""The plan card (spec #768, ADR 0016): after the page is read the producer shows the shop
owner 1 to 3 questions, one of them always which of 3 plans, and nothing is made until the
newest card of the job is answered. Code holds that lock, not the producer's instructions:
every tool that plans scenes, makes a person, a picture or music, starts a scene agent or
assembles refuses while it holds (`hold`). Only the chat's answer to the card lifts it
(`answer`); a message typed in the chat never does."""

from typing import Any, Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from chat import messages
from chat.models import Message, Session
from jobs import notices
from jobs.models import Job, PlanCard

from .loop import Refused, Tool
from .models import ToolCall

# The ad styles a plan can name, and the label the shop owner reads for each. Never "hero".
STYLE_LABELS = {"person_talking": "Person talking", "product_shots": "Product shots"}
# The styles the app can make today, so the only ones a card may offer (spec #768). Product
# shots join once their ads can be made (#770).
STYLES_OFFERED = ("person_talking",)
# Where each style's length starts, in seconds; the producer may move it for the product.
STARTING_SECONDS = {"person_talking": 20, "product_shots": 15}
# The longest ad a plan may promise: 30 seconds when the shop owner asks, and a little over.
MOST_SECONDS = 35
# What the card says of length, as it has no length question.
LONGER = "Want 30 seconds? Say so in the chat."
# The answer the chat sends for "Use the agent's picks", and what the owner's message says.
USE_THE_PICKS = "Use the agent's picks."

LOCKED = (
    "the plan card must be shown and answered first: call show_plan_card with 3 plans and "
    "wait for the shop owner's answer. A message in the chat doesn't answer it."
)


class Plan(BaseModel):
    """One of the card's 3 plans."""

    style: Literal["person_talking", "product_shots"] = Field(
        description='The ad style: "person_talking" or "product_shots".'
    )
    seconds: int = Field(description="How long the ad is, in seconds.")
    title: str = Field(description="A short title, a few words.")
    built_on: str = Field(
        description="One sentence: the one thing this listing has that similar products "
        "don't, which the plan is built on."
    )
    opens: str = Field(description="One sentence: what the ad's first seconds show.")
    doubt: str = Field(
        description="The buyer's doubt the plan answers, in the buyer's words. Kept with the "
        "job; the shop owner isn't shown it."
    )


class Question(BaseModel):
    """A question on the card with choices of its own."""

    asks: str = Field(description="The question, short, in plain words.")
    choices: list[str] = Field(description="2 to 4 choices, each a few words.")
    pick: int = Field(description="Your pick: the number of its choice, from 1.")


class ShowPlanCard(Tool):
    """Show the shop owner the plan card, once the page is read and before anything is
    planned or made: 3 plans to pick from, and at most 2 more questions. Nothing is made until
    they answer it in the chat; your turn ends here. A newer card replaces an open one."""

    name = "show_plan_card"
    ends_the_turn = True
    runs_while_paused = True

    opening: str = Field(
        description='One or two short sentences above the questions, such as "I read the '
        "page and looked at its 8 photos. Here are 3 plans. Pick one, or tell me what you'd "
        'rather have."'
    )
    product_option: Question | None = Field(
        description="Which product option the ad is for, only when the page sells more than "
        "one and the shop owner hasn't said which; otherwise null."
    )
    plans: list[Plan] = Field(description="Exactly 3 plans.")
    plans_pick: int = Field(description="Your pick of the plans: its number, from 1.")
    other: Question | None = Field(
        description="At most one more question of your own that the ad needs answered; "
        "otherwise null."
    )

    def run(self, call: ToolCall) -> str:
        job = call.session.jobs.first()
        if job is None or not page_read(job):
            raise Refused("the product page hasn't been read yet, and the plans are made from it.")
        if not waits_for_a_plan(job) or job.scenes.exists():
            raise Refused("the plan card was already answered, and the ad is planned from it.")
        problem = self._problem()
        if problem is not None:
            notices.record(
                call.session,
                notices.PLAN_CARD_INVALID,
                f"The producer's plan card couldn't be shown: {problem}",
                Message.Level.PROBLEM,
            )
            raise Refused(f"the card can't be shown: {problem} Fix it and call it again.")
        card = self._card()
        with transaction.atomic():
            job.plan_cards.filter(answered_at=None, replaced_at=None).update(
                replaced_at=timezone.now()
            )
            shown = PlanCard.objects.create(job=job, tool_call=call, card=card)
            # An idea of the shop owner's that couldn't be made is answered by the new card.
            job.chosen_plan = None
            job.save(update_fields=["chosen_plan"])
            messages.add(call.session, role=Message.Role.AGENT, plan_card=shown.pk)
        return (
            "The plan card is shown. Nothing is planned or made until the shop owner answers "
            "it: their answer comes to you as their message. Stop here."
        )

    def _problem(self) -> str | None:
        """What makes the card one that can't be shown, or None."""
        if len(self.plans) != 3:
            return f"it has {len(self.plans)} plans, and a card has exactly 3."
        if not 1 <= self.plans_pick <= 3:
            return f"the plans' pick is {self.plans_pick}, and it is the number of a plan, 1 to 3."
        for number, plan in enumerate(self.plans, start=1):
            if plan.style not in STYLES_OFFERED:
                offered = " or ".join(f'"{style}"' for style in STYLES_OFFERED)
                return (
                    f'plan {number} is "{plan.style}", which the app can\'t make yet: '
                    f"use {offered}."
                )
            if not 1 <= plan.seconds <= MOST_SECONDS:
                return (
                    f"plan {number} is {plan.seconds} seconds, and a plan is 1 to {MOST_SECONDS}."
                )
            if not all(text.strip() for text in (plan.title, plan.built_on, plan.opens)):
                return f"plan {number} has no title, built-on or opening."
        for name, question in (("product option", self.product_option), ("other", self.other)):
            if question is None:
                continue
            if not 2 <= len(question.choices) <= 4:
                return f"the {name} question has {len(question.choices)} choices; it takes 2 to 4."
            if not 1 <= question.pick <= len(question.choices):
                return f"the {name} question's pick isn't the number of one of its choices."
        if not self.opening.strip():
            return "it has no opening sentence."
        return None

    def _card(self) -> dict[str, object]:
        """The card as kept and shown: its questions in the order the owner reads them, each
        with a key, its choices and the pick's index (from 0)."""
        questions: list[dict[str, object]] = []
        if self.product_option is not None:
            questions.append(_asked("product_option", self.product_option))
        questions.append(
            {
                "key": "plan",
                "asks": "Which plan?",
                "choices": [plan.model_dump() for plan in self.plans],
                "pick": self.plans_pick - 1,
            }
        )
        if self.other is not None:
            questions.append(_asked("other", self.other))
        return {"opening": self.opening.strip(), "questions": questions}


def _asked(key: str, question: Question) -> dict[str, object]:
    return {
        "key": key,
        "asks": question.asks,
        "choices": list(question.choices),
        "pick": question.pick - 1,
    }


def page_read(job: Job) -> bool:
    """Whether the job's page was read: found to show its product, with a product photo."""
    return bool(job.page_html_key) and job.photos.filter(from_video=None).exists()


def shown(card: PlanCard) -> dict[str, object]:
    """The card as the chat draws it: the doubt each plan answers left out, each style by
    its label, and whether it is open, answered (with the answer) or replaced."""
    questions = []
    for question in card.card["questions"]:
        if question["key"] == "plan":
            choices = [
                {
                    "style": STYLE_LABELS[plan["style"]],
                    "seconds": plan["seconds"],
                    "title": plan["title"],
                    "built_on": plan["built_on"],
                    "opens": plan["opens"],
                }
                for plan in question["choices"]
            ]
            question = {**question, "choices": choices}
        questions.append(question)
    state = "answered" if card.answered_at else "replaced" if card.replaced_at else "open"
    return {
        "id": card.pk,
        "opening": card.card["opening"],
        "questions": questions,
        "longer": LONGER,
        "state": state,
        "answer": card.answer,
    }


def open_card(session: Session) -> PlanCard | None:
    """The job's plan card still waiting on the shop owner, or None."""
    job = session.jobs.first()
    if job is None:
        return None
    return job.plan_cards.filter(answered_at=None, replaced_at=None).last()


def hold(call: ToolCall) -> None:
    """Refuse `call`, with a developer notice, while the plan card's lock holds: the job's
    page may be read but no plan card of it has been answered and nothing is planned yet."""
    if not settings.PLAN_CARD_LOCK:
        return
    job = call.session.jobs.first()
    if job is None or not page_read(job) or not waits_for_a_plan(job) or job.scenes.exists():
        return
    notices.record(
        call.session,
        notices.PLAN_CARD_LOCKED,
        f"The producer called {call.tool} before the plan card was answered, so it was refused.",
        Message.Level.INFO,
    )
    raise Refused(LOCKED)


class NotOpen(Exception):
    """The card was already answered or replaced, so it takes no answer."""


class Replaced(NotOpen):
    """A newer card replaced the card, so only the newer one takes an answer."""


# What the shop owner is told when they answer a card a newer one replaced, as from a tab
# left open on the old one.
ANSWER_THE_NEWEST = "That card was replaced. Please answer the newest one."


class NotAChoice(ValueError):
    """An answer that names a question the card hasn't got, or a choice it hasn't."""


def answer(card: PlanCard, choices: dict[str, int] | None, idea: str = "") -> Message:
    """Answer `card` for the shop owner: with every pick when `choices` is None ("Use the
    agent's picks"), or with their choice in each question by key (an index from 0) and,
    instead of a plan, their own `idea` for the ad ("Make this ad"). The plans question must
    be answered by a plan or an idea; another question left out takes the producer's pick.
    Keeps what was chosen on the job and, for a plan, sets the ad's length to the plan's; an
    idea waits for the producer to make it the plan (TakeOwnersIdea), and the lock holds till
    then. Posts the owner's message saying what was chosen, which the producer reads.
    Raises Replaced for a card a newer one replaced (the shop owner is told to answer the
    newest), NotOpen for one answered already, NotAChoice for an answer the card can't take.
    Call it under the session's work lock."""
    idea = idea.strip() if choices is not None else ""
    with transaction.atomic():
        card = PlanCard.objects.select_for_update().get(pk=card.pk)
        if card.replaced_at is not None and card.answered_at is None:
            raise Replaced
        if card.answered_at is not None:
            raise NotOpen
        questions = card.card["questions"]
        unknown = set(choices or {}) - {question["key"] for question in questions}
        if unknown:
            raise NotAChoice(f"The card has no question {', '.join(sorted(unknown))}.")
        if choices is not None and "plan" not in choices and not idea:
            raise NotAChoice("Pick a plan, or say what you'd rather have.")
        if len(idea) > MOST_IDEA_CHARACTERS:
            raise NotAChoice(f"Say your idea in up to {MOST_IDEA_CHARACTERS} characters.")
        picked: dict[str, Any] = {}
        for question in questions:
            if question["key"] == "plan" and idea:
                picked["plan"] = None
                continue
            index = (choices or {}).get(question["key"], question["pick"])
            if not 0 <= index < len(question["choices"]):
                raise NotAChoice(f"{question['asks']} has no choice {index}.")
            picked[question["key"]] = index
        if idea:
            picked["idea"] = idea
        card.answer = picked
        card.answered_at = timezone.now()
        card.save(update_fields=["answer", "answered_at"])
        job = card.job
        job.chosen_plan = chosen(card)
        fields = ["chosen_plan"]
        if job.chosen_plan["plan"] is not None:
            job.target_seconds = job.chosen_plan["plan"]["seconds"]
            fields.append("target_seconds")
        job.save(update_fields=fields)
        said = "\n".join(_said(question, picked) for question in questions)
        if choices is None:
            said = f"{USE_THE_PICKS}\n{said}"
        assert job.session is not None
        return messages.add(
            job.session, role=Message.Role.USER, text=said, answers_plan_card=card.pk
        )


# The longest idea the shop owner may type on the card, as long as a chat message may be.
MOST_IDEA_CHARACTERS = 2000


def chosen(card: PlanCard) -> dict[str, Any]:
    """What the answered `card` chose: the plan, in full (None when the shop owner gave
    their own idea instead, kept under "idea"), and the answer to each other question, by
    key."""
    assert card.answer is not None
    kept: dict[str, Any] = {}
    for question in card.card["questions"]:
        index = card.answer[question["key"]]
        kept[question["key"]] = None if index is None else question["choices"][index]
    if card.answer.get("idea"):
        kept["idea"] = card.answer["idea"]
    return kept


def _said(question: dict[str, Any], picked: dict[str, Any]) -> str:
    """One question and the choice taken, as the owner's message says it."""
    index = picked[question["key"]]
    if index is None:
        return f"{question['asks']} My own idea: {picked['idea']}"
    choice = question["choices"][index]
    if question["key"] == "plan":
        style = STYLE_LABELS[choice["style"]]
        return f"{question['asks']} “{choice['title']}” ({style}, {choice['seconds']} s)"
    return f"{question['asks']} {choice}"


def for_producer(card: PlanCard) -> str:
    """The answered card as the producer reads it with the owner's answer: the whole chosen
    plan, its doubt too, or the owner's own idea and what to do with it, and every other
    answer."""
    kept = chosen(card)
    others = [
        f"{question['asks']} {kept[question['key']]}"
        for question in card.card["questions"]
        if question["key"] != "plan"
    ]
    also = f" Their other answers: {' '.join(others)}" if others else ""
    plan = kept["plan"]
    if plan is None:
        return (
            "[The shop owner answered the plan card with their own idea instead of a plan: "
            f"“{kept['idea']}”.{also} Turn it into the plan with take_owners_idea, keeping "
            "their words as given, and carry on. If it can't be made (a demo, a style the "
            "app can't make, or something the page contradicts), say why in one short "
            "sentence and show a new plan card.]"
        )
    return (
        f"[The shop owner answered the plan card. The plan: “{plan['title']}”, style "
        f"{plan['style']}, {plan['seconds']} seconds; built on: {plan['built_on']} Opens: "
        f"{plan['opens']} The doubt it answers: {plan['doubt']}{also} Plan the ad from it "
        "now.]"
    )


def waits_for_a_plan(job: Job) -> bool:
    """Whether the job has no chosen plan yet: no card answered, or one answered with the
    shop owner's idea that the producer hasn't made the plan yet."""
    return job.chosen_plan is None or job.chosen_plan.get("plan") is None


class TakeOwnersIdea(Plan, Tool):
    """Make the idea the shop owner typed on the plan card the ad's plan, once you have
    turned it into one: its style, length, title, what it is built on, how it opens and the
    doubt it answers, keeping their words as given. Only when the idea can be made; if it
    can't, say why and show a new plan card instead."""

    name = "take_owners_idea"

    def run(self, call: ToolCall) -> str:
        job = call.session.jobs.first()
        if job is None or job.chosen_plan is None or not job.chosen_plan.get("idea"):
            raise Refused("the shop owner hasn't answered a plan card with an idea of their own.")
        if job.chosen_plan.get("plan") is not None:
            raise Refused("their idea is already the plan.")
        if self.style not in STYLES_OFFERED:
            raise Refused(
                f'the app can\'t make "{self.style}" ads yet. Say so and show a new plan card.'
            )
        if not 1 <= self.seconds <= MOST_SECONDS:
            raise Refused(f"a plan is 1 to {MOST_SECONDS} seconds.")
        plan = Plan.model_validate(self.model_dump(include=set(Plan.model_fields)))
        job.chosen_plan = {**job.chosen_plan, "plan": plan.model_dump()}
        job.target_seconds = self.seconds
        job.save(update_fields=["chosen_plan", "target_seconds"])
        return f"Their idea is the plan: “{self.title}”, {self.seconds} seconds. Plan the ad now."


def _styles_rule() -> str:
    """What the producer is told of the styles a plan may take and where their lengths
    start: only the styles the app can make (STYLES_OFFERED)."""
    offered = " or ".join(f'"{STYLE_LABELS[style]}"' for style in STYLES_OFFERED)
    lengths = ", ".join(
        f"a {STYLE_LABELS[style].lower()} plan starts at {STARTING_SECONDS[style]} seconds"
        for style in STYLES_OFFERED
    )
    return (
        f"Each plan's style is {offered}: the shop owner reads only these labels. "
        f"No demo plans. Lengths: {lengths}; move it when the product calls for it. The card "
        "has no length question: it tells them 30 seconds is theirs if they ask in the chat, "
        "and then you show a new card at that length. "
    )


STYLES_RULE = _styles_rule()
