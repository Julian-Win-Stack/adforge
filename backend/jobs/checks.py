"""The planning checks, run on the script before anything is rendered: the fact check,
the default whole-clip length cap, and the owner's target length. What each model is handed,
what it must hand back, and the rules it follows."""

import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

from .pace import (
    FIXED_WORDS_PER_SECOND,
    SECONDS_AFTER_THE_LINE,
    SHORTEST_BROLL_LINE_SECONDS,
    fewest_words_in_a_broll_line,
    h3_clip_seconds,
)
from .planning import (
    END_CARD_SECONDS,
    NOT_A_CLAIM,
    SPOKEN_LINE_RULE,
    ChatMessage,
    ScriptScene,
    brand_missing,
    needs_missing,
)

# A script fits its target when its whole clips run no more than this much over it. Shorter
# always fits.
LENGTH_ALLOWANCE_SECONDS = 2
DEFAULT_AD_SECONDS = 30
DEFAULT_AD_MAX_SECONDS = 35
# Maximum fact rewrites or script shortenings before a producer handback or a longer ad.
MOST_REWRITES = 2
# Maximum line lengthenings or shortenings before the producer must fix the scene.
MOST_LENGTHENINGS = 2
MOST_BROLL_SHORTENINGS = 3


def h3_script_seconds(lines: list[str]) -> int:
    """The ad's length as whole clips, including the action after each line, and the end
    card after them (spec #401: its time counts toward the cap and the target)."""
    return sum(h3_clip_seconds(count_words(line)) for line in lines) + END_CARD_SECONDS


@dataclass(frozen=True)
class LengthFit:
    """A script's whole-clip duration and whether it fits its length: the default cap, or
    the owner's target."""

    seconds: int
    fits: bool


def default_length_fit(lines: list[str]) -> LengthFit:
    """Apply the default length policy wherever a script is accepted or rendered."""
    seconds = h3_script_seconds(lines)
    return LengthFit(seconds=seconds, fits=seconds <= DEFAULT_AD_MAX_SECONDS)


def target_length_fit(lines: list[str], target_seconds: int) -> LengthFit:
    """Whether a script's whole clips play within the owner's target, measured the way a
    default ad is: shorter always fits, and up to LENGTH_ALLOWANCE_SECONDS over still does."""
    seconds = h3_script_seconds(lines)
    return LengthFit(seconds=seconds, fits=seconds <= target_seconds + LENGTH_ALLOWANCE_SECONDS)


def most_words_in_clips(seconds: int, clips: int) -> int:
    """The most words `clips` clips can say and still add up to `seconds` in whole clips
    with the end card after them: each clip plays SECONDS_AFTER_THE_LINE longer than its
    line, and up to a second more where it is rounded up."""
    overhead = END_CARD_SECONDS + clips * (SECONDS_AFTER_THE_LINE + 1)
    return math.floor(round((seconds - overhead) * FIXED_WORDS_PER_SECOND, 6))


# Every rewrite sees the whole script: how its line must fit in it.
KEEPS_ITS_PLACE = (
    "A rewritten line keeps its scene's place in the script: it follows on from the scenes "
    "before it and leads into the ones after, and never says again what another scene's line "
    "already says. When the line says what kind of thing the product is, or tells the "
    "page's story as the product's story, the rewrite still does. "
)

FACT_CHECK_INSTRUCTIONS = (
    """\
You check the script of a short video ad against the product page it was written from. \
You get the page's visible text, followed by any product data the page declares for \
search engines, the conversation with the shop owner so far, the colour the ad shows the \
product in, and the lines to check, each with its scene number. Each message in the \
conversation is labelled "user" for the shop owner or "producer" for the producer. You \
also get what the product's video says: the words said in each of the shop's product \
videos, one per video. What the product's video says counts as the shop's own words, like \
the page's text. \
And you get the moments of the shop's product videos, one a line: the video's number, \
the moment's number, its seconds, what happens and what is on screen.
Some scenes don't show the person talking: they show something else while the line is \
said, described in the scene's "shows", with how the product is used in it, its \
"usage", and what you can see at its end, its "result". A wrong "usage" or "result" is a \
wrong "shows". In a "shows", a "usage" and a "result", using the product the normal \
everyday way it is made to be used is not a claim and is never wrong: such as wearing a \
watch on the wrist, zipping up a jacket, cooking in a pan or pouring from a jug. Normal \
styling, such as a jacket worn over a T-shirt, is not a claim either, but how the product \
looks, such as its colour, shape or finish, still is. Where the page or the shop's videos \
tell how to use it, a different method is wrong, such as stirring where the page says to \
shake. This is for what a scene shows only: a spoken line is checked as below, every \
claim in it. Check the rest of what a scene shows like a line: what the product does or \
achieves, such as a wrinkle-free shirt or smoother skin, a use the page doesn't give, \
such as using it on a material or in a place the page doesn't name, and any number, size \
or price. A number, a size or a price must be stated by the page or the shop owner; a \
use or a result may instead be plainly shown by the shop's product videos or photos. \
When a scene has a "shows", you also get the product photos in the ad's colour and the \
photos each such scene picked. A photo marked as a frame of the product's video is a \
still from that video, with the scenes that picked it. The video moments, their frames \
and the page photos are the shop's own showing of how the product is used: a use or \
result they plainly show is shown by the shop, but only what can be seen in them. Any \
detail of a result that the page doesn't state and they don't show, such as how long it \
lasts or how quickly it works, is wrong. Never read a fact such as a price, a number or \
a size from a photo or a moment.
Check every price, number, product name and claim in each line. A line is "ok" only if \
the page or the shop owner's own words state everything it claims. """
    + NOT_A_CLAIM
    + """ The producer's \
messages are there only to show what was asked: never take a fact from them, except a \
"shows" the producer proposed that the shop owner then approved, which with any change \
they asked for counts as the shop owner's own words. A claim \
nobody states is wrong, even if it is probably true: nothing may be guessed. A price \
must be the price a buyer pays today. Words that mean the same, such as "ml" and \
"millilitres", are not a problem.
For each wrong line, say whether the line, its "shows" or both are wrong, say what is \
wrong in one sentence, and quote what the page says about it, or say that the page \
doesn't mention it.
Decide "checked" after checking every line. A claim contradicted by the page is wrong:
quote the contradiction in its verdict so the producer can replace or remove the claim.
Never ask the shop owner to settle the check.
Give one sentence saying why, written for the shop owner."""
)

