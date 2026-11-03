"""What the model that plans a scene's starting picture is told, handed and answers."""

from typing import Self, cast

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from gateway.types import Handoff

from .planning import ChatMessage, ProductSize

# Which photo a starting picture takes the product from (Julian 2026-10-22 19:17): the plan's
# marks of the photos in the ad's colour are advice, never a gate, so a product whose photos
# all show it in a pack, a set or beside its other colours is still made. Its colour always
# comes from a photo, never from words.
COLOUR_PICK = (
    '"colour_photos" names the photos that show the product in the ad\'s colour, '
    '"product_colour": pick one of them. Only when none of them suits, or "colour_photos" is '
    "empty, pick another photo where the product is clearly seen, in that colour if one shows "
    "it. The product's look and colour come only from the photo picked: never change them or "
    "make one up."
)
SEVERAL_ITEMS = (
    "When the photo picked shows more than one item, such as a pack, a set, a line-up or the "
    "product in several colours, the picture prompt says which one item to take, by where it "
    'is in the photo and how it looks, such as "the round one on the right", in the ad\'s '
    "colour when the photo has it, and the picture shows only that item."
)

STARTING_PICTURE_INSTRUCTIONS = f"""\
You plan the starting picture for one scene of a short vertical video ad. In the scene, the \
person in the portrait holds the product and says the scene's line to camera. The picture \
is made by a picture model from two pictures: the portrait first, then one product photo. \
A video model then animates it to say the line, so it is the scene's first frame.
You are shown the portrait and the product photos you may pick from, each labelled with \
its number. {COLOUR_PICK}
Pick the photo whose view of the product suits the line best: for a line about a detail, \
a photo that shows that detail. {SEVERAL_ITEMS}
Then write the picture model's prompt: use the person from \
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

# What the model writing a B-roll scene's prompts is told: the shared rules, then only the
# rules of the scene's kind, so a rule for one kind can't be used on another
# (docs/broll-picture-logic.md, "Two kinds of scene" and "Prompt rules").

# The rules every B-roll prompt follows, made either way.

# The video prompt rules alone, as the fixing agent is told them: it writes no picture prompt.

# Every B-roll scene: a starting picture is made from the main photo, then animated.


# What a note naming movements may change, when only a B-roll clip is made again from a note:
# the proven rules above come with it, unchanged, but a note naming the movement to film wins
# over the one-movement rule here only (Julian's grade of the real-clip run, Oct 18: "show the
# gel going onto the brush first" was dropped).

# Added to what the model writing a B-roll scene's prompts is told when only its clip is made
# again, from a note (spec #210 point 21): the pictures its last clip was sent stay, and the
# writer is shown them with their labels.


# What the model planning the ad's before-picture is told. One picture of the place the
# B-roll scenes happen in, before any starts, and every B-roll starting picture is drawn from
# it: drawn alone, each scene got its own toilet and its own stains (round 3 #8 s3 FAIL,
# research/scenes-match/README.md). No product or person in it: the product's looks come only
# from its shop photo, and the presenter's from the portrait.
BEFORE_PICTURE_INSTRUCTIONS = """\
You plan the before-picture of a short vertical video ad. The ad's B-roll scenes don't show \
the person talking to camera: each shows what its "shows" describes, while the presenter's \
voice says its line over it. Each B-roll scene's starting picture is drawn from the \
before-picture, so the place, and each thing the scenes act on, look the same in every scene.
Write the picture model's prompt for the before-picture: the place as it is before any \
scene starts.
- It opens: "An upright 9:16 photo in a real, ordinary <place>." with one place every scene \
can happen in.
- Each thing the scenes act on, other than the product, with every part named as a real, \
ordinary one, like one you'd buy in any shop.
- Every problem any scene shows or the product fixes, on those things, as it looks before: \
all of them, even those only one scene names, each said once, with its colour, size, count \
and where it is, left or right.
- Where the camera is, its height and angle, so each of those things is seen whole.
- No product, no person and no hand: each scene's own picture adds them, the product from \
its shop photo and the presenter from the portrait. Nothing happens in it: it shows how \
things look, never an action.
Make nothing up: no thing, problem or detail the scenes don't need. Ask for no added text, \
captions or logos. Use the conversation with the shop owner for their wishes about how the \
ad looks.
Give a one-sentence reason for the prompt, written for the shop owner."""


# What the picture model is ordered to draw for the ad's room alone, from the before-picture:
# the room with nothing of any scene in it (spec #210 point 12; spec 2 item 53, Julian Oct 28
# 22:32), so a clip sent it as its background takes no scene's things or problems from it.
ROOM_ALONE_PROMPT = (
    "Draw the place in this picture again as an upright 9:16 photo, from the same camera and "
    "in the same light. Keep the room itself as it is: its walls, floor, surfaces and "
    "fittings. Take out every other thing, and every mark, stain or mess on what stays, so "
    "nothing of any scene is left. No person, no hand, and no added text, captions or logos."
)

# What the picture model is ordered to draw for the ad's person-in-room picture, from the
# portrait first, then the room alone: the presenter standing in the room and nothing else,
# as an order, not a ban (Julian Oct 28 17:37: "just standing straight in that room. that's
# it."). Untested: the risk that a video model copies the pose is read from the grades.
PERSON_IN_ROOM_PROMPT = (
    "An upright 9:16 photo of the person from the first picture, with the same face, hair "
    "and clothes, in the room from the second picture, from the same camera and in the same "
    "light. The person stands straight and still in the room, hands at their sides, and "
    "nothing else happens. Keep the room exactly as it is, and add no text, captions or "
    "logos."
)


# The fixed lines code puts around the fixing agent's own words for its place_edit (#237): a
# small edit of one scene's room or person-in-room picture, or a new room drawn fresh for it.
# The agent writes the change or the place; code keeps the shape and what goes in no room.
SCENE_PLACE_EDIT_PROMPT = (
    "Draw this picture again as an upright 9:16 photo, from the same camera and in the same "
    "light, with this one change: {edit} Keep everything else exactly as it is, and add no "
    "text, captions or logos."
)
SCENE_NEW_PLACE_PROMPT = (
    "An upright 9:16 photo of this place: {place} No person, no hand, and no added text, "
    "captions or logos."
)


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


class BrollSceneToDraw(Handoff):
    """A B-roll scene, as the model planning the ad's before-picture is handed it."""

    scene: int
    line: str
    shows: str
    broll_kind: str
    usage: str
    result: str


