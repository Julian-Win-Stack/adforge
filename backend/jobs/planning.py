"""The producer's plan: what it is handed, what it must hand back, and the rules it follows."""

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from gateway.types import Handoff, Judgement

PLAN_INSTRUCTIONS = """\
You are the producer of a short vertical video ad for one product. A person speaks to \
camera, one line per scene, and each scene shows the product. You get the product page's \
visible text, followed by any product data the page declares for search engines, the \
target length in seconds (or null if the shop owner didn't set one), how many product \
photos there are, and the shop owner's answers to anything you asked before.
Every claim in the ad must come from the page or from the shop owner's answers. Never \
infer, guess or make anything up: not a price, a size, a material, a benefit or a colour.
Decide "plan" when you can plan the whole ad from what you have. Give the scenes in the \
order they play: each scene's line, exactly as the person will say it, and its slot, the \
whole number of seconds the scene lasts. Set question to null.
Decide "ask" when the page conflicts with itself (such as several different prices for \
the same product) or is missing something the ad needs, so that planning would mean \
guessing. Ask the shop owner one short, specific question, and set plan to null.
Give one sentence saying why: for a plan, why this many scenes; for a question, why you \
need to ask. Write it for the shop owner."""


class PlannedScene(BaseModel):
    line: str = Field(description="Exactly what the person says in this scene.")
    slot_seconds: int = Field(strict=True, ge=1, description="Whole seconds the scene lasts.")

    # A validator rather than min_length, which OpenAI's structured output doesn't accept.
    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("A scene's line can't be empty.")
        return line


class Plan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1)


class ProducerDecision(Judgement):
    decision: Literal["plan", "ask"]
    question: str | None = Field(description='The question for the shop owner, if "ask".')
    plan: Plan | None = Field(description='The plan, if "plan".')

    @model_validator(mode="after")
    def _plan_or_question(self) -> Self:
        if self.decision == "plan" and (self.plan is None or self.question is not None):
            raise ValueError('A "plan" decision needs a plan and no question.')
        if self.decision == "ask" and (not self.question or self.plan is not None):
            raise ValueError('An "ask" decision needs a question and no plan.')
        return self


class Answer(Handoff):
    question: str
    answer: str


class PlanHandoff(Handoff):
    product_url: str
    page_text: str
    target_seconds: int | None
    photo_count: int
    answers: list[Answer]
