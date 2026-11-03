"""PROTOTYPE, never merged (handoffs/start-frame-design/spec.md, Julian's "go" Nov 3 02:05): the
B-roll scene agent. One agent owns one B-roll scene: it draws the scene's starting picture,
gets a separate picture check's two answers, fixes a picture that fails, and writes the
video prompt with the picture in view. It runs on our own loop (`loop.take_a_turn`,
`loop.settle`), as the fixing agent does, but from a scene's saved inputs (a test scene's
folder), not from an ad in the app: none of the app is wired to it.

Stage 1 (stills and prompts): `film` keeps the video prompt and ends the run, filming
nothing. Stage 2 (video) waits on two calls of Julian's (spec Undecided 2 and 3)."""

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from django.db.models import Sum
from pydantic import BaseModel, ConfigDict, Field

from adforge import file_store
from chat.models import Session
from gateway.gateway import call_model, edit_picture
from gateway.models import ModelCall
from gateway.types import Handoff, Happened, Image, Said, Shown, ToolUse
from jobs import page
from jobs.checks import FACT_CHECK_INSTRUCTIONS, FactCheckHandoff, LineToCheck, fact_check_for
from jobs.work import _broll_clip_seconds

from . import loop
from .loop import Failed, Refused, Tool, ToolResult
from .models import ToolCall

AGENT = "scene_agent"
PURPOSE = "scene_agent"
DRAW = "draw_broll_picture"
CHECK = "check_broll_picture"

# Caps per run (spec section 3, Julian Nov 3 00:30). The agent is never told them, as the
# fixing agent never is: so it never quits early.
MOST_PICTURES_PER_RUN = 6
MOST_CLIPS_PER_RUN = 6
# The newest pictures the agent is shown on a turn; the shared picture is always kept.
MOST_SHOWN = 50
# The most no-tool turns in a row before code ends the run.
MOST_IDLE_TURNS = 3
# The picture model takes at most 16 pictures; the shared picture takes one.
MOST_PHOTOS = 15

# ---------------------------------------------------------------- the inputs


@dataclass(frozen=True)
class LibraryItem:
    number: int
    label: str
    key: str
    photo: bool


@dataclass(frozen=True)
class Moment:
    video: int
    moment: int
    start: float
    end: float
    happens: str
    on_screen: str
    key: str | None


@dataclass
class Inputs:
    """One test scene's saved inputs (test-scenes/<slug>/scene.json), files in the store."""

    slug: str
    product_url: str
    product_name: str
    product_colour: str
    page_text: str
    product_behaviour: str
    plan: list[dict[str, Any]]
    scene: int
    line_seconds: float
    library: list[LibraryItem]
    moments: list[Moment]
    room: str | None
    person_in_room: str | None
    portrait: str | None = None
    owner_chat: str = ""

    def row(self) -> dict[str, Any]:
        return next(each for each in self.plan if each["number"] == self.scene)


def load(folder: Path) -> Inputs:
    """A test scene's inputs, its files saved in the file store once."""
    told = json.loads((folder / "scene.json").read_text())

    def stored(name: str | None) -> str | None:
        if not name:
            return None
        path = folder / name
        if not path.exists():
            return None
        return file_store.save(f"proto_{told['slug']}_{path.name}", path.read_bytes())

    library = []
    for item in told["library"]:
        key = stored(item.get("file"))
        if key is not None:
            library.append(
                LibraryItem(
                    item["number"],
                    item["label"] or "a product photo from the page",
                    key,
                    bool(item.get("photo", True)),
                )
            )
    moments = [
        Moment(
            m["video"],
            m["moment"],
            float(m["start"]),
            float(m["end"]),
            m.get("happens", ""),
            m.get("on_screen", ""),
            stored(m.get("file")),
        )
        for m in told.get("moments") or []
    ]
    return Inputs(
        slug=told["slug"],
        product_url=told["product_url"],
        product_name=told["product_name"],
        product_colour=told.get("product_colour") or "",
        page_text=told["page_text"],
        product_behaviour=told.get("product_behaviour") or "",
        plan=[dict(each) for each in told["plan"]],
        scene=int(told["scene"]),
        line_seconds=float(told["line_seconds"]),
        library=library,
        moments=moments,
        room=stored(told.get("room")),
        person_in_room=stored(told.get("person_in_room")),
        portrait=stored(told.get("portrait")),
        # The prototype gives the agent no owner words (#298); the check gets none either.
        owner_chat="",
    )


# ---------------------------------------------------------------- what a run keeps