class BeforePictureHandoff(Handoff):
    script: list[str]
    broll_scenes: list[BrollSceneToDraw]
    conversation: list[ChatMessage]


class BeforePicturePlan(BaseModel):
    prompt: str = Field(description="The prompt for the picture model.")
    prompt_reason: str = Field(description="One sentence saying why the prompt asks for that.")

    @field_validator("prompt", "prompt_reason")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


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


def starting_picture_choice_for[Choice: StartingPictureChoice](
    photos: list[int], choice: type[Choice]
) -> type[Choice]:
    """`choice` for a job whose product photos shown to the model are numbered `photos`. A
    photo that isn't one of them fails while the answer is read, like any other broken
    answer. With only one, the model isn't asked which (case C12b): code fills it in."""
    if len(photos) == 1:
        (only,) = photos

        class OnlyPhotoForJob(choice):  # type: ignore[valid-type,misc]
            photo: SkipJsonSchema[int] = only

        return OnlyPhotoForJob

    class StartingPictureChoiceForJob(choice):  # type: ignore[valid-type,misc]
        @model_validator(mode="after")
        def _a_photo_shown(self) -> Self:
            if self.photo not in photos:
                shown = ", ".join(str(number) for number in photos)
                raise ValueError(
                    f"Photo {self.photo} isn't one of the product photos: those are {shown}."
                )
            return self

    return StartingPictureChoiceForJob
