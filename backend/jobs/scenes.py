"""What the model that plans a scene's starting picture is told, handed and answers."""

from dataclasses import dataclass
from typing import Any, Self, cast

from pydantic import BaseModel, Field, create_model, field_validator, model_validator

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
- Film one movement only, whatever "shows", "usage" or the line list: one hand or tool \
doing one thing, such as one wipe, one pour or one pass. Keep the movement that makes the \
line's claim happen on screen, not a step before or after it; when "shows" names only one \
movement, film that one. The voice carries the rest. \
Never film the fiddly change between two states, such as clipping, unclipping or folding: \
open with it done.
- The product, or the tool used with it, does what the line claims, on screen, held the way \
"usage" says; "usage" tells how it is held and used, not how many steps to film. Something \
always acts. The product may stand in view, label to the camera, \
when holding it would bend its shape, while the tool used with it does the work.
- The main action, or the product, is in the middle of the frame. Say nothing about the \
top or the bottom of the frame.
- Only the presenter is shown. When "person_shown" is "has face", the person is the \
presenter from the portrait, with the same face, hair and clothes. When it is "no face", no \
face is seen. Any hand is fine.
- The product is shown, not described: the pictures show how it looks, so don't describe \
its shape, colours or brand name in words.
- Never write "no speech", "no sound" or "no text".
- Never ask for two things that can't both be true at once, of the product or the scene, \
such as "upright" and "nozzle pointing down", or "squeeze" and "no gel". Check every order \
against the product photos and against your other orders.
- Code starts the video prompt with the clip's length and its phone-video look, so write \
neither.
The producer may add a note, such as what the shop owner asked for this scene. Follow it \
unless it asks for something you can't do with these pictures, or something "shows" doesn't \
describe, and then say so in a reason.
Use the conversation with the shop owner for their wishes about how the ad looks. Facts \
about the product come only from what you are shown.
"""

# Way 1: a starting picture is made from the main photo, then animated.
BROLL_SHARED_RULES = f"""\
{_BROLL_SCENE}\
A picture model makes the scene's starting picture from the pictures in "pictures", in that \
order, each named by its number ("Image 1") and used only for its job. A video model then \
animates the picture into one clip, and the clip's sound is replaced by the voice.
You are shown the product photos you may pick from, each labelled with its number, and the \
presenter's portrait when the scene shows their face. Every product photo shows the product \
in the ad's colour. Pick the photo whose view of the product suits the scene best: it is \
Image 1.
Then write the picture model's prompt and the video model's prompt, following these rules:
- Take only the product from Image 1, exactly as it looks, with any label or print \
unchanged. Nothing else from that photo: not its background, setting or people. The setting \
is the ad's choice: the setting is described in words.
- The starting picture is the "before": the moment just before the action, with the \
product ready to be used. The video prompt does the action.
- The picture prompt opens: "An upright 9:16 photo taken on a phone in a real, ordinary \
<place>, casual, not a studio shot." with the scene's setting as the place.
- Anything that must be right goes in the starting picture, said plainly: the camera is a \
phone held by a person, so say where they hold it, its height and angle; each object the \
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

# Way 3: the video model gets real shop photos as example pictures, and no picture is made
# (docs/broll-picture-logic.md, items 27, 28 and 55).
BROLL_WAY_3_RULES = f"""\
{_BROLL_SCENE}\
No picture is made for this scene. The video model makes the clip from example pictures: \
real shop photos, and the presenter's portrait when the scene shows their face. They show it \
how things look; none of them is a frame of the clip. The clip's sound is replaced by the \
voice.
You are shown the product photos you may pick the main photo from, each labelled with its \
number, then the photos of what the scene needs that the main photo can't show ("needs"), \
then the portrait when "portrait" is true. Every photo in "colour_photos" shows the product \
in the ad's colour. Pick the one whose view of the product suits the scene best: it is the \
main photo, Image 1.
The pictures are sent in this order, each named by its number: Image 1, the main photo; then \
the photo of each of "needs", unless it is the main photo, which isn't sent twice; then the \
portrait. Give each picture sent its one job in its own slot, "image_1" for Image 1 and so \
on, such as "the bottle; keep its label exactly" or "only the gel's colour". Leave the slots \
after the last picture sent empty. For a photo with a stranger's face ("has_face"), also say \
what in it to ignore, such as "the woman"; the presenter is the only person shown. Code \
writes "Image 1 is" before each job, so don't number them yourself.
Then write the action: the one shot the clip shows, from its first moment, following these \
rules:
- Take only the product from Image 1, exactly as it looks, with any label or print \
unchanged. Nothing else from that photo: not its background, setting or people. The setting \
is the ad's choice: the setting is described in words. Take from each other picture only its \
job.
- The clip opens on the "before": the moment just before the action, with the product ready \
to be used. Where the rules below speak of the starting picture, they mean that first \
moment. The action then happens.
{_BROLL_PROMPT_RULES}\
Give a one-sentence reason for the main photo and one for the action, written for the shop \
owner."""

