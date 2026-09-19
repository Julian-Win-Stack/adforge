"""The producer's plan: what it is handed, what it must hand back, and the rules it follows."""

from typing import Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

PLAN_INSTRUCTIONS = """\
You are the producer of a short vertical video ad for one product. A person speaks to \
camera, one line per scene, and each scene shows the product. You get the product page's \
visible text, followed by any product data the page declares for search engines, the \
target length in seconds (or null if the shop owner didn't set one), how many product \
photos there are, and the shop owner's answers to anything you asked before. After that \
come the product photos themselves, each labelled with its number: "Photo 1", "Photo 2".
Every claim in the ad must come from the page or the shop owner's answers. The photos \
are only for the product's colour: never take any other fact from them, such as text on \
a label. Never infer, guess or make anything up: not a price, a size, a material, a \
benefit or a colour.
Decide "plan" when you can plan the whole ad from what you have. Give the scenes in the \
order they play, with each scene's line exactly as the person will say it. With a target \
length, write only as many words as fit it when spoken at an easy pace. One line says \
the product's price: the price a buyer pays today, so on a sale, the sale price. No line \
names the product's colour: the ad shows the colour, never says it. Name the product's \
colour as the photos show it, in plain words such as "sage green", and give the numbers \
of the photos that show the product in that colour. If the product comes in several \
colours, don't ask which: pick one the photos show. Describe the person who presents the \
ad: how they look, for a portrait, and how their voice sounds, for a voice designed to \
match. Choose someone who suits the product and its buyers, and never a real, famous \
person. Set question to null.
Decide "ask" when you don't know the one price to say, because neither the page nor the \
shop owner's answers give a price, or they give different prices to choose between (such \
as a single item, a pack and a subscription). Also decide "ask" when the page conflicts \
with itself or is missing something else the ad needs, so that planning would mean \
guessing. Ask the shop owner one short, specific question, and set plan to null.
Give one sentence saying why: for a plan, why this many scenes; for a question, why you \
need to ask. Write it for the shop owner."""


class PlannedScene(BaseModel):
    line: str = Field(description="Exactly what the person says in this scene.")

    # A validator rather than min_length, which OpenAI's structured output doesn't accept.
    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("A scene's line can't be empty.")
        return line


class Plan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1)
    product_colour: str = Field(
        description='The product\'s colour as the photos show it, such as "sage green".'
    )
    colour_photos: list[StrictInt] = Field(
        min_length=1, description="The numbers of the photos showing the product in that colour."
    )
    person_looks: str = Field(
        description="How the person presenting the ad looks: age, style, clothes, setting."
    )
    person_voice: str = Field(description="How the person's voice sounds: age, accent, tone, pace.")

    @field_validator("product_colour", "person_looks", "person_voice")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


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


def producer_decision_for(photo_count: int) -> type[ProducerDecision]:
    """The producer's decision for a job with `photo_count` photos. A plan naming a photo
    the job doesn't have fails while the answer is read, like any other broken plan."""

    class ProducerDecisionForJob(ProducerDecision):
        @model_validator(mode="after")
        def _real_photos(self) -> Self:
            for number in self.plan.colour_photos if self.plan else []:
                if not 1 <= number <= photo_count:
                    raise ValueError(f"There's no photo {number}: the job has {photo_count}.")
            return self

    return ProducerDecisionForJob


class Answer(Handoff):
    question: str
    answer: str


class PlanHandoff(Handoff):
    product_url: str
    page_text: str
    target_seconds: int | None
    photo_count: int
    answers: list[Answer]