@dataclass
class Picture:
    number: int
    key: str
    how: Literal["drawn", "edited"]
    prompt: str
    sent: list[str]
    product_same: dict[str, Any]
    physics: dict[str, Any]
    form: dict[str, Any]

    @property
    def passed(self) -> bool:
        return bool(self.product_same.get("passes")) and bool(self.physics.get("passes"))


@dataclass
class Run:
    inputs: Inputs
    session: Session
    out: Path
    conversation: list[Happened] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    scene_changes: list[dict[str, Any]] = field(default_factory=list)
    video_prompt: str = ""
    video_form: dict[str, Any] = field(default_factory=dict)
    ended: str = ""
    # change_scene calls that kept the line while the action changed, already told the line.
    told_the_line: set[str] = field(default_factory=set)

    @property
    def current(self) -> Picture | None:
        return self.pictures[-1] if self.pictures else None

    def shared(self) -> list[tuple[str, str]]:
        """The shared pictures this scene is drawn with, each with what it is: the person in
        the room for a scene with a face (the presenter and the room apart, for an ad that
        drew them apart), the room alone for one without."""
        inputs = self.inputs
        face = inputs.row().get("person_shown") == "has face"
        if face and inputs.person_in_room:
            return [(inputs.person_in_room, "person_in_room")]
        if face and inputs.portrait:
            return [
                (inputs.portrait, "portrait"),
                *([(inputs.room, "room")] if inputs.room else []),
            ]
        if inputs.room:
            return [(inputs.room, "room")]
        return [(inputs.person_in_room, "person_in_room")] if inputs.person_in_room else []


RUNS: dict[str, Run] = {}


def _run_of(call: ToolCall) -> Run:
    return RUNS[str(call.session_id)]


# ---------------------------------------------------------------- the instructions

INSTRUCTIONS = """\
You make one B-roll scene of a short vertical video ad, and you own it until it is made. In a \
B-roll scene the presenter's voice says the scene's line while the clip shows the product \
doing something. You draw the scene's starting picture, look at it, and then write its video \
prompt with the picture in front of you. A video model animates that one picture into the \
clip: it is sent the starting picture and your prompt and nothing else, so everything the clip \
needs is in the picture or in your words.

The ad's person and room were drawn once before you started, and every scene of the ad is \
drawn with them, so the scenes look like one video: that shared picture is shown to you first \
and stays in front of you. A scene with the presenter's face is drawn with the person in the \
room; a scene with no face, with the room alone. Code sends it with your picture, and every \
product photo in the library too, each with its job line: you never list them yourself.

How you work, in no fixed order but this one is usual:
1. Read the scene, the plan and the page. Open the library photos you need with \
look_at_library to see how the product really looks, its parts and what is inside it: what \
you open stays in front of you. When the page has a video of the product being used, open \
the moments that show this scene's action too: the video model is never sent the page video, \
so how the product really moves reaches the clip only through your picture and your words.
2. Fill the picture form in draw_starting_picture. Each box's description says how; a box \
that doesn't apply says "not needed here".
3. Each picture comes back with a separate check's two answers: whether the product, with \
what is inside it, looks the same as in its photos, and whether anything in the picture breaks \
physics. Look at the picture yourself as well. A picture the check fails is always fixed, \
never let go. You choose how, from the answers: edit_starting_picture changes only the part \
that is wrong and keeps the rest; draw_starting_picture draws it again; or, when the action \
itself can't be drawn right, change_scene changes the scene, then you draw again.
4. Once the newest picture passes, fill the video form in film, describing the scene as if \
it were real, in the same words your picture form used for the person, the room, the light \
and the product.
When, after trying, you judge the scene can't be made well, call give_up with your reason.

What you know about the video model, from the clips this app made:
- It follows orders that say what happens and ignores orders that say what must not happen. \
Write every order in the video prompt as what happens, what stays where it is, and how it \
ends.
- An action that is over in a moment, stretched over a clip of 5 seconds or more, fails: the \
model fills the spare seconds with things that never happen in life. Pick an action that \
keeps going by its own course for the whole clip, or write its beats so each second has its \
real part.
- The starting picture is an order the clip follows: what the picture shows is where the \
clip starts. A product photo's pose gets copied, so photos are for how the product looks \
only; the pose comes from your words.

Rules you keep, always (proven by the shop owner's grades of earlier ads):
- B-roll must sell: every clip does something with the product. Holding or placing it \
fails, unless holding is the claim. Finishing steps, such as putting it away or wiping up, \
get no clip.
- One action per clip. A claim with two states (before and after) is two one-action clips \
joined by a cut, so this scene films one of them.
- Only real-time speed, never slower.
- Never turn the product to a side no photo shows.
- A result shows only where the product touched.
- In a scene that shows the problem, nobody touches the product, and it is not in the scene.
- The line stays a true claim from the page. What the page says about how the product is \
used comes first; where it says nothing, go by common sense about how such a product is \
really used.
"""


