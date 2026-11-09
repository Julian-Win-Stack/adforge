"""The move check: before any clip is filmed, a separate model looks at exactly what H3
will get (its first frame, its last frame when there is one, and the video prompt) and asks
one narrow question: can the planned move really happen, or must a moving part pass through
something solid? (Physics try, Nov 9 2026: round 12 and 13's heel strap had to pass through
the ankle, and no step ever asked.)"""

import logging

from django.conf import settings
from pydantic import BaseModel, Field

from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import UnreadableImage, call_model
from gateway.types import AnswerTookTooLong, BlockedBySafetyFilter, Handoff, Image, UnusableReply
from jobs import notices
from jobs.models import Job

logger = logging.getLogger(__name__)

# The film tool's refusal starts with this, so a run's failed move checks can be counted.
MOVE_FAILED = "The move check failed:"
# After this many failed move checks in a run, the clip is filmed anyway, with a notice.
MOST_MOVE_FAILS = 3

MOVE_INSTRUCTIONS = """\
You check one planned move of a short video clip before it is filmed. You are shown the \
clip's first frame, its last frame when there is one, and the video prompt the video model \
will be given.

First find every part the prompt moves: a hand, the product, or a part of it such as a lid, \
a strap, a handle or a door. For each one, trace its path from where it is in the first \
frame to where it must end (the last frame, or where the prompt says it ends): around its \
pivot for a part that swings or turns, along its track for a part that slides.

Then answer one question: is something solid in that path, such as a body part, a wall or \
another object, so the moving part would have to pass through it; or must a stiff part bend \
to get there?

Judge the path from the frames, not from the prompt's words. Words that say a part flexes, \
stretches, clears, slips or goes around something are not evidence: look at where the part \
is fixed and what stands between its start and its end. A part fixed to the product at both \
of its ends (a loop, a handle, a band) cannot get from one side of a body part or object to \
the other side while that body part or object stays inside the loop.

Made-up examples that fail: a cabinet door that must swing open through a chair standing in \
front of it; a ring that must slide off a finger while a fist is clenched around it; a \
wooden ruler that must bend around a mug. \
Made-up examples that pass: a hand lifts a jar's lid straight up with nothing above it; a \
hand slides a book along an empty shelf; only the camera moves; a person talks, nods and \
gestures with an empty hand while the product rests still.

Fail only when you are sure from the frames and the prompt. How the clip looks, the product's \
look, the camera, the lighting and anything else are not this check's question."""


class MoveCheckHandoff(Handoff):
    video_prompt: str


class MoveCheckAnswer(BaseModel):
    moving_parts: str = Field(
        description="Each part that moves: where it starts in the first frame, where it must "
        "end, and its path (around which pivot, or along which track)."
    )
    blocked: bool = Field(
        description="True only when you are sure something solid is in a moving part's path, "
        "or a stiff part would have to bend."
    )
    says: str = Field(
        description="One line: what is in the path and where, or why the path is clear."
    )


def check(job: Job, *, scene: int, first: str, last: str | None, prompt: str) -> dict[str, object]:
    """Ask the move check about a clip whose first frame is the file `first`, last frame
    `last` (or none) and video prompt `prompt`. Status passed, failed or unchecked."""
    if not settings.QUALITY_CHECKS:
        return {"status": "passed", "answers": None}
    images = [Image(label="The clip's first frame:", key=first)]
    if last is not None:
        images.append(Image(label="The clip's last frame:", key=last))
    try:
        answer = call_model(
            job=job,
            purpose="check_move",
            instructions=MOVE_INSTRUCTIONS,
            handoff=MoveCheckHandoff(video_prompt=prompt),
            output=MoveCheckAnswer,
            images=images,
        )
    except (
        BlockedBySafetyFilter,
        UnusableReply,
        UnreadableImage,
        AnswerTookTooLong,
        OutsideServiceDown,
    ) as error:
        logger.warning("Move unchecked: %s", error)
        notices.post_notice(
            job,
            notices.MOVE_UNCHECKED,
            f"Scene {scene}'s move could not be checked ({error}); the clip is filmed without it.",
            Message.Level.PROBLEM,
            scene=scene,
        )
        return {"status": "unchecked", "answers": None}
    return {"status": "failed" if answer.blocked else "passed", "answers": answer.model_dump()}
