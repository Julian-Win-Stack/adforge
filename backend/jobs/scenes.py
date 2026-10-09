"""What the model that plans a scene's starting picture is told, handed and answers."""

from typing import Any, Self, cast

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
in the finished ad. Ask for no added text, captions or logos.
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

# What the model writing a B-roll scene's prompts is told: the shared rules, then only the
# rules of the scene's kind, so a rule for one kind can't be used on another
# (docs/broll-picture-logic.md, "Two kinds of scene" and "Prompt rules").
_BROLL_SCENE = """\
You write the prompts for one B-roll scene of a short vertical video ad. The scene doesn't \
show the person talking to camera: it shows what "shows" describes, while the presenter's \
voice says the scene's line over it.
"""

# The rules every B-roll prompt follows, made either way.
_BROLL_PROMPT_RULES = """\
- One continuous shot, with no cuts, at a natural, real-time pace. Never write seconds or \
timings, or words that slow it down, such as "slowly" or "gently".
- Film one movement only: one hand or tool doing one thing, such as one wipe, one pour or \
one pass. "shows" says which: when it names one movement, film that one, even when the line, \
"usage" or "result" name another, such as a step before it. When "shows" lists more than \
one, film the one where the product, or the tool used with it, does the work, not a step \
before it, such as putting the product on, or after it, such as rinsing. The voice carries \
the rest. A part that "shows" or "usage" says happens while the movement is done, such as \
pressing down while turning a cap, is part of that one movement: film it during the \
movement, never before it or in the starting picture. \
Never film the fiddly change between two states, such as clipping, unclipping or folding: \
open with it done.
- The product, or the tool used with it, does what "shows" describes, on screen, held the way \
"usage" says; "usage" tells how it is held and used, not how many steps to film. Something \
always acts. The product may stand in view, label to the camera, \
when holding it would bend its shape, while the tool used with it does the work.
- The side of the product facing the camera in the starting picture faces it at the end: \
never turn, flip or spin the product, and after a drop it lands that side up. Only a turn \
"usage" says is part of using it is filmed.
- The main action, or the product, is in the middle of the frame. Say nothing about the \
top or the bottom of the frame.
- Only the presenter is shown. When "person_shown" is "has face", the person is the \
presenter from the portrait, with the same face, hair and clothes. When it is "no face", no \
face is seen. Any hand is fine. When "shows" names a person or a hand, they are in the \
clip, doing what it says.
- The product is shown, not described: the pictures show how it looks, so don't describe \
its shape, colours or brand name in words.
- Never write "no speech", "no sound" or "no text".
- Never ask for two things that can't both be true at once, of the product or the scene, \
such as "upright" and "nozzle pointing down", or "squeeze" and "no gel". Check every order \
against the product photos and against your other orders.
- Code starts the video prompt with the clip's length and says the camera stays still, so \
write neither, and never move the camera or say where or how close it ends.
The producer may add a note, such as what the shop owner asked for this scene. Follow it \
unless it asks for something you can't do with these pictures, or something "shows" doesn't \
describe, and then say so in a reason.
Use the conversation with the shop owner for their wishes about how the ad looks. Facts \
about the product come only from what you are shown.
"""

# Every B-roll scene: a starting picture is made from the main photo, then animated.
BROLL_SHARED_RULES = f"""\
{_BROLL_SCENE}\
A picture model makes the scene's starting picture from the pictures in "pictures", in that \
order, each named by its number ("Image 1") and used only for its job. A video model then \
animates the picture into one clip, and the clip's sound is replaced by the voice.
You are shown the product photos you may pick from, each labelled with its number, then a \
photo of what the scene needs that they can't show, if it needs anything, and the presenter's \
portrait when the scene shows their face. Every photo in "colour_photos" shows the product in \
the ad's colour. Pick the one whose view of the product suits the scene best: it is Image 1.
Then write the picture model's prompt and the video model's prompt, following these rules:
- Take only the product from Image 1, exactly as it looks, with any label or print \
unchanged. Nothing else from that photo: not its background, setting or people. The setting \
is the ad's choice: the setting is described in words.
- The starting picture is the "before": the moment just before the action, with the \
product ready to be used. The video prompt does the action.
- The picture prompt opens: "An upright 9:16 photo in a real, ordinary <place>." with the \
scene's setting as the place.
- Anything that must be right goes in the starting picture, said plainly: say where the \
camera is, its height and angle; each object the \
action happens to, other than the product, with every part named as a real, ordinary one, \
like one you'd buy in any shop; the problem the product fixes, as it looks before; exact \
counts, and left or right; and any hand already in place for the action, holding what it \
uses.
- When the product is in an unusual pose, such as upside down, say the pose once and what \
it does to the product's shape and to each part you see, such as its label turning with \
it, even though the product is otherwise not described.
{_BROLL_PROMPT_RULES}\
Give a one-sentence reason for the photo, one for the picture prompt and one for the video \
prompt, written for the shop owner."""

