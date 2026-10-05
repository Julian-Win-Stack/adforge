"""The planning checks, run on the script before anything is rendered: the fact check,
and the length fit when the user set a target length. What each model is handed, what it
must hand back, and the rules it follows."""

import math
from typing import Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

from .planning import ChatMessage

# A script fits its target when it runs no more than this much over it. Shorter always fits.
LENGTH_ALLOWANCE_SECONDS = 2
# Times the producer rewrites a line the fact check failed, or shortens a script that
# doesn't fit or a line too long for a clip, before the user is asked instead.
MOST_REWRITES = 2
# The longest a scene's line may take to say. The video model makes clips of at most
# MOST_CLIP_SECONDS, and the voice's speed is measured on the whole script, not per line.
LONGEST_LINE_SECONDS = 18
# The longest a B-roll scene's line may take to say: its clip lasts at most
# MOST_BROLL_SECONDS, and a second is left over for the next line to start over its end.
LONGEST_BROLL_LINE_SECONDS = 14
# How many times a B-roll scene's line is shortened, over the job's whole life, before the
# scene is said to camera instead.
MOST_BROLL_SHORTENINGS = 3

FACT_CHECK_INSTRUCTIONS = """\
You check the script of a short video ad against the product page it was written from. \
You get the page's visible text, followed by any product data the page declares for \
search engines, the conversation with the shop owner so far, the colour the ad shows the \
product in, and the lines to check, each with its scene number. Each message in the \
conversation is labelled "user" for the shop owner or "producer" for the producer.
Some scenes don't show the person talking: they show something else while the line is \
said, described in the scene's "shows". Check a "shows" like a line: everything it shows, \
such as what the product is used for, with what, what it does and any result, must be \
stated by the page or the shop owner, or shown by the product photos. When a scene has a \
"shows", you also get the product photos in the ad's colour. Use them only for what the \
product looks like: never read a fact such as a price or a size from them.
Check every price, number, product name and claim in each line. A line is "ok" only if \
the page or the shop owner's own words state everything it claims. The producer's \
messages are there only to show what was asked: never take a fact from them, except a \
"shows" the producer proposed that the shop owner then approved, which with any change \
they asked for counts as the shop owner's own words. A claim \
nobody states is wrong, even if it is probably true: nothing may be guessed. A price \
must be the price a buyer pays today. A line that names the product's colour, or any \
other colour of the product, is wrong too: the ad shows the colour, never says it. \
Words that mean the same, such as "ml" and "millilitres", are not a problem.
For each wrong line, say whether the line, its "shows" or both are wrong, say what is \
wrong in one sentence, and quote what the page says about it, or say that the page \
doesn't mention it.
Decide "checked" when you checked every line. Decide "unclear" only when the page \
itself is unclear, so no line could be right: for example it gives two different \
prices for the same thing, or contradicts itself. Then ask the shop owner one short, \
specific question and give no verdicts.
Give one sentence saying why, written for the shop owner."""

REWRITE_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. Some scenes instead show something else while the person's voice says the \
line, described in the scene's "shows"; for a scene where the person talks, "shows" is \
null. The fact check failed one of your scenes. You get the page's text, the \
conversation with the shop owner so far, labelled "user" for them and "producer" for \
you, the colour the ad shows the product in, the whole script, the scene that failed, \
and each reason it failed, oldest first, with whether its line, its "shows" or both were \
wrong.
Rewrite that one scene so every claim in its line, and everything its "shows" shows, is \
stated by the page or by the shop owner's own words, or for what the product looks like, \
shown by the product photos. Your own messages only show what was asked: never take a \
fact from them, except a "shows" you proposed that the shop owner then approved, which \
with any change they asked for counts as their own words. Keep what the line is for in the \
ad and about the same length. If it says the price, it must still say the price. Never \
name the product's colour. Never infer or guess.
Give back the scene's "shows" too: unchanged if it wasn't wrong. Keep a scene that shows \
something showing something, unless nothing the page, the photos or the shop owner \
support could be shown: then give null, and the person says the line to camera. For a \
scene where the person talks, always give null."""

SHORTEN_LINE_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. One scene's line takes too long to say: a scene can last only so long. You \
get the page's text, the conversation with the shop owner so far, labelled "user" for \
them and "producer" for you, the colour the ad shows the product in, the whole script, \
the scene whose line is too long, what that scene shows while the line is said if it \
doesn't show the person talking, and the most words that fit at the speed the chosen \
voice speaks.
Rewrite that one line to fit: stay within the most words, and keep what the line is for \
in the ad. If it says the price, it must still say the price. Every claim must be stated \
by the page or by the shop owner's own words: your own messages only show what was \
asked. Never name the product's colour. Never infer or guess."""