# ---------------------------------------------------------------- the forms

# Creatify's fixed ending of a frame prompt (creatify-prompt-pattern.md section 2, row 9).
PICTURE_ENDING = (
    "Do not alter the product design. No retouching, no beauty filter, no text on props, no "
    "studio lighting, no ring light, no bokeh."
)
# Code's lines of the video prompt (spec section 5).
SPEED_LINE = "Film this in real-time speed."
SILENT_LINE = "No speech, murmurs, or vocal sounds."

NOT_NEEDED = re.compile(r"^\s*not needed( here)?\s*\.?\s*$", re.IGNORECASE)

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


class DrawStartingPicture(Tool):
    """Draw this scene's starting picture from the picture form below. Code puts each
    picture's job first (every product photo in the library, then the shared picture), joins
    your boxes in this order, adds the fixed ending, and draws it at phone size. A separate
    check then answers its two questions, and you are shown the picture with its answers.
    Every box is a checklist, not a limit: write "not needed here" where one doesn't apply."""

    name: ClassVar[str] = "draw_starting_picture"
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
        "with the reason: only a side a product photo shows. Hide printed words the photos "
        "don't show clearly (a hand over them, the plain side to the camera)."
    )
    inside: str = Field(
        description="What is inside the product or in use with it that is visible at the "
        "start, each with its look from a page photo, else the page's words. Nothing is "
        'guessed. "not needed here" when nothing is.'
    )
    size: str = Field(
        description="The product's size in the frame. A made-up example: \"the jar fills about "
        'a quarter of the frame width, in the lower third".'
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

    def run(self, call: ToolCall) -> ToolResult:
        return _draw(_run_of(call), self)


class EditStartingPicture(Tool):
    """Fix the newest starting picture by changing one thing and keeping the rest: code
    sends it as image 1, then every product photo with its job line, as "Edit image 1. Keep
    [your list] exactly the same. Change only [your change]." The check answers again and
    you are shown the new picture with its answers. Use it when the check, or your own look,
    names one part that is wrong; draw again when more is wrong."""

    name: ClassVar[str] = "edit_starting_picture"
    keep: list[str] = Field(
        description="Everything that stays exactly the same: the person, the room, the light, "
        "the grip, and every part of the product, each named."
    )
    change: str = Field(description="The one change, and what that part becomes.")

    def run(self, call: ToolCall) -> ToolResult:
        return _edit(_run_of(call), self)


class Film(Tool):
    """Write the scene's video prompt from the video form below, once the newest starting
    picture has passed the check, with the picture in front of you. Code puts the speed line
    first and the silence line last, joins your boxes in this order, and films the starting
    picture with it. Describe the scene as if it were real: no picture labels, no style
    words, no lists of what must not happen. About 106 to 150 words in all."""

    name: ClassVar[str] = "film"
    shot_size: str = Field(
        description='The shot size, after "Live-action vertical phone footage, handheld UGC,". '
        'A made-up example: "close-up".'
    )
    frame_shows: str = Field(
        description="What the frame shows (the person, the room, where the light comes from), "
        "in the picture form's own words."
    )
    product: str = Field(
        description="The product in one phrase: colour, material, shape, brand and one printed "
        "word it shows."
    )
    camera: str = Field(
        description="A camera word, then what the camera does in plain words. It never turns "
        "the product to a side no photo shows."
    )
    beats: list[str] = Field(
        description="The beats of the scene's one action, in order, filling the whole clip; "
        'code joins them with "then". A repeating action says that it repeats.'
    )
    shows_up_later: str = Field(
        description="What shows up as the clip goes on (contents, a result), in the page's "
        'words; "not needed here" when nothing does.'
    )
    small_life: str = Field(
        description='Small life when a person is in it: blinks, breath, a weight shift. "not '
        'needed here" when no person is.'
    )
    lock_line: str = Field(
        description='Last: "The [product] keeps its exact shape, colour and printed words." '
        "After a failed clip it grows: every part that must stay as it is."
    )
    anything_else: str = Field(
        description="Anything the boxes above don't cover that this clip needs; else \"not "
        'needed here". A made-up example: "the curtain behind her sways a little in the breeze".'
    )

    def run(self, call: ToolCall) -> str:
        return _film(_run_of(call), self)


class ChangeScene(Tool):
    """Change this scene's row: its line, what it shows (the one action), how the product is
    used, what it ends on, its kind and whether the presenter's face is in it. "Only the end
    result" is one kind of action: the clip starts with the action done and holds on its
    result. Code fact checks changed words against the page and refuses what breaks the
    rules. It draws and films nothing: draw again after it."""

    name: ClassVar[str] = "change_scene"
    why_change: str = Field(description="In plain words: why the scene changes.")
    line: str = Field(description="The scene's line, word for word: the same, or a new true one.")
    shows: str = Field(description="What the scene shows: the one action, in one sentence.")
    usage: str = Field(description="How the product is used in it, in one sentence.")
    result: str = Field(description='What it ends on, for "does a job"; empty otherwise.')
    broll_kind: Literal["does a job", "showcase", "shows the problem"]
    person_shown: Literal["no face", "has face"]

    def run(self, call: ToolCall) -> str:
        return _change_scene(_run_of(call), self, call)


class LookAtLibrary(Tool):
    """Open one library item by its number, to see the picture; or one moment of a page
    video, by the video's number and the moment's, to see its frames. What you open stays in
    front of you."""

    name: ClassVar[str] = "look_at_library"
    item: int | None = Field(description="The library item's number; null for a moment.")
    video: int | None = Field(description="For a moment: the page video's number; else null.")
    moment: int | None = Field(description="For a moment: its number in that video; else null.")

    def run(self, call: ToolCall) -> ToolResult:
        return _look_at_library(_run_of(call), self)


class LookAtTry(Tool):
    """See an earlier starting picture again, by its number, with the check's answers."""

    name: ClassVar[str] = "look_at_try"
    number: int

    def run(self, call: ToolCall) -> ToolResult:
        run = _run_of(call)
        found = [each for each in run.pictures if each.number == self.number]
        if not found:
            raise Refused(f"pictures so far: 1 to {len(run.pictures)}.")
        return _shown(found[0])


class GiveUp(Tool):
    """Give up on this scene, with your reason: the producer tells the shop owner and
    offers a way out (say it to camera, or send a photo)."""

    name: ClassVar[str] = "give_up"
    reason: str

    def run(self, call: ToolCall) -> str:
        run = _run_of(call)
        run.ended = f"gave up: {self.reason}"
        return "The scene is given up."


TOOLS: list[type[Tool]] = [
    LookAtLibrary,
    LookAtTry,
    ChangeScene,
    DrawStartingPicture,
    EditStartingPicture,
    Film,
    GiveUp,
]
AGENT_SPEC = loop.Agent(name=AGENT, purpose=PURPOSE, instructions=INSTRUCTIONS, tools=TOOLS)


# ---------------------------------------------------------------- the picture check

CHECK_INSTRUCTIONS = """\
You check one starting picture for a B-roll scene of a video ad, before it is animated. You \
are shown the picture, then every product photo it was drawn from, each with its job line. \
You are given the product's name, the ad's colour of it, and the page's words. Answer two \
questions, each in one line:
1. Does the product, with what is inside it, look the same as in its photos: its shape, \
colours, parts, printed words and contents? Where the photos don't show a part, the page's \
words say how it looks. A part the photos never show, drawn plausibly, doesn't fail it.
2. Does anything in the picture break physics: something no real camera could film, such as \
a person brushing hair with no head under it, a hand that isn't joined to an arm, an object \
floating, or a thing passing through another? A slightly wrong hand doesn't fail it.
Fail a question only for what you can see in the picture, and name what is wrong and where."""


class Answer(BaseModel):
    passes: bool
    says: str = Field(
        description="One line: what you saw, and what is wrong and where if it fails."
    )


class PictureCheckAnswers(BaseModel):
    product_same: Answer = Field(
        description="Question 1: does the product, with what is inside it, look the same as in its photos?"
    )
    physics: Answer = Field(description="Question 2: does anything in the picture break physics?")


class PictureCheckHandoff(Handoff):
    product_name: str
    product_colour: str
    page_text: str
    owner_chat: str
    photos: list[str]


def _check(run: Run, key: str, photos: list[tuple[str, str]]) -> PictureCheckAnswers:
    """The check's two answers for the picture at `key`, drawn from `photos` (job line,
    key). It gets none of the agent's chat or reasons, nor the prompt or earlier pictures."""
    inputs = run.inputs
    return call_model(
        job=None,
        purpose=CHECK,
        instructions=CHECK_INSTRUCTIONS,
        handoff=PictureCheckHandoff(
            product_name=inputs.product_name,
            product_colour=inputs.product_colour,
            page_text=page.for_model(inputs.page_text),
            owner_chat=inputs.owner_chat or "(none)",
            photos=[line for line, _ in photos],
        ),
        output=PictureCheckAnswers,
        images=[
            Image(label="The starting picture to check:", key=key),
            *(Image(label=line, key=photo) for line, photo in photos),
        ],
    )


# ---------------------------------------------------------------- drawing


def _photos(inputs: Inputs) -> list[LibraryItem]:
    return [each for each in inputs.library if each.photo][:MOST_PHOTOS]


def _photo_line(number: int, item: LibraryItem, product: str) -> str:
    return (
        f"Image {number} is a photo of the {product} (library item {item.number}: {item.label}). "
        "Use it only for how the product looks: its shape, colours, parts and printed words."
    )


SHARED_LINES = {
    "person_in_room": "Image {n} shows the person and the room of this ad: the same person "
    "with the same face, hair and clothes, in the same room with the same light. Use it for "
    "the person and the room only; everything else comes from this description.",
    "portrait": "Image {n} shows the presenter of this ad: the same person with the same "
    "face, hair and clothes. Use it for the person only; everything else comes from this "
    "description.",
    "room": "Image {n} shows the room of this ad: the same room with the same light. Use it "
    "for the room only; everything else comes from this description.",
}


def _refuse_past_the_cap(run: Run) -> None:
    if run.ended:
        raise Refused(f"the run has ended ({run.ended}).")
    if len(run.pictures) >= MOST_PICTURES_PER_RUN:
        raise Refused("the run has ended: no more starting pictures can be made in it.")


def _draw(run: Run, form: DrawStartingPicture) -> ToolResult:
    _refuse_past_the_cap(run)
    inputs = run.inputs
    photos = _photos(inputs)
    if not photos:
        raise Refused("the library holds no product photo.")
    lines = [_photo_line(i, item, inputs.product_name) for i, item in enumerate(photos, 1)]
    keys = [item.key for item in photos]
    for key, kind in run.shared():
        lines.append(SHARED_LINES[kind].format(n=len(keys) + 1))
        keys.append(key)
    boxes = [
        form.camera,
        form.person_and_room,
        form.pose,
        form.inside,
        form.size,
        form.moment,
        form.light,
        form.bodies,
        form.anything_else,
    ]
    prompt = " ".join([*lines, *filter(None, map(_box, boxes)), PICTURE_ENDING])
    return _made(
        run, "drawn", prompt, keys, list(zip(lines[: len(photos)], keys, strict=False)), form
    )


def _edit(run: Run, form: EditStartingPicture) -> ToolResult:
    _refuse_past_the_cap(run)
    current = run.current
    if current is None:
        raise Refused("there is no starting picture yet: draw one first.")
    if not form.change.strip():
        raise Refused("write the one change.")
    inputs = run.inputs
    photos = _photos(inputs)
    lines = [_photo_line(i, item, inputs.product_name) for i, item in enumerate(photos, 2)]
    keep = "; ".join(each.strip() for each in form.keep if each.strip())
    prompt = " ".join(
        [
            f"Edit image 1. Keep {keep} exactly the same." if keep else "Edit image 1.",
            f"Change only this: {_box(form.change)}",
            "Keep everything else as it is in image 1.",
            *lines,
            "Do not alter the product design. No text.",
        ]
    )
    keys = [current.key, *(item.key for item in photos)]
    return _made(run, "edited", prompt, keys, list(zip(lines, keys[1:], strict=True)), form)


def _made(
    run: Run,
    how: Literal["drawn", "edited"],
    prompt: str,
    keys: list[str],
    photos: list[tuple[str, str]],
    form: BaseModel,
) -> ToolResult:
    key = edit_picture(job=None, purpose=DRAW, prompt=prompt, pictures=keys)
    answers = _check(run, key, photos)
    picture = Picture(
        number=len(run.pictures) + 1,
        key=key,
        how=how,
        prompt=prompt,
        sent=keys,
        product_same=answers.product_same.model_dump(),
        physics=answers.physics.model_dump(),
        form=form.model_dump(),
    )
    run.pictures.append(picture)
    shown = _shown(picture)
    if not picture.passed and len(run.pictures) >= MOST_PICTURES_PER_RUN:
        run.ended = "gave up: every starting picture this run could make failed the check"
        return ToolResult(f"{shown.text}\nThe run has ended: it gives up.", shown.pictures)
    return shown


def _shown(picture: Picture) -> ToolResult:
    verdict = "It passes the check." if picture.passed else "It fails the check: fix it."
    return ToolResult(
        f"Starting picture {picture.number} ({picture.how}). The check's answers:\n"
        f"1. Product the same as its photos: {'yes' if picture.product_same['passes'] else 'no'}. "
        f"{picture.product_same['says']}\n"
        f"2. Breaks physics: {'no' if picture.physics['passes'] else 'yes'}. "
        f"{picture.physics['says']}\n{verdict}",
        [Image(label=f"Starting picture {picture.number}", key=picture.key)],
    )


# ---------------------------------------------------------------- the video prompt


def video_prompt(form: Film) -> str:
    """The video prompt as it is sent: code's lines around the agent's boxes."""
    beats = ", then ".join(
        re.sub(r"^(and )?then\s+", "", beat.strip().rstrip("."), flags=re.IGNORECASE)
        for beat in form.beats
        if beat.strip()
    )
    parts = [
        SPEED_LINE,
        _box(f"Live-action vertical phone footage, handheld UGC, {form.shot_size.strip()}"),
        _box(form.frame_shows),
        _box(form.product),
        _box(form.camera),
        _box(beats),
        _box(form.shows_up_later),
        _box(form.small_life),
        _box(form.anything_else),
        _box(form.lock_line),
        SILENT_LINE,
    ]
    return " ".join(filter(None, parts))


def _film(run: Run, form: Film) -> str:
    if run.ended:
        raise Refused(f"the run has ended ({run.ended}).")
    current = run.current
    if current is None or not current.passed:
        raise Refused("the newest starting picture hasn't passed the check: fix it first.")
    if not [beat for beat in form.beats if beat.strip()]:
        raise Refused("write the action's beats.")
    prompt = video_prompt(form)
    words = len(prompt.split())
    run.video_prompt = prompt
    run.video_form = {**form.model_dump(), "picture": current.number}
    # Stage 2 films here (spec section 3); held until Julian's calls on what the clip judge
    # compares against and on page-video scenes (spec Undecided 2 and 3).
    run.ended = "prompt written (stage 1: filming is held)"
    return (
        f"The video prompt is kept ({words} words), for starting picture {current.number}. "
        "Filming is held in this test, so your run ends here."
    )


# ---------------------------------------------------------------- the scene row


def _same(one: str, other: str) -> bool:
    return " ".join(one.split()).lower() == " ".join(other.split()).lower()


def _change_scene(run: Run, asked: ChangeScene, call: ToolCall) -> str:
    if run.ended:
        raise Refused(f"the run has ended ({run.ended}).")
    inputs = run.inputs
    row = inputs.row()
    if not asked.shows.strip():
        raise Refused("the scene must show an action or its result.")
    if asked.broll_kind != "does a job" and asked.result.strip():
        raise Refused('only a "does a job" scene has a result.')
    for other in inputs.plan:
        if (
            other["number"] != inputs.scene
            and other.get("shows")
            and _same(other["shows"], asked.shows)
        ):
            raise Refused(f"scene {other['number']} already films this action.")
    line = " ".join(asked.line.split())
    action_changed = not _same(asked.shows, row.get("shows") or "")
    told = _same(line, row["line"]) and action_changed
    key = " ".join(asked.shows.split()).lower()
    if told and key not in run.told_the_line:
        run.told_the_line.add(key)
        return (
            f'The scene\'s line says: "{row["line"]}" The action changes but the line stays. '
            "Decide whether the line still fits the new action: if it says something the clip "
            "won't show, call change_scene again with a new line; if it still fits, call it "
            "again with the same line. Nothing was changed."
        )
    wanted = {
        "line": line,
        "shows": asked.shows.strip(),
        "usage": asked.usage.strip(),
        "result": asked.result.strip(),
        "broll_kind": asked.broll_kind,
        "person_shown": asked.person_shown,
    }
    changed = [name for name, value in wanted.items() if value != (row.get(name) or "")]
    if not changed:
        return "Nothing changed: the scene row is already this."
    if {"line", "shows", "usage", "result"} & set(changed):
        _fact_checked(inputs, wanted)
    seconds = inputs.line_seconds
    said = ""
    if "line" in changed:
        # Not voiced in this test: its length is estimated from its words.
        seconds = max(1.0, len(line.split()) / 2.6)
        inputs.line_seconds = seconds
        said = f" The new line takes about {seconds:.1f} s to say (estimated), so the clip is {_broll_clip_seconds(seconds)} s."
    row.update(wanted)
    run.scene_changes.append(
        {"after_picture": len(run.pictures), "why": asked.why_change, **wanted}
    )
    return f"The scene row is changed: {', '.join(changed)}. Nothing was drawn.{said}"


def _fact_checked(inputs: Inputs, wanted: dict[str, Any]) -> None:
    checked = call_model(
        job=None,
        purpose="fact_check",
        instructions=FACT_CHECK_INSTRUCTIONS,
        handoff=FactCheckHandoff(
            page_text=page.for_model(inputs.page_text),
            conversation=[],
            product_colour=inputs.product_colour,
            lines=[
                LineToCheck(
                    scene=inputs.scene,
                    line=wanted["line"],
                    shows=wanted["shows"],
                    usage=wanted["usage"] or None,
                    result=wanted["result"] or None,
                )
            ],
            video_transcripts=[],
        ),
        output=fact_check_for([inputs.scene], showing=[inputs.scene]),
        images=[Image(label=f"Photo {item.number}", key=item.key) for item in _photos(inputs)],
    )
    verdict = checked.lines[0] if checked.decision == "checked" and checked.lines else None
    if verdict is None:
        raise Refused(
            f"the fact check couldn't tell whether it is true: {checked.question or checked.reason}"
        )
    if verdict.verdict != "ok":
        raise Refused(
            f"the fact check failed it: {verdict.problem} The page says: {verdict.page_says}"
        )


# ---------------------------------------------------------------- looking


def _look_at_library(run: Run, asked: LookAtLibrary) -> ToolResult:
    inputs = run.inputs
    if asked.item is not None and asked.video is None and asked.moment is None:
        found = [each for each in inputs.library if each.number == asked.item]
        if not found:
            raise Refused(f"there is no library item {asked.item}.")
        item = found[0]
        return ToolResult(
            f"Library item {item.number}: {item.label}",
            [Image(label=f"Library item {item.number}: {item.label}", key=item.key)],
        )
    if asked.item is None and asked.video is not None and asked.moment is not None:
        moments = [m for m in inputs.moments if (m.video, m.moment) == (asked.video, asked.moment)]
        if not moments:
            raise Refused(f"page video {asked.video} has no moment {asked.moment}.")
        moment = moments[0]
        if moment.key is None:
            raise Failed("this test holds that moment's words but not its video.")
        # Imported here: it pulls the watcher in, as the fixing agent's does.
        from agents.fixing_agent import _four_to_a_picture
        from jobs import clip_judge, product_videos

        part = product_videos.cut_part(moment.key, moment.start, moment.end, sound=False)
        frames = [
            {**frame, "times": [round(moment.start + t, 3) for t in frame["times"]]}
            for frame in clip_judge.piece_frames(part)
        ]
        named = f"Page video {moment.video}, moment {moment.moment}"
        return ToolResult(
            f"{named}, {moment.start:g} s to {moment.end:g} s: {moment.happens} On screen: {moment.on_screen}",
            [Image(label=each.label, key=each.key) for each in _four_to_a_picture(frames, named)],
        )
    raise Refused("name a library item, or a video and its moment, not both.")


# ---------------------------------------------------------------- the loop


def _intro(run: Run) -> str:
    inputs = run.inputs
    row = inputs.row()
    plan = "\n".join(_scene_facts(each) for each in inputs.plan)
    library = "\n".join(
        f"{item.number}. {item.label}" + ("" if item.photo else " (not a product photo)")
        for item in inputs.library
    )
    moments = "\n".join(
        f"Page video {m.video}, moment {m.moment}, {m.start:g} s to {m.end:g} s: {m.happens} "
        f"On screen: {m.on_screen}"
        for m in inputs.moments
    )
    return "\n\n".join(
        [
            f"The ad is for this product page: {inputs.product_url}",
            f"The product: {inputs.product_name}, in {inputs.product_colour or 'the colour its photos show'}.",
            f"You make scene {inputs.scene}:\n{_scene_facts(row)}",
            "The ad's picture library, one line per item, which look_at_library opens by its "
            f"number:\n{library or '(no items)'}"
            + (f"\n\nThe page videos' moments:\n{moments}" if moments else ""),
            "How this product behaves, as the planner wrote it from the page: "
            + (inputs.product_behaviour or "the planner wrote no note for this ad."),
            f"The whole plan, scene by scene:\n{plan}",
            f"Scene {inputs.scene}'s line takes {inputs.line_seconds:.2f} s to say, so its clip "
            f"is {_broll_clip_seconds(inputs.line_seconds)} s.",
            f"The page's words:\n{inputs.page_text}",
        ]
    )


def _scene_facts(scene: dict[str, Any]) -> str:
    facts = [f"Scene {scene['number']}", f'  line: "{scene["line"]}"']
    if scene.get("shows"):
        facts += [
            f"  shows: {scene['shows']}",
            f"  kind: {scene.get('broll_kind') or '?'}; face: {scene.get('person_shown') or '?'}",
            *([f"  how the product is used: {scene['usage']}"] if scene.get("usage") else []),
            *([f"  ends on: {scene['result']}"] if scene.get("result") else []),
        ]
    else:
        facts.append("  the presenter says it to camera")
    return "\n".join(facts)


SHARED_ID = "shared-picture"


def _start(run: Run) -> None:
    run.conversation = [Said(by="user", text=_intro(run))]
    shared = run.shared()
    if shared:
        what = {
            "person_in_room": "the ad's person in its room",
            "portrait": "the ad's presenter",
            "room": "the ad's room, with nobody in it",
        }
        run.conversation.append(
            ToolUse(
                call_id=SHARED_ID,
                tool=LookAtLibrary.name,
                arguments={"item": None, "video": None, "moment": None},
                result="The shared pictures: "
                + "; ".join(what[kind] for _, kind in shared)
                + ". Every scene of the ad is drawn with them.",
                pictures=[
                    Shown(label=f"The shared picture: {what[kind]}", key=key)
                    for key, kind in shared
                ],
            )
        )


def _fit(conversation: list[Happened]) -> list[Happened]:
    """The newest pictures within MOST_SHOWN; the shared picture always."""
    room = MOST_SHOWN
    kept: list[Happened] = []
    for each in reversed(conversation):
        if isinstance(each, ToolUse) and each.pictures and each.call_id != SHARED_ID:
            if len(each.pictures) <= room - 1:
                room -= len(each.pictures)
            else:
                each = each.model_copy(
                    update={
                        "pictures": [],
                        "result": f"{each.result} (Its pictures aren't shown any more: look again.)",
                    }
                )
        kept.append(each)
    return list(reversed(kept))


REMINDER = "Answer with a tool call: a look, a picture, the video prompt or give_up."


def make(inputs: Inputs, out: Path, *, on_step: Callable[[Run], None] | None = None) -> Run:
    """Have a scene agent make `inputs`' scene, until the video prompt is written or it gives
    up; everything it did is kept in `out`/run.json as it goes."""
    out.mkdir(parents=True, exist_ok=True)
    session = Session.objects.create(name=f"scene agent prototype: {inputs.slug}")
    run = Run(inputs=inputs, session=session, out=out)
    RUNS[str(session.pk)] = run
    _start(run)
    idle = 0
    try:
        while not run.ended:
            turn = loop.take_a_turn(
                session=session,
                job=None,
                purpose=PURPOSE,
                instructions=INSTRUCTIONS,
                conversation=_fit(run.conversation),
                tools=AGENT_SPEC.tool_specs(),
                done=f"the scene agent of {inputs.slug} carried on",
            )
            if turn.says:
                run.conversation.append(Said(by="agent", text=turn.says))
            if not turn.calls:
                idle += 1
                if idle >= MOST_IDLE_TURNS:
                    run.ended = "stopped: the agent called no tool three turns in a row"
                    break
                run.conversation.append(Said(by="user", text=REMINDER))
                continue
            idle = 0
            for asked in turn.calls:
                call = ToolCall.objects.create(
                    session=session,
                    agent=AGENT,
                    tool=asked.tool,
                    call_id=asked.call_id or uuid.uuid4().hex,
                    arguments=asked.arguments,
                )
                if run.ended:
                    call.result = f"Refused: the run has ended ({run.ended}). Nothing was done."
                    call.answer = "cant"
                    call.save(update_fields=["result", "answer"])
                else:
                    loop.settle(AGENT_SPEC, call, counted=False)
                call.refresh_from_db()
                run.conversation.append(
                    ToolUse(
                        call_id=call.call_id,
                        tool=call.tool,
                        arguments=call.arguments,
                        result=call.result,
                        pictures=[Shown(**picture) for picture in call.pictures],
                    )
                )
            keep(run)
            if on_step is not None:
                on_step(run)
    finally:
        keep(run)
    return run


def cost(run: Run) -> Decimal:
    total = ModelCall.objects.filter(session=run.session).aggregate(total=Sum("cost_usd"))["total"]
    return total or Decimal(0)


def keep(run: Run) -> None:
    """Everything the run did so far, as run.json in its folder."""
    record = {
        "slug": run.inputs.slug,
        "scene": run.inputs.scene,
        "row": run.inputs.row(),
        "ended": run.ended,
        "cost_usd": str(cost(run)),
        "video_prompt": run.video_prompt,
        "video_form": run.video_form,
        "scene_changes": run.scene_changes,
        "pictures": [
            {
                "number": p.number,
                "how": p.how,
                "file": file_store.url(p.key),
                "key": p.key,
                "prompt": p.prompt,
                "sent": p.sent,
                "product_same": p.product_same,
                "physics": p.physics,
                "passed": p.passed,
                "form": p.form,
            }
            for p in run.pictures
        ],
        "conversation": [each.model_dump(mode="json") for each in run.conversation],
    }
    (run.out / "run.json").write_text(json.dumps(record, indent=2))