BROLL_KIND_RULES: dict[str, str] = {
    "does a job": """\
This scene does a job you can see: the result in "result" comes from the product being \
used. The starting picture shows what the result will change, before it changes. The video \
prompt ends on the result, happening because the product, or the tool used with it, acts, \
not by itself. A result on a screen is shown without numbers or words, such as a charging \
light coming on.""",
    "showcase": """\
This scene is a showcase: the real product shown clearly, in use the way the page says, with \
one simple action a person really does with it, such as worn while walking, picked up or set \
down. The video prompt ends on what the line proves, filmed: the moment that shows its \
claim is true. Show no result or change the page doesn't prove.""",
}


def broll_prompt_instructions(broll_kind: str, way: int = 1) -> str:
    """What the model writing the prompts of a B-roll scene of `broll_kind`, made `way`, is
    told."""
    shared = BROLL_WAY_3_RULES if way == 3 else BROLL_SHARED_RULES
    return f"{shared}\n{BROLL_KIND_RULES[broll_kind]}"


# The job of each picture the picture model gets for a way 1 B-roll scene.
MAIN_PHOTO_JOB = "the product, only how it looks"
PORTRAIT_JOB = "the presenter"


def broll_video_prompt(prompt: str, seconds: int) -> str:
    """A B-roll scene's video prompt as it is sent: the clip's look and real length first,
    so no prompt goes without them, then the prompt as the model wrote it. Both clips Julian
    graded best opened this way (docs/broll-picture-logic.md, "Prompt rules")."""
    return (
        f"A {seconds}-second handheld phone video, casual, not cinematic, real-time speed. {prompt}"
    )


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
    """The photo sent for what a way 3 B-roll scene needs that the main photo can't show:
    for one need, or for several that the same photo shows."""

    what: list[str]
    photo: int
    has_face: bool


class BrollExamplesHandoff(Handoff):
    scene: int
    line: str
    script: list[str]
    shows: str
    broll_kind: str
    person_shown: str
    usage: str
    result: str
    needs: list[NeedPhoto]
    portrait: bool
    product_colour: str
    colour_photos: list[int]
    person_looks: str
    note: str | None
    conversation: list[ChatMessage]


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


@dataclass(frozen=True)
class ExamplePicture:
    """One example picture sent to the video model: a product photo by its number, or the
    presenter's portrait."""

    photo: int | None
    has_face: bool = False

    @property
    def portrait(self) -> bool:
        return self.photo is None


def example_pictures(
    main: ExamplePicture, needs: list[NeedPhoto], portrait: bool
) -> list[ExamplePicture]:
    """The example pictures a way 3 B-roll scene sends, in order: its main photo, then the
    photo of each of its needs unless it is the main photo, which isn't sent twice, then the
    presenter's portrait when the scene shows their face."""
    return [
        main,
        *(ExamplePicture(need.photo, need.has_face) for need in needs if need.photo != main.photo),
        *([ExamplePicture(None)] if portrait else []),
    ]


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


def _not_empty(text: str) -> str:
    """`text`, unless there is nothing in it: then it fails while the answer is read."""
    if not text.strip():
        raise ValueError("This can't be empty.")
    return text


class PictureSlot(BaseModel):
    """What one example picture is for, as the video prompt says it."""

    job: str = Field(
        description='This picture\'s one job, such as "the bottle; keep its label exactly" or '
        '"only the gel\'s colour". Without "Image N is": code writes that.'
    )
    ignore: str | None = Field(
        description="For a photo with a stranger's face, what in it to ignore, such as "
        '"the woman". Null otherwise.'
    )

    @field_validator("job")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        return _not_empty(text)

    def said(self) -> str:
        """The job as the video prompt says it, after "Image N is"."""
        job = self.job.strip().rstrip(".")
        ignore = (self.ignore or "").strip().rstrip(".")
        return f"{job}; ignore {ignore}" if ignore else job


