"""Pure scene forms and Creatify prompt assembly. No model calls or stored state."""

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobs.pace import announcer_word, asks_for_a_slow_pace
from jobs.picture_rules import LABEL_RULE, OPEN_RULE, SIDE_RULE

PICTURE_ENDING = (
    "Do not alter the product design. No retouching, no beauty filter, no text on props, no "
    "studio lighting, no ring light, no bokeh."
)
# Code's lines of the video prompt (spec section 5).
SPEED_LINE = "Film this in real-time speed."
# The hook catches the eye only if the clip is already moving when it opens (spec #401).
FIRST_FRAME_LINE = "Movement starts from the very first frame, never a still opening."

NOT_NEEDED = re.compile(r"^\s*not needed( here)?\s*\.?\s*$", re.IGNORECASE)
_DELIVERY_WORDS = re.compile(r"\b(?:ad|energy|precise)\b", re.IGNORECASE)
QUOTED_TEXT = re.compile(r'"[^"]+"|“[^”]+”|(?<!\w)\'.+?\'(?!\w)|(?<!\w)‘.+?’(?!\w)', re.DOTALL)


def has_quoted_text(text: str) -> bool:
    """Quotation pairs hold extra speech; apostrophes within words do not."""
    return QUOTED_TEXT.search(text) is not None


def unsuitable_delivery(delivery: str) -> str | None:
    """The voice or delivery expression that needs rewriting before a talking clip."""
    if word := announcer_word(delivery) or asks_for_a_slow_pace(delivery):
        return word
    found = _DELIVERY_WORDS.search(delivery)
    return found.group() if found else None


ANYTHING_ELSE = (
    "Anything the boxes above don't cover that this picture needs, in plain words; else "
    '"not needed here". Use it only for what no box holds, never to repeat a box. A made-up '
    'example: "steam rises from the cup at the left edge of the frame".'
)


def _box(text: str) -> str:
    """A box's words as they go into the prompt; nothing for one that isn't needed."""
    text = " ".join(text.split())
    if not text or NOT_NEEDED.match(text):
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


class Thing(BaseModel):
    """One thing the clip shows, from start to end."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        description="The thing: the product, one of its parts, what is inside it, a result."
    )
    when: Literal["at the start", "shows up later"] = Field(
        description="Whether it is in the starting picture, or only shows up as the clip goes on."
    )
    looks_from: str = Field(
        description='Where its look comes from: "library item N" for a page photo that shows '
        "it (first choice), else the page's own words, quoted. If nothing on the page shows "
        'it, write "nothing on the page": then it stays out of sight, or the action changes.'
    )


class PictureForm(BaseModel):
    """Draw this scene's starting picture from the picture form below. Code puts each
    picture's job first (the selected product photos, then the shared picture), joins
    your boxes in this order, adds the fixed ending, and draws it at phone size. A separate
    check then answers its questions, and you are shown the picture with its answers.
    Every box is a checklist, not a limit: write "not needed here" where one doesn't apply."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    everything_shown: list[Thing] = Field(
        description="First, before the picture: everything the clip shows from start to end "
        "(the product, its parts, what is inside it, the result), each with where its look "
        "comes from. What is there at the start is drawn visible; what shows up later goes in "
        "the video form. Nothing is guessed: a thing no page photo or page words show stays "
        "out of sight, or the action changes."
    )
    camera: str = Field(
        description="Camera and angle, as one frame of a phone video: who holds the phone or "
        'where it is propped, its height and how far it is. A made-up example: "A vertical '
        'phone video frame, handheld at chest height, looking down at a desk."'
    )
    person_and_room: str = Field(
        description="The person's look, as the shared picture shows them (in a scene with no "
        "face: their hands and forearms only), and the lived-in room, naming 3 to 5 things in "
        "it from the shared picture."
    )
    pose: str = Field(
        description="The pose and the grip, and which side of the product faces the camera "
        f"with the reason. {SIDE_RULE} {OPEN_RULE} Hide printed words the photos don't show "
        "clearly (a hand over them, the plain side to the camera)."
    )
    inside: str = Field(
        description="What is inside the product or in use with it that is visible at the "
        "start, each with its look from a page photo, else the page's words. Nothing is "
        'guessed. "not needed here" when nothing is.'
    )
    size: str = Field(
        description=f"The product's size in the frame. {LABEL_RULE} A made-up example: "
        '"the jar held close to the lens, its label to the camera".'
    )
    moment: str = Field(
        description="The moment of the action the picture freezes. A repeating action starts "
        'mid-action (made-up example: "the paint roller already halfway up the wall, paint '
        'wet behind it"). A single move starts at its start (made-up example: "a hand resting '
        'on the closed lid of a jar, fingers about to twist it"). A scene that shows the '
        'problem shows the problem with no product in it (made-up example: "a tangled cable '
        'on a desk, nobody touching it")."'
    )
    light: str = Field(
        description="Where the light comes from, as in the shared picture, which side is lit "
        "and which side falls into shadow."
    )
    bodies: str = Field(
        description="How many of each body part show, joined to what. A made-up example: "
        '"Exactly two hands, each joined to its arm; one hand holds the jar."'
    )
    anything_else: str = Field(description=ANYTHING_ELSE)