BROLL_KIND_RULES: dict[str, str] = {
    "does a job": """\
This scene does a job you can see: the result in "result" comes from the product being \
used. The starting picture shows what the result will change, before it changes. The video \
prompt ends on the result, happening because the product, or the tool used with it, acts, \
not by itself. The result shows only where the product, or the tool used with it, touches, \
and nothing else changes. It happens as the touch passes, at a real-time pace: never all at \
once, sped up or time-compressed. When a tool does the work after the product is put on, \
such as a brush after a gel, putting the product on is never filmed or drawn: the voice \
carries it. A result on a screen is shown without numbers or words, such as a charging \
light coming on.""",
    "shows the problem": """\
This scene shows the problem the product fixes, before the product is used: plainly, in \
the starting picture, with the product in view. Nothing about the problem changes in the \
clip. The one movement is a hand or the product, such as the product set down beside it.""",
    "showcase": """\
This scene is a showcase: the real product shown clearly, in use the way the page says, with \
one simple action a person really does with it, such as worn while walking, picked up or set \
down. The video prompt ends on the moment that shows what "shows" describes, done by a \
hand or the product. Show no result or change the page doesn't prove.""",
}


def broll_prompt_instructions(broll_kind: str) -> str:
    """What the model writing the prompts of a B-roll scene of `broll_kind` is told."""
    return f"{BROLL_SHARED_RULES}\n{BROLL_KIND_RULES[broll_kind]}"


# The job of each picture the picture model gets for a B-roll scene's starting picture.
MAIN_PHOTO_JOB = "the product, only how it looks"
PORTRAIT_JOB = "the presenter"


def broll_video_prompt(prompt: str, seconds: int) -> str:
    """A B-roll scene's video prompt as it is sent: the clip's real length and a still camera
    first, so no prompt goes without them, then the prompt as the model wrote it. The one
    movement is a hand or the product, never the camera (test ads, 08 Oct); the look is the
    video model's (docs/broll-picture-logic.md, "Prompt rules")."""
    return f"A {seconds}-second video at real-time speed. The camera stays still. {prompt}"


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


class PictureJob(Handoff):
    image: int
    job: str


class BrollPromptHandoff(Handoff):
    scene: int
    line: str
    script: list[str]
    shows: str
    broll_kind: str
    person_shown: str
    usage: str
    result: str
    pictures: list[PictureJob]
    product_colour: str
    colour_photos: list[int]
    person_looks: str
    note: str | None
    conversation: list[ChatMessage]


class NeedPhoto(Handoff):
    """The photo sent to the picture model for what a B-roll scene needs that the main photo
    can't show: for one need, or for several that the same photo shows."""

    what: list[str]
    photo: int
    has_face: bool


def photos_for_needs(
    needs: list[dict[str, Any]], faces: dict[int, bool], most: int
) -> list[NeedPhoto]:
    """The photo sent for each of a scene's `needs`, given which photos the job has and
    whether each shows a stranger's face (`faces`): the first of the need's photos without
    a face, otherwise its first (docs/broll-picture-logic.md, item 28). A photo is sent once,
    for every need it shows, and at most `most` are sent: the needs' last are left out."""
    sent: dict[int, list[str]] = {}
    for need in needs:
        photos = [number for number in need["photos"] if number in faces]
        if photos:
            photo = next((number for number in photos if not faces[number]), photos[0])
            sent.setdefault(photo, []).append(need["what"])
    return [
        NeedPhoto(what=what, photo=photo, has_face=faces[photo]) for photo, what in sent.items()
    ][:most]


# At most this many photos of what a B-roll scene needs go to the picture model, after the
# main photo: with the portrait, five pictures in all, as the video model was once sent.
MOST_NEEDS_PHOTOS = 3


def need_job(need: NeedPhoto) -> str:
    """The job of the photo sent for what a B-roll scene needs: only how those things look,
    with any stranger in it ignored, as the presenter is the only person shown."""
    job = f"only how {' and '.join(need.what)} looks"
    return f"{job}; ignore the person in it" if need.has_face else job


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