SHORTEN_LINE_INSTRUCTIONS = (
    """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. One scene's line takes too long to say: a scene can last only so long. You \
get the page's text, the conversation with the shop owner so far, labelled "user" for \
them and "producer" for you, the colour the ad shows the product in, the whole script, \
the scene whose line is too long, what that scene shows while the line is said if it \
doesn't show the person talking, and the most words that fit at the speed the chosen \
voice speaks.
Rewrite that one line to fit: stay within the most words, and keep what the line is for in the \
ad. """
    + KEEPS_ITS_PLACE
    + SPOKEN_LINE_RULE
    + """\
If it says the price, it must still say the price. Every claim must be stated by the page \
or by the shop owner's own words: your own messages only show what was asked. Never infer or \
guess."""
)

# Lengthening adds no new product fact (Julian, Nov 8 2026, thread "Stop rule drift after
# planning", part 2): told "add only what the page states", it added a new product fact in 7
# of 7 round 12 lengthenings.
LENGTHEN_LINE_INSTRUCTIONS = (
    """\
You are the producer of a short vertical video ad. A person speaks to camera, one line \
per scene. One scene shows something else while the person's voice says its line, \
described in its "shows", and its line is too short: the clip it plays over lasts longer \
than the line takes to say. You get the page's text, the conversation with the shop \
owner so far, labelled "user" for them and "producer" for you, the colour the ad shows \
the product in, the whole script, the scene whose line is too short, and the fewest words \
it needs at the speed the chosen voice speaks.
Rewrite that one line to have at least the fewest words. Keep what the line is for in the ad. \
Never add a new fact about the product, even one the page or the shop owner state: lengthen \
it only by saying the same point more fully, or with her own feeling or everyday words, \
which need no source. Your own messages only show what was asked. """
    + KEEPS_ITS_PLACE
    + SPOKEN_LINE_RULE
    + """\
It must still match what the scene shows. If it says the price, it must still say \
the price. Never infer or guess."""
)

SHORTEN_INSTRUCTIONS = (
    """\
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
they are. """
    + KEEPS_ITS_PLACE
    + SPOKEN_LINE_RULE
    + """\
Keep the first scene, \
the hook, and the last, the call to action: never drop either. If the script says a \
price, keep it in one line; never introduce a price the script omitted. Every claim must be \
stated by the page or by the shop owner's own words: your own \
messages only show what was asked. Never infer or \
guess. Give the lines in the order they play."""
)


def count_words(text: str) -> int:
    return len(text.split())


def line_seconds(line: str, words_per_second: float) -> float:
    """How long the voice takes to say one line."""
    return count_words(line) / words_per_second


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
    video_transcripts: list[str]
    # Saved before the moments were sent (round 12 and earlier, the eval's cases): none.
    video_moments: str = ""


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
    decision: Literal["checked"]
    lines: list[LineVerdict]


def fact_check_for(scenes: list[int], *, showing: list[int]) -> type[FactCheck]:
    """The fact check for exactly these scenes, of which `showing` have a "shows". A
    verdict missing, repeated or for a scene that wasn't sent, or finding fault with a
    "shows" a scene doesn't have, fails while the answer is read."""

    class FactCheckForScenes(FactCheck):
        @model_validator(mode="after")
        def _one_verdict_per_scene(self) -> Self:
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
    owner_words: list[str] = Field(default_factory=list)
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    library_pictures: int
    pictures_not_shown: int
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


