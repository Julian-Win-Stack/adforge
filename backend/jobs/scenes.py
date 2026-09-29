"""What the model that plans a scene's starting picture is told, handed and answers."""

from typing import Self, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from gateway.types import Handoff

from .planning import ChatMessage, ProductSize

STARTING_PICTURE_INSTRUCTIONS = """\
You plan the starting picture for one scene of a short vertical video ad. In the scene, the \
person in the portrait holds the product and says the scene's line to camera. The picture \
is made by a picture model from two pictures: the portrait first, then one product photo. \
A video model then animates it to say the line, so it is the scene's first frame.
You are shown the portrait and the product photos you may pick from, each labelled with \
its number. Every one shows the product in the ad's colour.
Pick the photo whose view of the product suits the line best: for a line about a detail, \
a photo that shows that detail. Then write the picture model's prompt: use the person from \
the first picture, with the same face, hair and clothes, and the product from the second, \
exactly as it looks, with any label or print unchanged and facing the camera. Pose them \
with the product exactly as "pose" says: it is set from how big the product really is, so \
a product too big to hold stands on the floor and a tiny one is held up close, and the top \
and bottom of the frame stay clear, because text is drawn there in the finished ad. Then \
say their expression (looking into the camera, mouth relaxed as if mid-sentence), the \
framing (upright 9:16, the person's head and shoulders in frame with space above the head), \
and the setting, which should match the portrait's. Ask for a natural, casual phone-video \
look, and no added text, captions or logos.
The producer may add a note, such as what the shop owner asked for this scene. Follow it \
unless it asks for something you can't do with these pictures, and then say so in a reason.
Use the conversation with the shop owner for their wishes about how the ad looks. Facts \
about the product come only from what you are shown.
Give a one-sentence reason for the photo and one for the prompt, written for the shop owner."""

BROLL_PICTURE_INSTRUCTIONS = """\
You plan the starting picture for one scene of a short vertical video ad. The scene doesn't \
show the person talking to camera: it shows what the scene's "shows" describes, while the \
person's voice says the scene's line over it. The picture is made by a picture model from \
two pictures: the portrait first, then one product photo. A video model then animates it, \
with no sound, so it is the scene's first frame.
You are shown the portrait and the product photos you may pick from, each labelled with \
its number. Every one shows the product in the ad's colour.
Pick the photo whose view of the product suits what the scene shows best. Then write the \
picture model's prompt: the product from the second picture, exactly as it looks, with any \
label or print unchanged; the scene as "shows" describes it and nothing more; the setting \
and light of the portrait's; the person from the first picture only if "shows" needs them, \
and then the same person, with the same face, hair and clothes; upright 9:16, with the \
product in the middle so the top and bottom of the frame stay clear: text is drawn there \
in the finished ad. Ask for a natural, casual phone-video look, and no added text, captions \
or logos.
Then write the video model's motion prompt: what moves in the scene, and how, in one or two \
short sentences.
Make nothing up, in either prompt. Show only what "shows" describes: no result, use, \
feature, texture, colour or amount it doesn't state, nothing that makes the product look \
bigger, better or more effective than described, and no part of the product the photos \
don't show.
The producer may add a note, such as what the shop owner asked for this scene. Follow it \
unless it asks for something you can't do with these pictures, or something "shows" \
doesn't describe, and then say so in a reason.
Use the conversation with the shop owner for their wishes about how the ad looks. Facts \
about the product come only from what you are shown.
Give a one-sentence reason for the photo, one for the prompt and one for the motion prompt, \
written for the shop owner."""

# Added by code to every prompt for a scene that shows the product, after what the model
# wrote, so the rule is in every prompt sent whatever the model leaves out.
NOTHING_MADE_UP = "Show only what is described; add or change nothing about the product."


def with_nothing_made_up(prompt: str) -> str:
    """A prompt for a scene that shows the product, as it is sent: NOTHING_MADE_UP added."""
    return f"{prompt} {NOTHING_MADE_UP}"