class EditForm(BaseModel):
    """Fix the newest starting picture by changing one thing and keeping the rest: code
    sends it as image 1, then selected product photos with their job lines, as "Edit image 1.
    Keep
    [your list] exactly the same. Change only [your change]." The check answers again and
    you are shown the new picture with its answers. Use it when the check, or your own look,
    names one part that is wrong; draw again when more is wrong."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    anything_else: str = Field(description=ANYTHING_ELSE)
    keep: list[str] = Field(
        description="Everything that stays exactly the same: the person, the room, the light, "
        "the grip, and every part of the product, each named."
    )
    change: str = Field(description="The one change, and what that part becomes.")


class VideoForm(BaseModel):
    """Write the scene's video prompt from the video form below, once the newest starting
    picture has passed the check, with the picture in front of you. Code puts the speed line
    first and the sound line last, joins your boxes in this order, and films the starting
    picture with it. Describe the scene as if it were real: no picture labels, no style
    words. Code supplies any spoken line and voice;
    never write or change spoken words in a box. Describe on-screen text without spelling
    out its words. Keep spoken-style numbers and clock times out of the action description;
    a counted action beat, such as after the third bounce, is allowed.
    In a talking scene call her the woman, never the presenter, in every box.
    About 106 to 150 words in all."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    shot_size: str = Field(
        description='The shot size, after the phone video opener. A made-up example: "close-up".'
    )
    frame_shows: str = Field(
        description="What the frame shows (the person, the room, where the light comes from), "
        "in the picture form's own words."
    )
    product: str = Field(
        description="The product in one phrase: colour, material and shape. "
        "Describe visible printing without spelling out its words."
    )
    camera: str = Field(
        description=f"A camera word, then what the camera does in plain words. {SIDE_RULE}"
        " In a talking scene the phone stays perfectly steady, even in a selfie she holds: "
        "never write sway, shake, wobble, drift or handheld movement; a slow push in is the "
        "only camera move allowed."
    )
    beats: list[str] = Field(
        description="The beats of the scene's one action, in order, filling the whole clip; "
        'code joins them with "then". A repeating action says that it repeats.'
        " In a talking scene she moves like a real person telling a friend: her free hand, "
        "head and shoulders move as she talks. Never tell her to hold still, keep the same "
        "grip, stay anchored or freeze; only the product keeps its shape (the lock line). "
        "Leave the product move to product_move, without repeating it here, and call her "
        "the woman, never the presenter."
        " With an end picture, keep the product on the changing spot, finish the change at "
        "an early counted beat, and say what happens after."
    )
    shows_up_later: str = Field(
        description="What shows up as the clip goes on (contents, a result), in the page's "
        'words; "not needed here" when nothing does.'
        " With an end picture, describe the result as a finished state, backed by a short "
        "'no X', rather than a gradual quality."
    )
    small_life: str = Field(
        description='Small life when a person is in it: blinks, breath, a weight shift. "not '
        'needed here" when no person is.'
    )
    delivery: str = Field(
        default="not needed here",
        description="Talking scene only: how she says the line, in a few casual words matched "
        "to the feeling, the way a person telling a friend sounds. Made-up examples: "
        '"upbeat and casual like telling a friend" for a hook, "amused and a bit fed up", '
        '"surprised and delighted", "warm and pleased". Never ad, announcer, presenter or '
        'energy words, and never "clear", "articulation", "unhurried" or "precise". '
        '"not needed here" in B-roll.',
    )
    feeling: str = Field(
        default="not needed here",
        description="Talking scene only: her face and head moves; not needed here in B-roll.",
    )
    product_move: str = Field(
        default="not needed here",
        description="Talking scene only: one small move with the product, tied to a moment "
        "of the line by its sentence (as she names it, at the end of the first sentence), "
        "then back: she lifts it a little closer to the lens, gives it a small shake, or "
        "tilts it toward the camera. Its photographed side stays to the camera. This is "
        'the scene\'s one action with the product. "not needed here" in B-roll. Never tie '
        "the move to a quoted word.",
    )
    sounds: str = Field(
        default="not needed here",
        description="Talking scene only: one or two quiet, concrete sounds of the room in the "
        "starting picture, or of the action, that a phone would pick up. Made-up examples: "
        '"a fridge humming softly", "traffic far off through a window". '
        "No music, no other voices. Code already writes room tone; do not repeat it. "
        'Write "not needed here" in B-roll.',
    )
    video_for: str = Field(
        default="not needed here",
        description="For a page-video scene, after looking at its frames: what to take from the "
        "video, such as its movement. Otherwise write not needed here.",
    )
    after_last_word: str = Field(
        default="not needed here",
        description="What she does after her last word, so the clip ends on an action. "
        "Describe the movement, never the spoken words. "
        "With an end picture, end in the pose the end picture shows.",
    )
    lock_line: str = Field(
        description='Last: "The [product] keeps its exact shape, colour and printed words." '
        "After a failed clip it grows: every part that must stay as it is."
    )
    anything_else: str = Field(
        description="Anything the boxes above don't cover that this clip needs; else \"not "
        'needed here". A made-up example: "the curtain behind her sways a little in the breeze".'
    )


