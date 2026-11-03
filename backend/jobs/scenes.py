"""What the model that plans a scene's starting picture is told, handed and answers."""

from typing import cast

from pydantic import BaseModel, Field, field_validator

from gateway.types import Handoff

from .planning import ChatMessage, ProductSize

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
