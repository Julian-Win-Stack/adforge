"""The check of a B-roll scene's starting picture, made before any clip is paid for: what the
model checking it is told, handed and answers, and what the model writing the scene's
prompts is told when a picture failed it (evals/judge-notes.md, "judge the start picture
too", Julian 2026-10-07)."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from gateway.types import Handoff

from .scenes import BrollPromptHandoff

# A picture that fails is drawn again at most this many times; the last one drawn is kept.
MOST_REDRAWS = 2

STARTING_PICTURE_CHECK = """\
You check the starting picture of one B-roll scene of a short vertical video ad before a \
video model animates it into a clip, which costs money. You are shown the shop photo of the \
product, then the starting picture. The picture model was asked for the picture in \
"picture_prompt"; the video model will then be asked for "video_prompt", starting from this \
picture. The scene shows what "shows" describes.
Look at the picture closely, and judge each check on its own. A check passes only when \
nothing in the picture breaks it. When it fails, say exactly what is wrong in one sentence, \
naming the part and what it should look like.
- real_objects: every object is a real, ordinary version of itself, with the shape, parts \
and proportions it has in real life, such as a toilet whose rim is level and whose seat \
fits its bowl. Dirt or mess the scene asks for is fine; a made-up shape is not.
- matches_the_shop_photo: the product looks exactly as in the shop photo: its shape, neck, \
cap, nozzle, label and print, and the colour of each small part. Compare the picture with \
the photo itself, not with the prompt's words.
- label_turns_with_the_product: when the product is turned, tipped or upside down, its \
label and print turn with it, as printing on a real object would.
- shows_the_before: the picture shows the moment just before the action in "video_prompt", \
not its result already there.
- no_face: when "person_shown" is "no face", no face, mouth or chin is in the picture. \
Passes whenever "person_shown" is anything else.
- prompts_agree: "picture_prompt" and "video_prompt" never ask for two things the product \
or the scene can't do at once, such as a bottle "upright" with its "nozzle pointing down", \
or "squeezed" with "no gel", and "video_prompt" asks for nothing this picture contradicts."""

# Added to what the model writing a B-roll scene's prompts is told when the last picture
# drawn for it failed its check.
REDO_RULES = """\
The last picture drawn for this scene failed its check: "redo" holds the prompts it was \
drawn from and each problem found, and the picture is shown last. Write new prompts that fix \
every problem: change what made it go wrong, such as two orders that can't both be true, or \
words too vague to draw a real object from. Keep what was right."""


class CheckResult(BaseModel):
    passes: bool
    problem: str | None = Field(
        description="When it fails, what is wrong, in one sentence. Null when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if not self.passes and not (self.problem or "").strip():
            raise ValueError("A check that fails must say what is wrong.")
        return self


class StartingPictureCheck(BaseModel):
    real_objects: CheckResult
    matches_the_shop_photo: CheckResult
    label_turns_with_the_product: CheckResult
    shows_the_before: CheckResult
    no_face: CheckResult
    prompts_agree: CheckResult

    def problems(self) -> list[str]:
        """What is wrong with the picture, one sentence for each check it fails."""
        checks = (getattr(self, name) for name in type(self).model_fields)
        return [check.problem for check in checks if not check.passes and check.problem]


class StartingPictureCheckHandoff(Handoff):
    shows: str
    broll_kind: str
    person_shown: str
    usage: str
    result: str
    picture_prompt: str
    video_prompt: str


class Redo(Handoff):
    """Why a B-roll scene's prompts are written again: the prompts its last picture was
    drawn from, and what its check found wrong with it."""

    picture_prompt: str
    video_prompt: str
    problems: list[str]


class BrollRedoHandoff(BrollPromptHandoff):
    redo: Redo