def picture_prompt(form: PictureForm, picture_jobs: Sequence[str]) -> str:
    """Jobs are already labelled in the same order as the pictures being sent."""
    boxes = (
        form.camera,
        form.person_and_room,
        form.pose,
        form.inside,
        form.size,
        form.moment,
        form.light,
        form.bodies,
        form.anything_else,
    )
    return " ".join(
        [*filter(None, map(_box, picture_jobs)), *filter(None, map(_box, boxes)), PICTURE_ENDING]
    )


def edit_prompt(form: EditForm, picture_jobs: Sequence[str]) -> str:
    """Image 1 is the picture being edited; reference jobs start with Image 2."""
    keep = "; ".join(
        item.strip().rstrip(".")
        for item in form.keep
        if item.strip() and not NOT_NEEDED.match(item)
    )
    return " ".join(
        filter(
            None,
            [
                f"Edit image 1. Keep {keep} exactly the same." if keep else "Edit image 1.",
                f"Change only this: {_box(form.change)}" if _box(form.change) else "",
                "Keep everything else as it is in image 1.",
                *map(_box, picture_jobs),
                _box(form.anything_else),
                "Do not alter the product design. No text.",
            ],
        )
    )


def video_prompt(
    form: VideoForm,
    *,
    reference_mode: bool = False,
    end_picture: bool = False,
    line: str,
    voice_words: str = "",
    kind: str = "broll",
    hook: bool = False,
) -> str:
    """The video prompt as it is sent: code's lines around the agent's boxes. Talking
    delivery fits each line; B-roll keeps its off-camera voice wording."""
    cleaned = [
        re.sub(r"^(and )?then\s+", "", beat.strip().rstrip("."), flags=re.IGNORECASE)
        for beat in form.beats
        if beat.strip() and not NOT_NEEDED.match(beat)
    ]
    # Each beat after the first goes on the sentence: "..., then the hand lifts it".
    beats = ", then ".join([*cleaned[:1], *(beat[:1].lower() + beat[1:] for beat in cleaned[1:])])
    shot = " ".join(form.shot_size.split())
    if NOT_NEEDED.match(shot):
        shot = ""
    after_last_word = ""
    energy = ", in a high-energy, excited delivery" if hook else ""
    talking = []
    opener = "Live-action vertical phone footage, handheld UGC"
    steady_phone = ""
    if kind == "talking":
        opener = "Live-action vertical phone footage, UGC, filmed on a steady phone"
        steady_phone = "The phone stays perfectly steady the whole time: no sway, no shake."
        delivery = _box(form.delivery).rstrip(".")
        delivery = delivery[:1].lower() + delivery[1:]
        sounds = _box(form.sounds).rstrip(".")
        sounds = re.sub(r"^room tone\b[\s,.;:]*", "", sounds, flags=re.IGNORECASE)
        sounds = sounds[:1].lower() + sounds[1:]
        talking = [
            f"She talks straight to the lens{', ' + delivery if delivery else ''}, and says "
            f"with {voice_words.strip().rstrip('.')}: <d>[English] {line}</d> "
            "Her mouth moves in sync with every word.",
            _box(form.product_move),
            _box(form.feeling),
        ]
        sound = (
            f"Sound: room tone{', ' + sounds if sounds else ''}, her clear close voice. No music."
        )
    else:
        sound = (
            f"Off camera, the presenter says with {voice_words.strip().rstrip('.')}{energy}: "
            f"<d>[English] {line}</d> "
            "Sound: room tone, the action's sounds and the presenter's voice. No music."
        )
    action = form.after_last_word.strip()
    if _box(action):
        after_last_word = _box("After the last word, " + action[:1].lower() + action[1:])
    parts = [
        SPEED_LINE,
        FIRST_FRAME_LINE,
        _box(opener + (f", {shot}" if shot else "")),
        _box(form.frame_shows),
        _box(form.product),
        _box(form.camera),
        steady_phone,
        _box(beats),
        _box(form.shows_up_later),
        _box(form.small_life),
        _box(form.anything_else),
        *talking,
        after_last_word,
        _box(form.video_for) if reference_mode else "",
        "The clip starts exactly like Image 1." if reference_mode else "",
        "Image 1 is the first frame and Image 2 is the last frame."
        if reference_mode and end_picture
        else "",
        _box(form.lock_line),
        sound,
    ]
    return " ".join(filter(None, parts))