class BrollExamplesChoice(BaseModel):
    """The answer for a way 3 B-roll scene: the main photo, a slot per example picture sent
    (`image_1` ... `image_N`, added for each scene by broll_examples_choice_for), and the
    action."""

    photo: int = Field(description="The number of the main photo: Image 1.")
    photo_reason: str = Field(description="One sentence saying why that photo suits the scene.")

    def slots(self) -> list[PictureSlot]:
        """Each picture's slot, Image 1 first, up to the last one filled."""
        slots: list[PictureSlot | None] = []
        while (slot := getattr(self, f"image_{len(slots) + 1}", None)) is not None:
            slots.append(slot)
        return cast(list[PictureSlot], slots)

    def video_prompt(self) -> str:
        """The video model's prompt: each picture named as "Image N" with its job, then the
        action (docs/broll-picture-logic.md, item 55)."""
        named = [f"Image {n} is {slot.said()}." for n, slot in enumerate(self.slots(), start=1)]
        return " ".join([*named, cast(str, self.__dict__["action"]).strip()])

    def action_reason(self) -> str:
        """Why the action is shown that way."""
        return cast(str, self.__dict__["prompt_reason"])


def broll_examples_choice_for(
    colour_photos: dict[int, bool], needs: list[NeedPhoto], portrait: bool
) -> type[BrollExamplesChoice]:
    """The answer for a way 3 B-roll scene whose main photo is one of `colour_photos`, each
    with whether it shows a stranger's face, with
    `needs`' photos and, if `portrait`, the portrait: one slot per picture sent. Which are
    sent can depend on the main photo picked (a need's photo that is the main photo isn't
    sent twice), so a slot only some picks send may be null. One missing, null for a picture
    sent, given for a picture not sent, or saying nothing to ignore in a photo with a face
    fails while the answer is read, like any other broken answer."""

    def sent_with(main: int) -> list[ExamplePicture]:
        return example_pictures(ExamplePicture(main, colour_photos[main]), needs, portrait)

    # With no photo to pick, any pick is refused while the answer is read.
    counts = [len(sent_with(main)) for main in colour_photos] or [1]
    least, most = min(counts), max(counts)

    class Checked(BrollExamplesChoice):
        @field_validator("photo_reason", "action", "prompt_reason", check_fields=False)
        @classmethod
        def _not_empty(cls, text: str) -> str:
            return _not_empty(text)

        @model_validator(mode="after")
        def _one_slot_per_picture_sent(self) -> Self:
            if self.photo not in colour_photos:
                shown = ", ".join(str(number) for number in colour_photos)
                raise ValueError(
                    f"Photo {self.photo} isn't one showing the product in the ad's colour: "
                    f"those are {shown}."
                )
            sent = sent_with(self.photo)
            for n in range(1, most + 1):
                slot = getattr(self, f"image_{n}")
                if n > len(sent) and slot is not None:
                    raise ValueError(f"image_{n} must be null: {len(sent)} pictures are sent.")
                if n <= len(sent) and slot is None:
                    raise ValueError(
                        f"image_{n} is null, but {len(sent)} pictures are sent: give Image "
                        f"{n}'s job."
                    )
                if n <= len(sent) and sent[n - 1].has_face and not (slot.ignore or "").strip():
                    raise ValueError(
                        f"Image {n} shows a stranger's face: image_{n} must say what in it "
                        "to ignore."
                    )
            return self

    slots: dict[str, Any] = {
        f"image_{n}": (
            PictureSlot if n <= least else PictureSlot | None,
            Field(
                description=f"Image {n}'s job"
                + ("." if n <= least else ", or null when fewer pictures are sent.")
            ),
        )
        for n in range(1, most + 1)
    }
    return create_model(
        "BrollExamplesChoiceForScene",
        __base__=Checked,
        **slots,
        action=(
            str,
            Field(description="The shot the clip shows, after the pictures are named."),
        ),
        prompt_reason=(
            str,
            Field(description="One sentence saying why the action is shown that way."),
        ),
    )
