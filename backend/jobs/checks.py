"""The planning checks, run on the script before anything is rendered: the fact check,
and the length fit when the user set a target length. What each model is handed, what it
must hand back, and the rules it follows."""

import math
from typing import Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

from .planning import BROLL_DETAILS_INSTRUCTIONS, ChatMessage, ScriptScene, photos_missing

# A script fits its target when it runs no more than this much over it. Shorter always fits.
LENGTH_ALLOWANCE_SECONDS = 2
# Times the producer rewrites a line the fact check failed, or shortens a script that
# doesn't fit or a talking line too long for its clip, before the user is asked instead.
MOST_REWRITES = 2
# The longest a talking scene's line may take to say. The video model makes clips of at most
# MOST_CLIP_SECONDS, and the voice's speed is measured on the whole script, not per line.
LONGEST_LINE_SECONDS = 18
# How long a B-roll scene's line must take to say. Its clip lasts 5 to 15 whole seconds
# (MOST_BROLL_SECONDS at most), and the next line starts while the clip's end still plays:
# a line of at least 4 s leaves at most about 1 s of clip under the next line.
SHORTEST_BROLL_LINE_SECONDS = 4
LONGEST_BROLL_LINE_SECONDS = 14
# Times a B-roll line is lengthened, before it is kept as it is, or shortened, over the job's
# whole life, before its scene is said to camera instead. The shop owner is never asked about
# a B-roll line's length.
MOST_LENGTHENINGS = 2
MOST_BROLL_SHORTENINGS = 3

FACT_CHECK_INSTRUCTIONS = """\
You check the script of a short video ad against the product page it was written from. \
You get the page's visible text, followed by any product data the page declares for \
search engines, the conversation with the shop owner so far, the colour the ad shows the \
product in, and the lines to check, each with its scene number. Each message in the \
conversation is labelled "user" for the shop owner or "producer" for the producer.
Some scenes don't show the person talking: they show something else while the line is \
said, described in the scene's "shows", with how the product is used in it, its \
"usage", and what you can see at its end, its "result". Check a "shows", a "usage" and a \
"result" like a line: everything they show, such as what the product is used for, how, \
with what, what it does and any result, must be stated by the page or the shop owner, or \
shown by the product photos. A wrong "usage" or "result" is a wrong "shows". When a scene \
has a "shows", you also get the product photos in the ad's colour and the photos each \
such scene needs. Use them only for what the product looks like: never read a fact such \
as a price or a size from them.
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

REWRITE_INSTRUCTIONS = (
    """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. Some scenes instead show something else while the person's voice says the \
line, described in the scene's "shows"; for a scene where the person talks, "shows" is \
null. The fact check failed one of your scenes. You get the page's text, the \
conversation with the shop owner so far, labelled "user" for them and "producer" for \
you, the colour the ad shows the product in, how many product photos there are and the \
numbers of those showing the product in that colour, the whole script, the scene that \
failed, and each reason it failed, oldest first, with whether its line, its "shows" or \
both were wrong. A scene that shows something has its "usage", how the product is used \
in it, and its "result", what you can see at its end; a wrong "usage" or "result" is a \
wrong "shows". For such a scene, the product photos come after, each labelled with its \
number: "Photo 1", "Photo 2".
Rewrite that one scene whole so every claim in its line, and everything it shows, is \
stated by the page or by the shop owner's own words, or for what the product looks like, \
shown by the product photos. Your own messages only show what was asked: never take a \
fact from them, except a "shows" you proposed that the shop owner then approved, which \
with any change they asked for counts as their own words. Keep what the line is for in the \
ad and about the same length. If it says the price, it must still say the price. Never \
name the product's colour. Never infer or guess.
Give back the scene's "shows" too: unchanged if it wasn't wrong. Keep a scene that shows \
something showing something, unless nothing the page, the photos or the shop owner \
support could be shown: then give null, and the person says the line to camera. For a \
scene where the person talks, always give null, and no B-roll details.
A scene that shows something is a B-roll scene. Give back all its B-roll details with \
its line and "shows", so they always match what it shows.
"""
    + BROLL_DETAILS_INSTRUCTIONS
)

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

LENGTHEN_LINE_INSTRUCTIONS = """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. One scene shows something else while the person's voice says its line, \
described in its "shows", and its line is too short: the clip it plays over lasts longer \
than the line takes to say. You get the page's text, the conversation with the shop \
owner so far, labelled "user" for them and "producer" for you, the colour the ad shows \
the product in, the whole script, the scene whose line is too short, and the fewest words \
it needs at the speed the chosen voice speaks.
Rewrite that one line to have at least the fewest words. Keep what the line is for in the \
ad, and add only what the page or the shop owner's own words state: your own messages \
only show what was asked. It must still match what the scene shows. If it says the price, \
it must still say the price. Never name the product's colour. Never infer or guess."""

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
    """The most words a talking line can have and still be said within LONGEST_LINE_SECONDS."""
    return math.floor(LONGEST_LINE_SECONDS * words_per_second)


def fewest_words_in_a_broll_line(words_per_second: float) -> int:
    """The fewest words a B-roll line can have and take SHORTEST_BROLL_LINE_SECONDS to say."""
    return math.ceil(SHORTEST_BROLL_LINE_SECONDS * words_per_second)


def most_words_in_a_broll_line(words_per_second: float) -> int:
    """The most words a B-roll line can have and still be said within
    LONGEST_BROLL_LINE_SECONDS."""
    return math.floor(LONGEST_BROLL_LINE_SECONDS * words_per_second)


# Which part of a scene the fact check found wrong: its line, what it shows, or both.
Wrong = Literal["line", "shows", "both"]


class LineToCheck(BaseModel):
    scene: int
    line: str
    # What the scene shows while the line is said. None for the person talking to camera.
    shows: str | None = None
    # How the product is used in a scene that shows something, and the result it ends on.
    usage: str | None = None
    result: str | None = None


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
    photo_count: int
    colour_photos: list[int]
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


class RewrittenScene(ScriptScene):
    """A scene rewritten whole: its line, what it shows and its B-roll details together."""

    # Overrides the planner's rule: a rewrite giving a blank "shows" makes the person say
    # the line, and the B-roll details it gave with it are dropped.
    @model_validator(mode="after")
    def _labelled_as_its_kind(self) -> Self:
        if self.shows is None:
            self.broll_kind = self.person_shown = self.usage = self.result = None
            self.needs = []
            return self
        return self.check_broll_details()


def rewritten_scene_for(
    number: int, *, shows_something: bool, photo_count: int
) -> type[RewrittenScene]:
    """A rewrite of scene `number`, which shows something or where the person talks, in a
    job with `photo_count` photos: a rewrite never turns the person talking into a scene
    that shows something else, and follows the planner's rules for a B-roll scene."""

    class RewrittenSceneForItsKind(RewrittenScene):
        @model_validator(mode="after")
        def _talking_stays_talking(self) -> Self:
            if not shows_something and self.shows is not None:
                raise ValueError('The person talks in this scene: its "shows" must be null.')
            return self

        @model_validator(mode="after")
        def _its_pictures_can_be_sent(self) -> Self:
            for problem in (
                photos_missing(self, photo_count),
                self.too_many_pictures(number),
            ):
                if problem is not None:
                    raise ValueError(problem)
            return self

    return RewrittenSceneForItsKind


class ShortenLineHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    most_words: int


class LengthenLineHandoff(Handoff):
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    fewest_words: int


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
