"""A B-roll clip's first-send prompt (#210 point 16): what the video model is told with the
pictures and pieces cut-and-send resolved for the clip, the first time it is filmed.

Code puts the speed line first and writes each piece's line and the drawn picture's line
(`drawn_label`, fixed text); the writer model writes each library picture's label line, from
its library label and what the scene uses it for, and ONE sentence: the action, the thing,
the place and the end state. Nothing else goes: no opener about the camera or the side, no
line saying a picture is how the clip starts, no picture prompt, no timings.
A retry's prompt is the fixing agent's, not this one."""

import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from gateway.gateway import call_model
from gateway.types import Handoff
from jobs.library import Drawn, For, SentPicture, SentPiece
from jobs.models import Job

# Put first by code in every clip's prompt, the first send and every retry (#210 points 16, 23).
SPEED_LINE = "Film this in real-time speed."
# Every piece's label: a piece is sent for how things move in it, never for how they look.
# Prototype (lean inputs, item 5): the video model may copy the look as well as the movement.
PIECE_LABEL = "the movement to copy; its look may be copied too"
# The drawn picture's label (tier 3), fixed text: #210 point 12's line for the person in the
# room, said of the presenter the plan made (its words are the woman's), and the room-only
# line to match it. A job planned before the presenter's gender was asked says "presenter".
PERSON_IN_ROOM_LABEL = (
    "the {person} and the room: {they} the person in the clip and it is the background"
)
ROOM_LABEL = "the room: it is the background"
_PRESENTER: dict[str, tuple[str, str]] = {"woman": ("woman", "she is"), "man": ("man", "he is")}


def drawn_label(job: Job, drawn: Drawn) -> str:
    """The words after "Image N is" for `job`'s drawn picture `drawn`."""
    if drawn == "room":
        return ROOM_LABEL
    person, they = _PRESENTER.get(job.person_gender, ("presenter", "they are"))
    return PERSON_IN_ROOM_LABEL.format(person=person, they=they)


FIRST_SEND_INSTRUCTIONS = """\
You write the prompt for one B-roll clip of a short vertical video ad. A video model films \
the clip from the pictures it is sent, Image 1, Image 2 and so on (those in "pictures", and \
the drawn one whose line is "drawn", when there is one), and from any videos sent after \
them, while the presenter's voice says the scene's "line" over it.
Each picture has its "label", what the picture shows, and may have "used_for", what the \
scene takes from it: "look" is how the product looks when the clip starts, "content" is \
what the product takes in or acts on, "result" how the \
scene ends, "in-use" how the product is held or used, "place" where it happens, "person" \
how the person, or their hand, is with the product, and "state" how the product is in this \
scene. A picture without "used_for" is there for how the product looks.
Each picture also has its "colour", the colour the product is in that picture. A picture may \
have been recoloured after its label was written, so when a line says the product's colour, \
it says the picture's "colour", never a colour word from its label.
Write two things:
- "labels": one for each picture in "pictures", in their order. Code writes "Image N is" \
before each and a full stop after, so write only the rest of that line: what the picture \
is, from its label, in a few plain words starting in lower case, then, when it has a \
"used_for", what the clip takes from it, said the natural way. Say nothing about the \
picture its label doesn't say.
- "sentence": ONE sentence for the whole clip, built from the scene and the pictures: the \
action, the thing it is done to or with, the place, and how it ends, said the way a person \
would say it. Give no timings. Never say anything a picture contradicts.
Code puts the speed line first, the line for each video and the "drawn" line, so write none \
of them."""


def _one_line(text: str) -> str:
    if "\n" in text:
        raise ValueError("a label is one line")
    return text


def _one_sentence(text: str) -> str:
    if "\n" in text or re.search(r"[.!?]\s", text) or text[-1] not in ".!?":
        raise ValueError("the sentence is one sentence, ended by a full stop")
    return text


Words = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class LabelledPicture(Handoff):
    image: int
    label: str
    colour: str
    used_for: For | None


class FirstSendScene(Handoff):
    """What the writer is told of the scene the clip is for."""

    line: str
    shows: str
    broll_kind: str
    usage: str
    result: str


class FirstSendHandoff(Handoff):
    scene: FirstSendScene
    pictures: list[LabelledPicture]
    drawn: str | None
    videos: int


def _answer_for(pictures: int) -> type[BaseModel]:
    """The writer's answer for a clip sent `pictures` pictures: exactly one label each."""

    class FirstSendAnswer(BaseModel):
        labels: list[Annotated[Words, AfterValidator(_one_line)]] = Field(
            min_length=pictures,
            max_length=pictures,
            description='One per picture, in order: the words after "Image N is".',
        )
        sentence: Annotated[Words, AfterValidator(_one_sentence)]

    return FirstSendAnswer


def _drawn_line(job: Job, picture: SentPicture) -> str:
    assert picture.drawn is not None
    return f"Image {picture.image} is {drawn_label(job, picture.drawn)}."


def _colours(job: Job, library: Sequence[SentPicture]) -> dict[int, str]:
    """Each library picture's colour, by its image number: its library item's own colour,
    which a recoloured item's label may not say (Julian, tickets thread, Oct 29: the label is
    kept, the colour field holds the new colour)."""
    numbers: dict[int, int] = {}
    for picture in library:
        assert picture.library_number is not None, "a library picture has its number"
        numbers[picture.image] = picture.library_number
    held = dict(job.library.filter(number__in=numbers.values()).values_list("number", "colour"))
    return {image: held[number] for image, number in numbers.items()}


def write_first_send_prompt(
    job: Job, pictures: Sequence[SentPicture], pieces: Sequence[SentPiece], scene: FirstSendScene
) -> str:
    """The prompt a clip is first sent with: SPEED_LINE, then a line for each picture in
    order (the model's from its label and its library item's colour for a library picture,
    drawn_label's for the drawn one), then one for each piece, then the model's one
    sentence, each on its own line."""
    library = [picture for picture in pictures if picture.drawn is None]
    drawn = [_drawn_line(job, picture) for picture in pictures if picture.drawn is not None]
    colours = _colours(job, library)
    answer = call_model(
        job=job,
        purpose="write_first_send_prompt",
        instructions=FIRST_SEND_INSTRUCTIONS,
        handoff=FirstSendHandoff(
            scene=scene,
            pictures=[
                LabelledPicture(
                    image=picture.image,
                    label=picture.label,
                    colour=colours[picture.image],
                    used_for=picture.for_,
                )
                for picture in library
            ],
            drawn=drawn[0] if drawn else None,
            videos=len(pieces),
        ),
        output=_answer_for(len(library)),
        # A clip step run again, as after a worker stopped, is answered from the record.
        pay_once=True,
    )
    written = dict(
        zip(
            (picture.image for picture in library),
            answer.labels,  # type: ignore[attr-defined]
            strict=True,
        )
    )
    sentence: str = answer.sentence  # type: ignore[attr-defined]
    return "\n".join(
        [
            SPEED_LINE,
            *(
                _drawn_line(job, picture)
                if picture.drawn
                else f"Image {picture.image} is {written[picture.image].rstrip('. ')}."
                for picture in pictures
            ),
            *(f"Video {piece.number} is {PIECE_LABEL}." for piece in pieces),
            sentence,
        ]
    )
