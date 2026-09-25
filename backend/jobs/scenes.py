"""What the model that plans a scene's starting picture is told, handed and answers."""

from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from gateway.types import Handoff

from .planning import ChatMessage

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
exactly as it looks, with any label or print unchanged and facing the camera. Say how they \
hold the product, their pose and expression (looking into the camera, mouth relaxed as if \
mid-sentence), the framing (head and shoulders, with space above the head, upright 9:16), \
and the setting, which should match the portrait's. Ask for a natural, casual phone-video \
look, and no added text, captions or logos.
The producer may add a note, such as what the shop owner asked for this scene. Follow it \
unless it asks for something you can't do with these pictures, and then say so in a reason.
Use the conversation with the shop owner for their wishes about how the ad looks. Facts \
about the product come only from what you are shown.
Give a one-sentence reason for the photo and one for the prompt, written for the shop owner."""


# How the video model moves the person in every clip. The same for every scene: the line's
# audio says what they say, and the starting picture how they look.
CLIP_MOTION_PROMPT = (
    "The person talks to the camera naturally, like a casual phone video, holding the "
    "product still beside their face with any label facing the camera. Minimal hand movement."
)


class StartingPictureHandoff(Handoff):
    scene: int
    line: str
    script: list[str]
    product_colour: str
    colour_photos: list[int]
    person_looks: str
    note: str | None
    conversation: list[ChatMessage]


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


def starting_picture_choice_for(colour_photos: list[int]) -> type[StartingPictureChoice]:
    """The choice for a job whose photos in the ad's colour are `colour_photos`. A photo
    that isn't one of them fails while the answer is read, like any other broken answer."""

    class StartingPictureChoiceForJob(StartingPictureChoice):
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