SHORTEN_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. The script is too long for the shop owner's target length. You get the \
page's text, the conversation with the shop owner so far, labelled "user" for them and \
"producer" for you, the colour the ad shows the product in, the target length, the most \
words that fit it at the speed the chosen voice speaks, and the script: each scene's \
number, its line, and what the scene shows while the line is said if it doesn't show the \
person talking.
Rewrite the script to fit: trim lines, or drop a scene. Stay within the most words. \
Give each line with the number of the scene it comes from: a scene keeps what it shows, \
so a shortened line must still match it. Keep lines you don't need to change exactly as \
they are. The first line must be one the person says to camera. One line must still say \
the price. Every claim must be stated by the page or by the shop owner's own words: your own \
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


def line_seconds(line: str, words_per_second: float) -> float:
    """How long the voice takes to say one line."""
    return count_words(line) / words_per_second


def most_words_in_a_line(words_per_second: float) -> int:
    """The most words a line can have and still be said within LONGEST_LINE_SECONDS."""
    return math.floor(LONGEST_LINE_SECONDS * words_per_second)


# Which part of a scene the fact check found wrong: its line, what it shows, or both.
Wrong = Literal["line", "shows", "both"]


class LineToCheck(BaseModel):
    scene: int
    line: str
    # What the scene shows while the line is said. None for the person talking to camera.
    shows: str | None = None


class FactCheckHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    lines: list[LineToCheck]


class LineVerdict(BaseModel):
    scene: StrictInt
    verdict: Literal["ok", "wrong"]
    wrong: Wrong | None = Field(
        description='Whether the line, its "shows" or both are wrong, if "wrong". Null if "ok".'
    )
    problem: str | None = Field(description='What is wrong, if "wrong".')
    page_says: str | None = Field(
        description='What the page says about it, or that it doesn\'t mention it, if "wrong".'
    )

    @model_validator(mode="after")
    def _says_why_when_wrong(self) -> Self:
        if self.verdict == "wrong" and not (self.wrong and self.problem and self.page_says):
            raise ValueError(
                'A "wrong" line needs what is wrong, its problem and what the page says.'
            )
        return self


class FactCheck(Judgement):
    decision: Literal["checked", "unclear"]
    question: str | None = Field(description='The question for the shop owner, if "unclear".')
    lines: list[LineVerdict]


def fact_check_for(scenes: list[int], *, showing: list[int]) -> type[FactCheck]:
    """The fact check for exactly these scenes, of which `showing` have a "shows". A
    verdict missing, repeated or for a scene that wasn't sent, or finding fault with a
    "shows" a scene doesn't have, fails while the answer is read."""

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
            for verdict in self.lines:
                if verdict.wrong in ("shows", "both") and verdict.scene not in showing:
                    raise ValueError(
                        f'Scene {verdict.scene} has no "shows": only its line can be wrong.'
                    )
            return self

    return FactCheckForScenes


class Problem(BaseModel):
    wrong: Wrong
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


class RewrittenScene(RewrittenLine):
    shows: str | None = Field(
        description="What the scene shows while the line is said, or null for the person "
        "talking to camera."
    )

    @field_validator("shows")
    @classmethod
    def _blank_is_talking(cls, shows: str | None) -> str | None:
        return " ".join(shows.split()) or None if shows is not None else None


def rewritten_scene_for(*, shows_something: bool) -> type[RewrittenScene]:
    """A rewrite of a scene that shows something, or of one where the person talks: a
    rewrite never turns the person talking into a scene that shows something else."""

    class RewrittenSceneForItsKind(RewrittenScene):
        @model_validator(mode="after")
        def _talking_stays_talking(self) -> Self:
            if not shows_something and self.shows is not None:
                raise ValueError('The person talks in this scene: its "shows" must be null.')
            return self

    return RewrittenSceneForItsKind


class ShortenLineHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    most_words: int


class ShortenHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    target_seconds: int
    most_words: int
    script: list[LineToCheck]


class ShortenedLine(BaseModel):
    scene: int = Field(description="The number of the scene the line comes from.")
    line: str

    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("A scene's line can't be empty.")
        return line


class ShortenedScript(BaseModel):
    lines: list[ShortenedLine] = Field(min_length=1)


def shortened_script_for(script: list[LineToCheck]) -> type[ShortenedScript]:
    """A shortening of `script`: each line from one of its scenes, in the order they play,
    opening on one the person says to camera. Anything else fails while the answer is
    read, like any other broken answer."""
    shows = {scene.scene: scene.shows for scene in script}

    class ShortenedScriptForJob(ShortenedScript):
        @model_validator(mode="after")
        def _scenes_kept_in_order(self) -> Self:
            kept = [line.scene for line in self.lines]
            for scene in kept:
                if scene not in shows:
                    raise ValueError(f"There's no scene {scene}: the script has {len(shows)}.")
            if kept != sorted(set(kept)):
                raise ValueError("Give the lines in the order they play, each scene once.")
            if shows[kept[0]] is not None:
                raise ValueError("The first scene is the person talking to camera.")
            return self

    return ShortenedScriptForJob