# How the person is posed with the product in a talking scene, one sentence per size the
# plan can give. Code owns these, not the planner, so the rule holds in every job: the
# same sentence is handed to the model that plans the starting picture and put into the
# clip's motion prompt, so the picture's pose and the motion the video model is asked for
# never disagree (a picture posed one way and a prompt saying another made the video model
# invent limbs). Each keeps the top and bottom of the frame clear: the overlay and the
# captions are drawn there (docs/what-makes-a-good-ad.md).
POSES: dict[ProductSize, str] = {
    # One reading only: "or wears it" let the picture show studs worn and the video model,
    # told to hold something up close, invent a box to hold.
    "tiny": (
        "The person holds the product up close to the camera between finger and thumb, so "
        "it fills a good part of the frame, with any label facing the camera, and the top "
        "and bottom of the frame stay clear."
    ),
    "handheld": (
        "The person holds the product at chest height, beside their face, with any label "
        "facing the camera, so the top and bottom of the frame stay clear."
    ),
    "large": (
        "The product stands on the floor at its real size, and the person stands beside it "
        "with one hand resting on it, framed so both fit, with space above the head and "
        "below the product, so the top and bottom of the frame stay clear."
    ),
}


def pose_for(product_size: str) -> str:
    """The pose for a job whose product is `product_size`. A job planned before the plan
    said the size has none stored, and is posed as handheld, as every job was then."""
    if not product_size:
        return POSES["handheld"]
    # The plan only ever gives one of the three sizes, so a stored size is one of them.
    return POSES[cast(ProductSize, product_size)]


def talking_motion_prompt(product_size: str) -> str:
    """How the video model moves the person in a talking scene's clip: the same for every
    scene of the job, holding the pose its starting picture was planned around. The line's
    audio says what they say, and the starting picture how they look."""
    return (
        "The person talks to the camera naturally, like a casual phone video. "
        f"{pose_for(product_size)} Minimal hand movement."
    )


class StartingPictureHandoff(Handoff):
    scene: int
    line: str
    script: list[str]
    product_colour: str
    colour_photos: list[int]
    person_looks: str
    pose: str
    note: str | None
    conversation: list[ChatMessage]


class BrollPictureHandoff(StartingPictureHandoff):
    shows: str


class StartingPictureChoice(BaseModel):
    photo: int = Field(description="The number of the product photo to make the picture from.")
    photo_reason: str = Field(description="One sentence saying why that photo suits the line.")
    prompt: str = Field(description="The prompt for the picture model.")
    prompt_reason: str = Field(description="One sentence saying why the prompt asks for that.")

    @field_validator("photo_reason", "prompt", "prompt_reason")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


class BrollPictureChoice(StartingPictureChoice):
    motion_prompt: str = Field(description="The prompt for the video model: what moves, and how.")
    motion_prompt_reason: str = Field(
        description="One sentence saying why the motion prompt asks for that."
    )

    @field_validator("motion_prompt", "motion_prompt_reason")
    @classmethod
    def _motion_not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


def starting_picture_choice_for[Choice: StartingPictureChoice](
    colour_photos: list[int], choice: type[Choice]
) -> type[Choice]:
    """`choice` for a job whose photos in the ad's colour are `colour_photos`. A photo
    that isn't one of them fails while the answer is read, like any other broken answer."""

    class StartingPictureChoiceForJob(choice):  # type: ignore[valid-type,misc]
        @model_validator(mode="after")
        def _a_photo_in_the_colour(self) -> Self:
            if self.photo not in colour_photos:
                shown = ", ".join(str(number) for number in colour_photos)
                raise ValueError(
                    f"Photo {self.photo} isn't one showing the product in the ad's colour: "
                    f"those are {shown}."
                )
            return self

    return StartingPictureChoiceForJob
