"""A B-roll clip's first-send prompt (#210 point 16): what the video model is told with the
pictures and pieces cut-and-send resolved for the clip, the first time it is filmed.

Code puts the speed line first and writes each piece's line; the writer model writes each
picture's label line, from its library label and what the scene uses it for, and ONE
sentence: the action, the thing, the place and the end state. Nothing else goes: no opener
about the camera or the side, no line saying a picture is how the clip starts, no picture
prompt, no timings. A retry's prompt is the fixing agent's, not this one."""

import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from gateway.gateway import call_model
from gateway.types import Handoff
from jobs.library import For, SentPicture, SentPiece
from jobs.models import Job

# Put first by code in every clip's prompt, the first send and every retry (#210 points 16, 23).
SPEED_LINE = "Film this in real-time speed."
# Every piece's label: a piece is sent for how things move in it, never for how they look.
PIECE_LABEL = "only for the movement, not the look"

FIRST_SEND_INSTRUCTIONS = """\
You write the prompt for one B-roll clip of a short vertical video ad. A video model films \
the clip from the pictures it is sent, Image 1, Image 2 and so on in "pictures", and from \
any videos sent after them, while the presenter's voice says the scene's "line" over it.
Each picture has its "label", what the picture shows, and may have "used_for", what the \
scene takes from it: "content" is what the product takes in or acts on, "result" how the \
scene ends, "in-use" how the product is held or used, "place" where it happens, "person" \
who does it, and "state" how the product is in this scene. A picture without "used_for" is \
there for how the product looks.
Write two things:
- "labels": one for each picture, in the order of "pictures". Code writes "Image N is" \
before each and a full stop after, so write only the rest of that line: what the picture \
is, from its label, in a few plain words starting in lower case, then, when it has a \
"used_for", what the clip takes from it, said the natural way. Say nothing about the \
picture its label doesn't say.
- "sentence": ONE sentence for the whole clip, built from the scene and the pictures: the \
action, the thing it is done to or with, the place, and how it ends, said the way a person \
would say it. Give no timings.
Code puts the speed line first and the line for each video, so write neither."""


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


def write_first_send_prompt(
    job: Job, pictures: Sequence[SentPicture], pieces: Sequence[SentPiece], scene: FirstSendScene
) -> str:
    """The prompt a clip is first sent with: SPEED_LINE, then a line for each picture in
    order, written by the model from its label, then one for each piece, then the model's
    one sentence, each on its own line."""
    answer = call_model(
        job=job,
        purpose="write_first_send_prompt",
        instructions=FIRST_SEND_INSTRUCTIONS,
        handoff=FirstSendHandoff(
            scene=scene,
            pictures=[
                LabelledPicture(image=picture.image, label=picture.label, used_for=picture.for_)
                for picture in pictures
            ],
            videos=len(pieces),
        ),
        output=_answer_for(len(pictures)),
    )
    labels: list[str] = answer.labels  # type: ignore[attr-defined]
    sentence: str = answer.sentence  # type: ignore[attr-defined]
    return "\n".join(
        [
            SPEED_LINE,
            *(
                f"Image {picture.image} is {label.rstrip('. ')}."
                for picture, label in zip(pictures, labels, strict=True)
            ),
            *(f"Video {piece.number} is {PIECE_LABEL}." for piece in pieces),
            sentence,
        ]
    )