def rewritten_line_for(brand: str = "") -> type[RewrittenLine]:
    """A rewrite of one scene's line. For the last scene of an ad with a `brand`, the new
    line still says the brand, as the planner's was made to (planning.brand_missing)."""
    if not brand.strip():
        return RewrittenLine

    class RewrittenLastLine(RewrittenLine):
        @model_validator(mode="after")
        def _says_the_brand(self) -> Self:
            missing = brand_missing(self.line, brand)
            if missing is not None:
                raise ValueError(missing)
            return self

    return RewrittenLastLine


class RewrittenScene(ScriptScene):
    """A scene rewritten whole: its line, what it shows and its B-roll details together."""

    # Overrides the planner's rule: a rewrite giving a blank "shows" makes the person say
    # the line, and the B-roll details it gave with it are dropped.
    @model_validator(mode="after")
    def _labelled_as_its_kind(self) -> Self:
        if self.shows is None:
            self.broll_kind = self.person_shown = self.usage = self.result = None
            self.needs = []
            self.thin_film = False
            return self
        return self.check_broll_details()


def rewritten_scene_for(
    number: int,
    *,
    shows_something: bool,
    library: Collection[int],
    kind: Literal["talking", "broll"] | None = None,
    kind_why: str = "",
    brand: str = "",
) -> type[RewrittenScene]:
    """A rewrite of scene `number`, which shows something or where the person talks, in a
    job whose library holds the numbers in `library`: a rewrite never turns the person
    talking into a scene that shows something else, and follows the planner's rules for a
    B-roll scene. Given the ad's `brand` for its last scene, the rewritten line still says
    it."""

    class RewrittenSceneForItsKind(RewrittenScene):
        @model_validator(mode="before")
        @classmethod
        def _keeps_its_h3_kind(cls, value: Any) -> Any:
            if kind is not None and isinstance(value, dict):
                value = {"kind": kind, "kind_why": kind_why, **value}
                if value["kind"] != kind or not str(value.get("shows") or "").strip():
                    raise ValueError("Keep the scene's kind and give its action with the product.")
                if kind == "broll":
                    if not value["kind_why"].strip():
                        raise ValueError(
                            "A B-roll scene needs why it cannot be shown at arm's length."
                        )
            return value

        @model_validator(mode="after")
        def _talking_stays_talking(self) -> Self:
            if not shows_something and self.shows is not None:
                raise ValueError('The person talks in this scene: its "shows" must be null.')
            return self

        @model_validator(mode="after")
        def _its_needed_photos_exist(self) -> Self:
            problem = needs_missing(self, library)
            if problem is not None:
                raise ValueError(problem)
            return self

        @model_validator(mode="after")
        def _the_last_line_says_the_brand(self) -> Self:
            missing = brand_missing(self.line, brand)
            if missing is not None:
                raise ValueError(missing)
            return self

    return RewrittenSceneForItsKind


class ShortenLineHandoff(Handoff):
    owner_words: list[str] = Field(default_factory=list)
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    most_words: int


class LengthenLineHandoff(Handoff):
    owner_words: list[str] = Field(default_factory=list)
    page_text: str
    conversation: list[ChatMessage]
    product_colour: str
    script: list[LineToCheck]
    scene: int
    fewest_words: int


class ShortenHandoff(Handoff):
    owner_words: dict[int, list[str]] = Field(default_factory=dict)
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


def shortened_script_for(
    script: list[LineToCheck], *, default_length: bool = False, brand: str = ""
) -> type[ShortenedScript]:
    """A shortening of `script`: each line from one of its scenes, in the order they play,
    opening on its first scene, the hook, whether that scene is said to camera or shows the
    product, and with an ad's `brand`, ending on a line that says it. A default-length
    shortening keeps every scene and fits the whole-clip cap. Anything else fails while the
    answer is read, like any other broken answer."""
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
            if kept[0] != script[0].scene:
                raise ValueError("Keep the first scene, the hook: never drop it.")
            if len(script) > 1 and kept[-1] != script[-1].scene:
                raise ValueError("Keep the last scene, the call to action: never drop it.")
            missing = brand_missing(self.lines[-1].line, brand)
            if missing is not None:
                raise ValueError(missing)
            if default_length:
                if kept != [scene.scene for scene in script]:
                    raise ValueError("Keep every scene; shorten its line instead of dropping it.")
                fewest = fewest_words_in_a_broll_line(FIXED_WORDS_PER_SECOND)
                if any(count_words(line.line) < fewest for line in self.lines):
                    raise ValueError(
                        f"Every line must still take at least {SHORTEST_BROLL_LINE_SECONDS} "
                        f"seconds ({fewest} words)."
                    )
                if not default_length_fit([line.line for line in self.lines]).fits:
                    raise ValueError(
                        "Shorten the lines so their whole clips and the end card add up to "
                        "at most 35 seconds."
                    )
            return self

    return ShortenedScriptForJob
