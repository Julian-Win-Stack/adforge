"""The check of a B-roll scene's starting picture, made before any clip is paid for: a judge
shown the picture and the shop photo it was drawn from looks for objects that can't exist,
and what the model writing the scene's prompts is told when a picture failed it. Tested on
98 pictures Julian graded (/mnt/project-files/evals/picture-judge/,
wrong-pictures-and-photo-test.md); Julian agreed the build 2026-10-10 21:55 and the photo and
model 23:05."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from gateway.types import Handoff

from .scenes import BrollPromptHandoff

# A picture that fails is drawn again at most this many times; when the last one drawn fails
# too, the run stops (Julian 2026-10-10 21:48: "the best to crash the run").
MOST_REDRAWS = 2

# The judge is asked about each picture up to this many times, each time on its own, and the
# picture fails when this many of its answers fail it: one answer missed the impossible phone
# about 1 run in 5 (reference/vision-judges.md, rule 6).
ASKS = 3
FAILS_TO_FAIL = 2

# Its examples are made up, never from a test product (vision-judges.md, rule 7). Kept word
# for word as tested (`PARTS_WITH_PHOTO` in /mnt/project-files/evals/picture-judge/prompts.py,
# Julian's yes 23:05); don't reword it for one case.
STARTING_PICTURE_CHECK = """\
You check an AI-drawn photo before it is animated into a video ad. Picture models sometimes \
draw objects that cannot exist, such as a car with headlights on its back, a door hinged on \
both sides, or a hand with six fingers. Find those, and only those.

Work in two steps.
1. Look before you judge. For each main object in the picture (the products, devices, \
furniture and tools, and any hands holding them), write down which side of it faces the \
camera and every part you can see on that side, and where. Describe what is actually drawn, \
not what such an object usually has. Look closely at small parts: lenses, buttons, screens, \
spouts, caps, hinges, handles, fingers.
2. Then judge each object: could a real one of that object, seen from that side, show \
exactly those parts in exactly those places? Compare with how the real object is built: which \
parts sit on which side, and what you could and could not see from one viewpoint at once.

Decide "fail" only when you are sure something is physically impossible: a part on a side \
where the real object can't have it, two sides of one object seen at once, a part fused \
into another, a body part that can't be. Decide "pass" for everything else, including small \
differences in shape, size or proportion, odd but possible poses, blurry or unreadable \
print, dirt or mess, lighting, and anything merely unusual or unlikely.

You may also be shown one shop photo of the product. It tells you what the product is and \
how it is built: which parts it has and where they sit. Use it only for that. Ignore the \
photo's background, setting and anything being done with the product; only the product \
itself matters. Never compare the two pictures: differences in size, shape, angle, distance, \
colour, label or print are not a fail, and the product may be held, turned, upside down or \
partly hidden. Fail the product only if it shows a part a real one can't have there, two \
sides at once, or a label that didn't turn with it."""

# Said beside the two pictures, as tested.
PICTURES_SHOWN = "The picture to judge is first; the shop photo, when shown, is second."


class ObjectLook(BaseModel):
    name: str
    side_facing_camera: str = Field(description="Which side or face of the object the camera sees.")
    parts_seen: str = Field(description="The parts visible on that side, and where they are.")
    a_real_one_could_look_like_this: bool
    why: str = Field(description="One short sentence.")


# The judge's answer, filled in from the top: it looks at each object before it decides, so
# the verdict is built on what it wrote down (vision-judges.md, rule 1). A comment, not a
# docstring: a docstring would be sent to the judge in the answer's schema.
class StartingPictureVerdict(BaseModel):
    objects: list[ObjectLook]
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: what is impossible, in one sentence. Null when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "fail" and not (self.problem or "").strip():
            raise ValueError("A picture that fails must say what is wrong.")
        return self


class StartingPictureCheckHandoff(Handoff):
    """Which of the judge's asks about one picture this is, from 1, and which picture is
    which. The judge is shown the picture and the shop photo it was drawn from, never its
    prompt or line; the number only keeps each ask's answer apart, so one asked again after
    the worker stopped isn't paid for twice."""

    ask: int
    pictures: str = PICTURES_SHOWN


class PictureKeptFailing(Exception):
    """A B-roll scene's starting picture failed its check every time it was drawn, so its step
    fails, answering the producer with its ways on. Holds each picture drawn, oldest first,
    with what the judge found in it."""

    def __init__(self, scene: int, failed: list[tuple[str, list[str]]]) -> None:
        self.scene = scene
        self.failed = failed
        super().__init__(
            f"scene {scene}'s starting picture failed its check all {len(failed)} times it "
            "was drawn"
        )


# Added to what the model writing a B-roll scene's prompts is told when the last picture
# drawn for it failed its check.
REDO_RULES = """\
The last picture drawn for this scene failed its check: "redo" holds the prompts it was \
drawn from and each problem found, and the picture is shown last. Write new prompts that fix \
every problem: change what made it go wrong, such as two orders that can't both be true, or \
words too vague to draw a real object from. Keep what was right."""


class Redo(Handoff):
    """Why a B-roll scene's prompts are written again: the prompts its last picture was
    drawn from, and what its check found wrong with it."""

    picture_prompt: str
    video_prompt: str
    problems: list[str]


class BrollRedoHandoff(BrollPromptHandoff):
    redo: Redo
