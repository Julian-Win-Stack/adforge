"""The planning checks, run on the script before anything is rendered: the fact check,
and the length fit when the user set a target length. What each model is handed, what it
must hand back, and the rules it follows."""

import math
from typing import Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

from .planning import ChatMessage

# A script fits its target when it runs no more than this much over it. Shorter always fits.
LENGTH_ALLOWANCE_SECONDS = 1
# Times the producer rewrites a line the fact check failed, or shortens a script that
# doesn't fit, before the user is asked instead.
MOST_REWRITES = 2

FACT_CHECK_INSTRUCTIONS = """\
You check the script of a short video ad against the product page it was written from. \
You get the page's visible text, followed by any product data the page declares for \
search engines, the conversation with the shop owner so far, the colour the ad shows the \
product in, and the lines to check, each with its scene number. Each message in the \
conversation is labelled "user" for the shop owner or "producer" for the producer.
Check every price, number, product name and claim in each line. A line is "ok" only if \
the page or the shop owner's own words state everything it claims. The producer's \
messages are there only to show what was asked: never take a fact from them. A claim \
nobody states is wrong, even if it is probably true: nothing may be guessed. A price \
must be the price a buyer pays today. A line that names the product's colour, or any \
other colour of the product, is wrong too: the ad shows the colour, never says it. \
Words that mean the same, such as "ml" and "millilitres", are not a problem.
For each wrong line, say what is wrong in one sentence, and quote what the page says \
about it, or say that the page doesn't mention it.
Decide "checked" when you checked every line. Decide "unclear" only when the page \
itself is unclear, so no line could be right: for example it gives two different \
prices for the same thing, or contradicts itself. Then ask the shop owner one short, \
specific question and give no verdicts.
Give one sentence saying why, written for the shop owner."""

REWRITE_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. The fact check failed one of your lines. You get the page's text, the \
conversation with the shop owner so far, labelled "user" for them and "producer" for \
you, the colour the ad shows the product in, the whole script, the scene whose line \
failed, and each reason it failed, oldest first.
Rewrite that one line so every claim in it is stated by the page or by the shop owner's \
own words. Your own messages only show what was asked: never take a fact from them. Keep \
what the line is for in the ad and about the same length. If it says the price, it \
must still say the price. Never name the product's colour. Never infer or guess."""

SHORTEN_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. The script is too long for the shop owner's target length. You get the \
page's text, the conversation with the shop owner so far, labelled "user" for them and \
"producer" for you, the colour the ad shows the product in, the target length, the most \
words that fit it at the speed the chosen voice speaks, and the script.
Rewrite the script to fit: trim lines, or drop a scene. Stay within the most words. \
Keep lines you don't need to change exactly as they are. One line must still say the \
price. Every claim must be stated by the page or by the shop owner's own words: your own \
messages only show what was asked. Never name the product's colour. Never infer or \
guess. Give the lines in the order they play."""


def count_words(text: str) -> int:
    return len(text.split())


def script_seconds(lines: list[str], words_per_second: float) -> float:
    """How long the voice takes to say the whole script."""
    return sum(count_words(line) for line in lines) / words_per_second


def fits_target(seconds: float, target_seconds: int) -> bool:
    return seconds <= target_seconds + LENGTH_ALLOWANCE_SECONDS


def most_words(target_seconds: int, words_per_second: float) -> int:
    """The most words the voice can say within the target, allowance included."""
    return math.floor((target_seconds + LENGTH_ALLOWANCE_SECONDS) * words_per_second)


class LineToCheck(BaseModel):
    scene: int
    line: str


class FactCheckHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    lines: list[LineToCheck]


class LineVerdict(BaseModel):
    scene: StrictInt
    verdict: Literal["ok", "wrong"]
    problem: str | None = Field(description='What is wrong, if "wrong".')
    page_says: str | None = Field(
        description='What the page says about it, or that it doesn\'t mention it, if "wrong".'
    )

    @model_validator(mode="after")
    def _says_why_when_wrong(self) -> Self:
        if self.verdict == "wrong" and not (self.problem and self.page_says):
            raise ValueError('A "wrong" line needs its problem and what the page says.')
        return self


class FactCheck(Judgement):
    decision: Literal["checked", "unclear"]
    question: str | None = Field(description='The question for the shop owner, if "unclear".')
    lines: list[LineVerdict]


def fact_check_for(scenes: list[int]) -> type[FactCheck]:
    """The fact check for exactly these scenes. A verdict missing, repeated or for a
    scene that wasn't sent fails while the answer is read."""

    class FactCheckForScenes(FactCheck):
        @model_validator(mode="after")
        def _one_verdict_per_scene(self) -> Self:
            if self.decision == "unclear":
                if not self.question:
                    raise ValueError('An "unclear" decision needs a question.')
                return self
            judged = sorted(verdict.scene for verdict in self.lines)
            if judged != sorted(scenes):
                raise ValueError(f"Expected one verdict for each of scenes {sorted(scenes)}.")
            return self

    return FactCheckForScenes


class Problem(BaseModel):
    problem: str
    page_says: str


class RewriteHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    problems: list[Problem]


class RewrittenLine(BaseModel):
    line: str

    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("The line can't be empty.")
        return line


class ShortenHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    target_seconds: int
    most_words: int
    script: list[str]


class ShortenedScript(BaseModel):
    lines: list[str] = Field(min_length=1)

    @field_validator("lines")
    @classmethod
    def _each_says_something(cls, lines: list[str]) -> list[str]:
        if any(not line.strip() for line in lines):
            raise ValueError("A scene's line can't be empty.")
        return lines
