"""The judge of a B-roll clip, run right after the clip is made: two checks, each asked
once (ASKS), each failing when its answer fails it. SIDES looks at SIDES_FRAMES_PER_SECOND
frames for objects that can't exist and a turn that shows a side that isn't real; MOTION looks
at FRAMES_PER_SECOND frames for movement and change no real camera could film. Ported from the
eval harness the judge was tested in (/mnt/project-files/evals/clip-judge-v2: prompts.py arm
PM, the motion wording that passed stage 4 on gpt-6.1-sol, 2026-10-13, and arm F1, the
phone-flip sides wording that caught 3 of 3 flips with no wrong fail on all 110 graded clips,
2026-10-13; judge.py; score.py). The plan: Claude Doc "Clip judge discussion", "Plan: what
runs when" and changes 7 and 9; the SIDES change: decisions/phone-flip-judge-test.md.

The judge's words are Julian's to decide (project rule, 2026-10-12): SIDES_CHECK,
MOTION_CHECK and the answer forms below are kept word for word as tested: SIDES names the
face it sees in every frame and decides the direction of every turn from the motion before
it looks at the face that comes back (Julian's yes 2026-10-14 18:30; no code reads those
fields), MOTION without the changed-thing step, both still failing only when sure, and the
problem in up to 3 sentences for the auto-redo's prompt writer (change 9;
decisions/julian-away-judgment-calls.md call 2). Which model judges is the catalog's
(check_clip_sides, check_clip_motion)."""

import contextvars
import hashlib
import io
import json
import logging
import math
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import PIL.Image
from django.conf import settings
from pydantic import BaseModel, Field, model_validator

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway import catalog
from gateway.gateway import call_model, past_the_safety_filter, refused_by_halving, refuses
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)

from . import assembly
from .first_send import PIECE_LABEL
from .library import VIDEO_API_MOST_PIECE_SECONDS, SentList, sent_list_of
from .models import AgentFix, AgentFixTry, ProducedItem, SceneStep
from .notices import post_left_out_by_the_filter, post_notice, post_refused_by_the_filter
from .planning import ChatMessage
from .scenes import photos_for_needs
from .threads import closing_its_connection

logger = logging.getLogger(__name__)

# Each check is asked once and fails when its answer fails it (Julian 2026-10-22 21:09, "drop
# the vote"; docs/adr/0008-clip-judge-asks-once.md). On round 6's 43 graded clips one ask
# matched his grades as often as 3 asks and a vote (28 vs 29): the judge's errors are its bar,
# made by every ask alike, not noise a vote evens out (runs/gemini-judge-test/one-ask-vs-vote.md).
ASKS = 1

# MOTION's frames: the frame nearest each 1/FRAMES_PER_SECOND second, from the start.
FRAMES_PER_SECOND = 5
# SIDES' frames, the same way: twice as many, so it sees the moment a product is edge-on and
# the frames right before and after, where a turn's direction shows (5 stills missed every
# phone flip; 5 a second missed the direction: harness arms sides5, C8 and F1).
SIDES_FRAMES_PER_SECOND = 10

# ---------------------------------------------------------------- the product's facts

# Said beside the conversation with the shop owner (plan.md §3, Julian 2026-10-12 21:05).
CONVERSATION_FRAME = (
    "This is the conversation with the shop owner. Use it only for what the product is and "
    "looks like. When the owner and the page disagree, the owner is right."
)

PAGE_FRAME = (
    "This is the shop's own text. Use it only for what the product is, what it's made of, "
    "what's inside and which parts it has. Ignore what it claims the product does."
)

# ---------------------------------------------------------------- SIDES (harness arm F1, as tested)

SIDES_CHECK = """\
You check frames cut from an AI-made video clip before it is used in a product ad. Video models \
sometimes draw objects that cannot exist, such as a car with headlights on its back, a door hinged \
on both sides, or a hand with six fingers, and sometimes an object that turns over shows a side \
that is not a real side of it, such as a mug that turns and shows a second handle where the plain \
side should be, or a phone that turns over and shows a camera again where its screen should be. \
Find those, and only those.

You get frames from one clip, ten per second, in order, several per image side by side, each \
labelled with its time in seconds.

Work in three steps.
1. Look at the product, frame by frame. For every frame write which face of the product the camera \
sees. Name the faces by what is drawn on them (for a phone: front = the screen face, back = the \
face with the camera and logo), and use the same name for the same face in every frame. Name the \
parts you can see on that face in that frame, each in a few words with where it sits (top left, \
centre, bottom edge). Write what is drawn, not what such a product usually has. A part hidden by a \
hand or too small to make out is left out, never guessed. Also write which thin edge of the \
product you can see beside the broad face, and on which side of the broad face it sits in the \
picture.
2. Turns. A turn is every stretch where the product goes edge-only, or nearly, between two frames \
that show a broad face. For each turn, decide whether the product kept turning the same way, so \
the face that comes back into view must be the opposite face, or turned back, so the same face \
returns. Decide the direction from the motion only, never from how the face after the turn looks: \
that face is what you are checking, and the direction tells you what it has to be. Cues: the \
narrow strip of broad face beside the thin edge in the frames right before and right after the \
edge-only moment, on the same side of the thin edge both times means it turned back, on the other \
side means it kept turning; an end or edge that was moving toward the camera and keeps moving that \
way means it kept turning; a hand that keeps turning its wrist the same way; a product that tips \
over as it falls or lands keeps turning, it does not tip back up. When the cues are not visible, \
say unsure.
3. Then judge. For each frame, could a real one of that product, seen from that face, show exactly \
those parts in exactly those places? Across the frames: when the product turns, is the face that \
comes into view a real face of it? Do two frames show opposite faces that look the same, although \
a real one's opposite faces differ? Does a part appear or vanish on a face that kept facing the \
camera?

Decide "fail" only when you are sure something is physically impossible: a part on a side where \
the real object can't have it, two sides of one object seen at once, a part fused into another, a \
body part that can't be, two opposite sides that look the same where a real one's differ. Decide \
"pass" for everything else.

You may also be shown one shop photo of the product, and facts about it from the shop. They tell \
you what the product is and how it is built: which parts it has and where they sit. Use them only \
for that. Ignore the photo's background, setting and anything being done with the product; only \
the product itself matters. Never compare the frames with the photo: differences in size, shape, \
angle, distance, colour, label or print are not a fail, and the product may be held, turned, \
upside down or partly hidden. Fail the product only if it shows a part a real one can't have \
there, two sides at once, or a label that didn't turn with it."""

# Change 7's field, Julian's yes 2026-10-13 04:34, in both checks' answers.
BACK_TO_NORMAL = "When is this thing back to normal? Give the second, or say never."


# What SIDES sees of the product in one frame. Filling this in for every frame, and a
# direction for every turn, is what makes the judge decide the direction from the motion
# before it looks at the face that comes back; nothing in the code reads them.
class FrameFace(BaseModel):
    time: float = Field(description="The frame's time in seconds, as labelled.")
    seen: Literal["broad_face", "edge_only", "not_visible", "unsure"] = Field(
        description="broad_face: a face of the product shows. edge_only: the product is seen "
        "so thin that no broad face shows. not_visible: the product is out of view or fully "
        "hidden."
    )
    face: str = Field(
        description="The name of the face that shows (front, back, top, left side...), the "
        "same name for the same face in every frame. Empty when no broad face shows."
    )
    parts: str = Field(
        description="The parts drawn on that face in this frame, each in a few words with "
        "where it sits. Empty when none can be made out."
    )
    thin_edge_side: Literal["left", "right", "top", "bottom", "none"] = Field(
        description="On which side of the broad face, in the picture, the visible thin edge "
        "of the product sits. none when no thin edge shows or no broad face shows."
    )


class Turn(BaseModel):
    from_time: float = Field(description="The last frame before the turn that shows a broad face.")
    to_time: float = Field(description="The first frame after the turn that shows a broad face.")
    direction: Literal["kept_turning", "turned_back", "unsure"]
    why: str = Field(description="One sentence: the motion cue you used, with frame times.")


# SIDES' answer, filled in from the top: it looks at each frame, then each turn, before it
# decides. A comment, not a docstring: a docstring would be sent to the judge in the answer's
# schema.
class SidesVerdict(BaseModel):
    frames: list[FrameFace]
    turns: list[Turn]
    across_frames: str = Field(
        description="Which sides of each main object were seen, in which frames; whether a "
        "side that came into view is a real side; whether a part appeared or "
        "vanished on a side that kept facing the camera."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: up to 3 sentences: the object and what is impossible "
        "about it, the frame time, and what a real one would look like. Null when it passes."
    )
    back_to_normal_at: str | None = Field(description=BACK_TO_NORMAL)


# ---------------------------------------------------------------- MOTION (proof arm PM, as tested)

# Its scope sentence is general since Julian's yes 2026-10-19 03:37 ("general scope wording,
# no list"): round 5's Olipop scene 3 presenter's shirt changed colour mid-clip while the old
# sentence told the judge nothing but the product was its concern.

MOTION_CHECK = """\
You check a short video clip for a product ad before it is shown to the shop owner. You decide \
whether everything in it moves and changes the way it would in real life, and whether it shows \
what the scene was meant to show.

You get frames from the clip, in order, each labelled with its time in seconds, so you can \
follow what moves between them. You also get the scene's order: one sentence saying what the \
clip should show.

Work in two steps.
1. Look before you judge. Fill in the look form from the frames: what is in the clip; how \
everything is at the first frame; then every movement and change of the product, of the hands \
touching it, and of the one thing the action changes (a surface, a liquid, a level, hair, a \
shape), step by step, with the time of the frame where you see it; and how everything is at the \
last frame. Describe what is on the frames, not what the product is for or what usually happens. \
Everything in the frame is your concern, not only the product: write down anything else that \
moves or changes too, and judge it in the questions as well. Anything that changes while nothing \
acts on it is a fault.
2. Then answer three questions, each "pass" or "fail".
- real_motion: does every object and liquid move the way a real one would? Use the facts about \
the product: a thick gel must move like a thick gel, a hard object must stay rigid.
- sudden_change: does the changed thing change only while something acts on it, and only where \
it acts, step by step? Fail when it changes before the action reaches it, where nothing touched \
it, or all at once between two frames; or when the product itself switches shape or state \
between frames.
- does_what_was_ordered: does the clip do the main action of the order with the product? Fail \
only when that main action is missing, a different main action is done with the product, or the \
person, hand or object the order names is not in the clip. A camera move, a close-up, the \
framing, the pace, or something the order says should be "in view" or "emphasized" is never a \
fail. Small extra movements around the main action, walking, posing or holding are not a fail.

Decide "fail" only when you are sure from the frames. When a question passes, its problem is \
null. When it fails, write up to 3 sentences: what went wrong and with which part, the second it \
starts, and what a real one would do.

You may also be shown one shop photo of the product, and facts about it from the shop. They tell \
you what the product is, what it is made of, what is inside and how it is built. Use them only \
for that. Never compare the frames with the photo: differences in size, angle, colour, label or \
print are not a fail."""


class Moment(BaseModel):
    time: float = Field(description="Seconds, the time of the frame where it is seen.")
    what_happens: str


class ClipLook(BaseModel):
    in_the_clip: str = Field(
        description="The product, the hands touching it, the thing the action changes, where "
        "each is."
    )
    start: str = Field(
        description="How everything is at the first frame: what is held, where, which way it faces."
    )
    moments: list[Moment]
    end: str = Field(description="How everything is at the last frame.")


class Check(BaseModel):
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: up to 3 sentences: what went wrong and with which part, "
        "the second it starts, and what a real one would do. Null when it passes."
    )
    back_to_normal_at: str | None = Field(description=BACK_TO_NORMAL)


class MotionVerdict(BaseModel):
    look: ClipLook
    ordered_action: str = Field(
        description="The main action the order asks for, in your words, without camera or "
        "framing words."
    )
    seen_action: str = Field(description="The main action the clip actually does with the product.")
    real_motion: Check
    sudden_change: Check
    does_what_was_ordered: Check


MOTION_QUESTIONS = ("real_motion", "sudden_change", "does_what_was_ordered")


# ---------------------------------------------------------------- a fix's clip: one more question

# The clip fix check is this judge, never a judge of its own (Julian 2026-10-14 19:27-19:29,
# decisions/spec-fix-check-shares-judge.md): a clip made again for the shop owner's clip fix
# is judged as any clip, and each MOTION ask is also shown the clip they complained about,
# told their change and asked this question. Its answer never fails the clip: it is the fix
# check's verdict alone. Wording: Julian's yes, 2026-10-14 21:44.
MOTION_FIX_QUESTION = """\
This clip was made again because the shop owner asked for a change, given to you word for \
word as the owner's change. Before the new clip's frames you are shown frames of the old \
clip, the one the owner complained about. Answer one more question, owner_change: is the \
change clearly there in the new clip? Compare the old clip with the new one only for that \
change. Nothing else about the old clip matters, and other differences between the two clips \
are not a fail. Answer "fixed" only if the change is clearly made. Answer "not fixed" if what \
the owner complained about is still there, or the change is only partly made, and say in one \
sentence what is still wrong."""


# The answer to that question, filled in from the top: it looks at both clips before it
# decides (vision-judges.md, rule 1). The picture check's FixVerdict in clip words, kept apart
# on purpose: it has no rule that a "not fixed" says why, since a form that breaks a rule
# loses the whole MOTION answer, the clip's own checks with it (review F1). owner_change_answers
# drops such an answer instead.
class FixAnswer(BaseModel):
    old_shows: str = Field(description="What the old clip does about the owner's change.")
    new_shows: str = Field(description="What the new clip does about the owner's change.")
    verdict: Literal["fixed", "not fixed"]
    still_wrong: str | None = Field(
        description="When it isn't fixed: what is still wrong, in one sentence. Null when it is."
    )


class MotionFixVerdict(MotionVerdict):
    owner_change: FixAnswer


# For a clip fix that takes things from the shop owner's photos (Julian 2026-10-15 19:34, the
# one exception to the freeze): MOTION is also shown each photo, named, and looks at it first.
# Every other clip is judged as before, and this never counts in the clip's verdict.
MOTION_PHOTO_FIX_QUESTION = """\
The shop owner also sent photos of their own things for this change, named in \
"owner_photos" in the order shown, after the shop photos. In owner_change, before anything \
else, write what the shop owner's photos show about each named thing. Each named thing in \
the new clip should look like it does in its photo, whatever the old clip or the shop photos \
show; one that doesn't is not fixed."""

OWNER_PHOTO_LABEL = "The shop owner's photo of their {}:"


# FixAnswer with what the shop owner's photos show first.
class PhotoFixAnswer(BaseModel):
    owner_photo_shows: str = Field(
        description="What the shop owner's photos show about each named thing."
    )
    old_shows: str = Field(description="What the old clip does about the owner's change.")
    new_shows: str = Field(description="What the new clip does about the owner's change.")
    verdict: Literal["fixed", "not fixed"]
    still_wrong: str | None = Field(
        description="When it isn't fixed: what is still wrong, in one sentence. Null when it is."
    )


class MotionPhotoFixVerdict(MotionVerdict):
    owner_change: PhotoFixAnswer


# ---------------------------------------------------------------- a clip with its sent list

# A clip that carries the sent list its video API got (#210 point 18, #228) is judged against
# exactly that: the sent pictures, each piece's frames and the prompt, then the clip. Its words
# are the judge build brief's (handoffs/judge-build/brief.md, section 3), word for word from the
# hand test's harness (runs/hand-rounds/judge-test/run.py): PRODUCT replaces SIDES, and MOTION
# keeps today's words but for its last paragraph. A clip with no sent list is judged as above.

PRODUCT_CHECK = """\
You check an AI-made video clip before it is used in a product ad. The video API that made the \
clip was given reference pictures of the product, sometimes a reference video of the real \
product in use, and a prompt. You are given exactly the same, and then the clip as frames in \
order, each labelled with its time in seconds. Your one question: is the product in the clip \
the real product, and does it behave the way the real product behaves?

Work in three steps.
1. Learn the product from the references. From the pictures and the video frames write down: \
its parts and where each sits; each side you can see and what is on that side; how many of it \
the scene has; how it is held; and, from the video frames, what moves when it is used and what \
comes out or changes, in what shape and amount. Write only what the references show. Also \
write what the references never show (a side, a state, an action), because you cannot know \
what a real one looks like there.
2. Look at the clip, frame by frame. For each frame write what the product shows and what it \
is doing, and whether anything in it differs from the references: a part a real one does not \
have, a part on a side where the real one has none, a side that is not the reference's side, a \
second copy of the product where the scene has one, a part that appears or vanishes between \
frames, the product changing shape, state or hands between frames, or an action or output \
unlike the real one's.
3. Decide.
- "fail" when you are sure, from the references, that the product on screen is not the real \
product or does not behave like it. Name the fault in product terms, with the reference you \
compared against and the frame times, so that someone who has not watched the clip can fix it.
- "unsure" when the clip shows a side, a state or an action that the references never show, so \
you cannot know what a real one looks like there. Say what the references never show. Unsure \
is not a fail: the shop owner decides.
- "pass" when every part of the product you can see matches the references and it behaves like \
the real one.
Differences in colour, size, angle, distance, lighting, background, or small print on a label \
are not a fault by themselves. The person, the place and the camera are not your question."""


# A comment, not a docstring, for the same reason as SidesVerdict's.
class ClipFrame(BaseModel):
    time: float = Field(description="The frame's time in seconds, as labelled.")
    product_shows: str = Field(
        description="What of the product shows and what it is doing, in a few words."
    )
    differs: str = Field(description="What differs from the references in this frame, or empty.")


class ProductVerdict(BaseModel):
    product_from_references: str = Field(
        description="The real product as the references show it: parts, sides, count, how it "
        "is held, how it works, what comes out or changes. Then what the references never show."
    )
    frames: list[ClipFrame]
    verdict: Literal["pass", "fail", "unsure"]
    compared_against: str | None = Field(
        description="On a fail: the reference you compared against, e.g. 'Image 2' or 'Video 1 "
        "at 5 s'. Null otherwise."
    )
    clip_shows: str | None = Field(
        # Changed from the brief's words (#228 review M1): its example was a past failed case
        # (CODING_STANDARDS.md), now a general one.
        description="On a fail: what the clip shows and at which second, e.g. 'a part on a side "
        "where the real one has none, at 2.3 s'. Null otherwise."
    )
    real_product_shows: str | None = Field(
        description="On a fail: what the real product shows there, from the reference. Null "
        "otherwise."
    )
    fault_frame_times: list[float] = Field(
        description="On a fail: the frame times where the fault shows. Empty otherwise."
    )
    fault: str | None = Field(
        description="On a fail: the fault in product terms, one or two sentences, for the "
        "person who fixes it. Null otherwise."
    )
    references_never_show: str | None = Field(
        description="On unsure: the side, state or action the clip shows that the references "
        "never show. Null otherwise."
    )


# MOTION for a clip with its sent list: today's words, the paragraph on the shop photo
# replaced (requirement 14; the brief's MOTION_NEW).
MOTION_SHOP_PHOTO_PARAGRAPH = (
    "You may also be shown one shop photo of the product, and facts about it from the shop. They "
    "tell you what the product is, what it is made of, what is inside and how it is built. Use "
    "them only for that. Never compare the frames with the photo: differences in size, angle, "
    "colour, label or print are not a fail."
)
assert MOTION_CHECK.count(MOTION_SHOP_PHOTO_PARAGRAPH) == 1
MOTION_FROM_REFERENCES = MOTION_CHECK.replace(
    MOTION_SHOP_PHOTO_PARAGRAPH,
    "Before the clip you are shown what the video API that made the clip was given: the "
    "reference pictures, each labelled as in the prompt, frames from the reference video when "
    "there was one, and the prompt itself. They tell you what the product is, what it is made "
    "of, how it is built and how a real one moves and behaves. Use them to know how a real one "
    "moves.",
)

# Said beside the planner's note on how this product behaves (#210 point 18, spec 2 item 28,
# Julian Oct 28 22:38). Claude's wording: the brief didn't carry a line for the note when the
# judge was built, and has carried this one, word for word, since Oct 29 04:19.
BEHAVIOUR_FRAME = (
    "This is a note on how this product behaves, written from the shop's own page and what its "
    "videos show. Use it, with the references, to know how a real one looks, moves and behaves."
)

# The judge's first question (#210 point 18, spec 2 item 27, Julian Oct 28 23:16): do the
# references and the behaviour note show enough to judge this clip's action? Claude's wording
# (the brief has none for it), for Julian's yes; the logic is his.
REFERENCES_QUESTION = """\
You look at an AI-made video clip before it is judged for a product ad. The video API that \
made the clip was given reference pictures of the product, sometimes a reference video of the \
real product in use, and a prompt. You are given exactly the same, a note on how this product \
behaves, the scene's order, and then the clip as frames in order, each labelled with its time \
in seconds. Your one question: do the references and the note show enough of the real product \
to judge what the clip does with it?

Work in two steps.
1. Write what the references and the note show of the real product: its sides, its states, and \
how it moves and what it does when it is used. Then write what the clip shows the product doing, \
and each side and state of the product it shows, with the frame times.
2. Decide.
- "enough" when the references or the note show each side and state of the product the clip \
shows, and show or say how the product behaves in the clip's action, so that someone could tell \
from them whether the clip is right.
- "cannot tell" when the clip shows a side, a state or an action of the product that neither the \
references nor the note show, so nobody could know from them what a real one looks like or does \
there. Say what they never show, in a few words that finish the sentence "the references never \
show ...".
You do not judge whether the clip is right. The person, the place and the camera are not your \
question."""


class ReferencesVerdict(BaseModel):
    references_show: str = Field(
        description="What the references and the note show of the real product: its sides, its "
        "states, how it moves and what it does."
    )
    clip_shows: str = Field(
        description="What the clip shows the product doing, and each side and state of it, "
        "with the frame times."
    )
    verdict: Literal["enough", "cannot tell"]
    references_never_show: str | None = Field(
        description="On cannot tell: what the references and the note never show, in a few "
        'words that finish "the references never show ...". Null otherwise.'
    )

    @model_validator(mode="after")
    def _cannot_tell_says_what(self) -> Self:
        if self.verdict == "cannot tell" and not (self.references_never_show or "").strip():
            raise ValueError("A clip it can't tell about must say what the references never show.")
        return self


class SentClipCheckHandoff(Handoff):
    """Which of a check's asks about one clip this is, from 1; the product; what the clip's
    video API was sent: its prompt word for word and a line for each picture and video, in the
    order shown; how long the clip is; and the planner's note on how this product behaves
    (#210 point 18). The judge build brief's handoff, with the note."""

    ask: int
    product: str
    prompt_sent_to_the_video_api: str
    references: list[str]
    clip_seconds: float | None
    about_how_it_behaves: str = BEHAVIOUR_FRAME
    how_this_product_behaves: str


class SentMotionCheckHandoff(SentClipCheckHandoff):
    """And the scene's order: what the clip should show."""

    order: str


class SentMotionFixCheckHandoff(SentMotionCheckHandoff):
    """And, for a clip made again for the shop owner's clip fix, their change word for word."""

    owner_change: str


class SentMotionPhotoFixCheckHandoff(SentMotionFixCheckHandoff):
    """And the things the shop owner's photos for the fix are taken for, in the order shown."""

    owner_photos: list[str]


# ---------------------------------------------------------------- the ruling

Verdict = Literal["pass", "fail", "unsure", "not_checked"]

# The first check of a ruling: SIDES for a clip judged with no sent list, PRODUCT for one
# judged against its sent list (#228). A ruling's answers keep each under its own name.
FIRST_CHECKS = ("sides", "product")


def judged(
    first: Sequence[dict[str, Any] | None],
    motion: Sequence[dict[str, Any] | None],
    *,
    first_check: Literal["sides", "product"] = "sides",
    blind: Iterable[tuple[str, int]] = (),
) -> dict[str, Any]:
    """The judge's ruling on a clip from its answers, in the order asked: each a SidesVerdict
    (or, with `first_check` "product", a ProductVerdict) or MotionVerdict as a dict, or None
    for an ask that went unchecked. Each check (SIDES or PRODUCT, and each MOTION question) is
    settled by itself: it fails when an answer fails it; it is unsure when an answer says
    unsure, or when an ask in `blind` (its check and ask) answered without some of the clip's
    frames, which the safety filter refused, and nothing fails it (Julian, Oct 29 00:10); and
    it is not checked when it has no usable answer. The clip fails when any check fails, and
    is unsure when none fails and any is unsure. Every failing answer counts, however soon its
    thing is back to normal: the half-second forgive rule is gone (Julian 2026-10-19 03:37).

    Gives "verdict" (pass, fail, unsure or not_checked), "checks" (each check's verdict),
    "failed_answers" (how many answers failed on anything, of all asked), "failures" (each
    failed answer's check, ask, problems and whole answer) and "answers" (every answer, as
    given)."""
    blinded = set(blind)
    # Each check's name and, by ask, the part of each answer that holds its verdict.
    asked: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = [
        (first_check, [(ask, answer) for ask, answer in enumerate(first, 1) if answer])
    ] + [
        (question, [(ask, answer[question]) for ask, answer in enumerate(motion, 1) if answer])
        for question in MOTION_QUESTIONS
    ]
    checks: dict[str, Verdict] = {}
    problems: dict[tuple[str, int], list[str]] = {}
    for name, usable in asked:
        which = first_check if name == first_check else "motion"
        failing = [(ask, said) for ask, said in usable if said["verdict"] == "fail"]
        for ask, said in failing:
            problems.setdefault((which, ask), []).append(_problem(said))
        # A refused second keeps the check unsure even when no ask could be answered past it.
        unsure = any(said["verdict"] == "unsure" for _, said in usable) or any(
            check == which for check, _ in blinded
        )
        checks[name] = (
            "fail" if failing else "unsure" if unsure else "pass" if usable else "not_checked"
        )
    failures = [
        {
            "check": which,
            "ask": ask,
            "problems": said,
            # Never the answer to a fix's question: it is no fault of the clip (spec, part 2).
            "answer": _without_owner_change((first if which == first_check else motion)[ask - 1]),
        }
        for (which, ask), said in sorted(
            problems.items(), key=lambda item: (item[0][0] != first_check, item[0][1])
        )
    ]
    verdicts = set(checks.values())
    return {
        "verdict": next(
            (verdict for verdict in ("fail", "unsure", "not_checked") if verdict in verdicts),
            "pass",
        ),
        "checks": checks,
        "failed_answers": len(failures),
        "failures": failures,
        "answers": {first_check: first, "motion": motion},
    }


# What a failing answer that names no fault is said to have found, for the fixing agent.
NO_FAULT_NAMED = "The judge failed the clip without saying why: look at the clip's frames."


def _problem(said: dict[str, Any]) -> str:
    """A failing answer's problem: a PRODUCT answer names it its fault, or else says what the
    clip shows. One that says neither is NO_FAULT_NAMED, never "None"."""
    if "fault" not in said:
        return str(said["problem"] or NO_FAULT_NAMED)
    named = str(said["fault"] or "").strip() or str(said["clip_shows"] or "").strip()
    if not named:
        logger.warning("A PRODUCT answer failed a clip without naming its fault")
    return named or NO_FAULT_NAMED


def owner_change_answers(ruling: dict[str, Any]) -> list[dict[str, Any] | None]:
    """Each MOTION answer's answer about the shop owner's change, in the order asked, for the
    fix check to settle: None for an ask that went unchecked, or a "not fixed" that doesn't
    say what is still wrong."""
    said = [answer and answer["owner_change"] for answer in ruling["answers"]["motion"]]
    return [
        None
        if answer is None
        or (answer["verdict"] == "not fixed" and not (answer["still_wrong"] or "").strip())
        else answer
        for answer in said
    ]


def _without_owner_change(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    return answer and {key: said for key, said in answer.items() if key != "owner_change"}


def still_failing(ruling: dict[str, Any]) -> list[str]:
    """The problems behind a ruling's failed checks, SIDES (or PRODUCT) first, each check's in
    the order asked: never one a ruling made before left out (left_out)."""
    left = left_out(ruling)
    answers = ruling.get("answers", {})
    problems = []
    for name in [*FIRST_CHECKS, *MOTION_QUESTIONS]:
        if ruling.get("checks", {}).get(name) != "fail":
            continue
        first = name in FIRST_CHECKS
        for ask, answer in enumerate(answers.get(name if first else "motion", []), 1):
            said = answer and (answer if first else answer[name])
            if said and said["verdict"] == "fail" and (name, ask) not in left:
                problems.append(_problem(said))
    return problems


def left_out(ruling: dict[str, Any]) -> set[tuple[str, int]]:
    """Each check and ask whose failing answer doesn't count in a ruling's failed check: one
    let go by a ruling made before the half-second forgive rule was gone, or, in a ruling made
    while each check was asked 3 times, one whose fault the same-fault vote found no other
    answer naming (its "agreement")."""
    left = {(each["check"], each["ask"]) for each in ruling.get("let_go", [])}
    for name, agreed in ruling.get("agreement", {}).items():
        if "sharing" not in agreed:
            continue
        motion = ruling["answers"]["motion"]
        left |= {
            (name, ask)
            for ask, answer in enumerate(motion, 1)
            if answer and answer[name]["verdict"] == "fail" and ask not in agreed["sharing"]
        }
    return left


# ---------------------------------------------------------------- judging a clip

# A model takes at most this many pictures in one ask (Azure: 400 "Exceeded maximum number of
# images (50)", seen 2026-10-12). When the frames and the shop photo don't fit, frames side by
# side in time order are joined into one picture.
MOST_PICTURES = 50
# How the pictures are labelled for the judge.
SHOP_PHOTO_LABEL = "The shop photo of the product:"
NEEDS_PHOTO_LABEL = "A shop photo of {}:"
FRAMES_LABEL = "Frames from the clip, in order:"
# On a fix's clip, MOTION's frames of each clip.
OLD_FRAMES_LABEL = "Frames from the old clip, the one the owner complained about, in order:"
NEW_FRAMES_LABEL = "Frames from the new clip, in order:"
# At most this many photos of what a scene needs are shown beside the main one (plan.md §3).
NEEDS_PHOTOS_SHOWN = 5


class ClipCheckHandoff(Handoff):
    """Which of a check's asks about one clip this is, from 1; the facts about the product:
    what it is, from the plan and the shop's own text; and the conversation with the shop
    owner, for what the product is and looks like, with, for a fix's clip, what the producer
    restated the owner's notes as asking for (plan.md §3, Julian 2026-10-12 21:19). The number
    only keeps each ask's answer apart, so one asked again after the worker stopped isn't paid
    for twice. The judge never sees the clip's prompts or line."""

    ask: int
    facts: str
    about_the_conversation: str = CONVERSATION_FRAME
    conversation: list[ChatMessage]
    what_the_agent_understood_from_the_owner: str | None


class MotionCheckHandoff(ClipCheckHandoff):
    """And the scene's order: what the clip should show."""

    order: str


class MotionFixCheckHandoff(MotionCheckHandoff):
    """And, for a clip made again for the shop owner's clip fix, their change word for word,
    for the question about it."""

    owner_change: str


class MotionPhotoFixCheckHandoff(MotionFixCheckHandoff):
    """And the things the shop owner's photos for the fix are taken for, in the order shown."""

    owner_photos: list[str]


def complained_about(step: SceneStep) -> ProducedItem | None:
    """For a step that makes a scene's new clip for the shop owner's clip fix, or for a fix
    with their photo of a thing that only shows during the clip, the clip they complained
    about: the one they saw last before the step started. None for any other step, or when
    the scene has no clip from before it."""
    if step.kind != SceneStep.Kind.CLIP:
        return None
    agent_try = AgentFixTry.objects.filter(step=step).select_related("agent_fix__step").first()
    if agent_try is not None:
        # A try the fixing agent films for the shop owner's note is judged beside the clip
        # they noted, try 0, whichever try it is.
        fixing = agent_try.agent_fix
        if fixing.door == AgentFix.Door.OWNER:
            return fixing.first_clip
    return None


def owner_change_of(step: SceneStep) -> str:
    """The shop owner's change a fix's clip is judged for, in one line: for a try the fixing
    agent films for their note (door 2), the goal it was handed (the producer's instruction);
    else the step's own goal."""
    agent_try = AgentFixTry.objects.filter(step=step).select_related("agent_fix").first()
    if agent_try is not None and agent_try.agent_fix.door == AgentFix.Door.OWNER:
        return agent_try.agent_fix.goal
    return step.fix_goal


def judge_clip(
    step: SceneStep, clip: ProducedItem, *, conversation: list[ChatMessage]
) -> dict[str, Any]:
    """Judge the B-roll clip `step` made, keeping the ruling on the clip and giving it: see
    `judged`, plus "wording" (wording_version()), "models" (each check's) and "pictures" (what
    it was shown of the clip). `conversation` is what was said with the shop owner when the
    step started. Its pictures are cut once and kept on the clip before any ask, so a clip
    judged again after the worker stopped is shown the same pictures, and pays for no answer
    twice. A clip that can't be judged at all, for a reason nobody expected, is kept as not
    checked, with "error" saying why, in a ruling of the same shape: the clip is paid for.

    A clip made for the shop owner's clip fix is judged the same way, and each MOTION ask is
    also shown the clip they complained about (complained_about) and asked whether their
    change is made: each answer's "owner_change", which never counts in the verdict. Its
    ruling's "compared_with" is that clip's id. When that clip's frames can't be cut, the
    clip is judged as any other, and the fix is left unchecked.

    A clip that carries its sent list is judged against it instead (_judge_against_sent)."""
    sent = sent_list_of(clip)
    if sent is not None:
        return _judge_against_sent(step, clip, sent)
    answered: list[_Answered] = []
    try:
        sides, motion, old = _asked(step, clip, conversation)
        answered = [*sides, *motion]
        ruling = {
            **_ruling(
                [each.answer for each in sides],
                [each.answer for each in motion],
                clip,
                compared_with=old,
                photos=False,
            ),
            **_past_the_filter(sides, motion),
        }
    except Exception as error:
        logger.exception("Scene %s's clip couldn't be judged", step.scene.number)
        ruling = {
            **_ruling([None] * ASKS, [None] * ASKS, clip),
            "error": str(error),
        }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    _say_what_the_filter_refused(step, answered, ruling["verdict"])
    return ruling


def _judge_against_sent(step: SceneStep, clip: ProducedItem, sent: SentList) -> dict[str, Any]:
    """Judge a B-roll clip that carries `sent`, the sent list its video API got (#210 point
    18), keeping the ruling on the clip and giving it. Every ask is shown the sent pictures in
    their order and each piece's frames (sent_references), then the clip's frames, and told the
    prompt and the planner's note on how this product behaves. The first question
    (REFERENCES_QUESTION) asks whether those show enough to judge the clip's action: when they
    don't, the ruling is "unsure" with "cannot_tell" saying what the references never show,
    neither check is asked, the clip is kept and the shop owner is told to check it themselves.
    Else PRODUCT and MOTION are asked, as judge_clip asks SIDES and MOTION, and an unsure check
    keeps the clip for the shop owner to decide. The ruling also gives "behaviour" (the note it
    was told) and, in "pictures", "references" (what it was shown of the sent list)."""
    answered: list[_Answered] = []
    try:
        ruling, answered = _asked_against_sent(step, clip, sent)
    except Exception as error:
        logger.exception("Scene %s's clip couldn't be judged", step.scene.number)
        ruling = {
            **judged([None] * ASKS, [None] * ASKS, first_check="product"),
            **_sent_ruling_facts(step, clip, None),
            "error": str(error),
        }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    _say_what_the_filter_refused(step, answered, ruling["verdict"])
    _say_the_judge_is_unsure(step, ruling)
    return ruling


def _asked_against_sent(
    step: SceneStep, clip: ProducedItem, sent: SentList
) -> tuple[dict[str, Any], list[_Answered]]:
    """_judge_against_sent's ruling and every answer it was given."""
    pictures: dict[str, Any] | None = (clip.ruling or {}).get("pictures")
    fresh = not pictures or "references" not in pictures
    old = complained_about(step)
    photos: list[dict[str, Any]] = []
    owners = [
        Image(label=OWNER_PHOTO_LABEL.format(each["thing"]), key=str(each["file"]))
        for each in photos
    ]
    if fresh:
        references = sent_references(sent)
        beside = len(references)
        beside_each_clip = MOST_PICTURES - (MOST_PICTURES - beside - len(owners)) // 2
        old_frames = _frames_beside(old, beside=beside_each_clip, cut=True) if old else None
        pictures = {
            **cut_pictures(clip.file, beside=beside, sets=("sides",)),
            **cut_pictures(
                clip.file, beside=beside_each_clip if old_frames else beside, sets=("frames",)
            ),
            "references": references,
        }
    else:
        assert pictures is not None
        beside_each_clip = (
            MOST_PICTURES - (MOST_PICTURES - len(pictures["references"]) - len(owners)) // 2
        )
        old_frames = _frames_beside(old, beside=beside_each_clip, cut=False) if old else None
    assert pictures is not None
    if old is not None and old_frames is None:
        logger.warning("Scene %s's fix is judged without the old clip", step.scene.number)
    clip.ruling = {"pictures": pictures}
    clip.save(update_fields=["ruling"])
    shown = [Image(label=each["label"], key=each["key"]) for each in pictures["references"]]
    job = step.scene.job
    told: dict[str, Any] = {
        "product": job.product_name,
        "prompt_sent_to_the_video_api": sent.prompt,
        "references": sent_said(sent),
        "clip_seconds": clip.seconds,
        "how_this_product_behaves": job.product_behaviour,
        "order": step.shows,
    }
    fixing = old_frames is not None
    facts = _sent_ruling_facts(step, clip, old if fixing else None)

    asking_first = _Ask(
        "check_clip_references",
        REFERENCES_QUESTION,
        SentMotionCheckHandoff(ask=1, **told),
        ReferencesVerdict,
        [*shown, *_frames_shown(pictures["frames"])],
        references=0,
    )
    first = _answer(step, asking_first)
    if first.unusable:
        # An answer that broke its form (a "cannot tell" that doesn't say what is never
        # shown) is asked for once more.
        first = _answer(step, asking_first)
    if first.answer is None:
        post_notice(
            job,
            f"The clip judge couldn't ask whether scene {step.scene.number}'s references show "
            "enough to judge its clip, so it judged the clip on its two checks alone.",
            Message.Level.PROBLEM,
        )
    elif first.answer["verdict"] == "cannot tell":
        return {
            "verdict": "unsure",
            "checks": {},
            "failed_answers": 0,
            "failures": [],
            "answers": {"references": first.answer, "product": [], "motion": []},
            "cannot_tell": first.answer["references_never_show"],
            **facts,
            # Only the first question was asked, so the version is the plain one.
            "wording": wording_version(sent=True),
        }, [first]

    with_photos = fixing and bool(owners)
    motion_shown = (
        [*shown, *owners, *_frames_shown(pictures["frames"], old=old_frames)]
        if with_photos
        else [*shown, *_frames_shown(pictures["frames"], old=old_frames)]
    )
    asks = [
        _Ask(
            "check_clip_product",
            PRODUCT_CHECK,
            SentClipCheckHandoff(ask=ask, **{k: v for k, v in told.items() if k != "order"}),
            ProductVerdict,
            [*shown, *_frames_shown(pictures["sides"])],
            references=len(shown),
            frames=_times(pictures["sides"]),
        )
        for ask in range(1, ASKS + 1)
    ] + [
        _Ask(
            "check_clip_motion",
            f"{MOTION_FROM_REFERENCES}\n\n{MOTION_FIX_QUESTION}\n\n{MOTION_PHOTO_FIX_QUESTION}"
            if with_photos
            else f"{MOTION_FROM_REFERENCES}\n\n{MOTION_FIX_QUESTION}"
            if fixing
            else MOTION_FROM_REFERENCES,
            SentMotionPhotoFixCheckHandoff(
                ask=ask,
                **told,
                owner_change=owner_change_of(step),
                owner_photos=[str(each["thing"]) for each in photos],
            )
            if with_photos
            else SentMotionFixCheckHandoff(ask=ask, **told, owner_change=owner_change_of(step))
            if fixing
            else SentMotionCheckHandoff(ask=ask, **told),
            MotionPhotoFixVerdict if with_photos else MotionFixVerdict if fixing else MotionVerdict,
            motion_shown,
            references=len(shown) + (len(owners) if with_photos else 0),
            frames=_times(pictures["frames"]),
        )
        for ask in range(1, ASKS + 1)
    ]
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        asked = [
            pool.submit(contextvars.copy_context().run, closing_its_connection, _answer, step, each)
            for each in asks
        ]
        answers = [each.result() for each in asked]
    product, motion = answers[:ASKS], answers[ASKS:]
    blinded = [
        (check, ask, each.blind)
        for check, said in (("product", product), ("motion", motion))
        for ask, each in enumerate(said, 1)
        if each.blind
    ]
    blind = [
        {"check": check, "ask": ask, "seconds": list(seconds)} for check, ask, seconds in blinded
    ]
    ruling = {
        **judged(
            [each.answer for each in product],
            [each.answer for each in motion],
            first_check="product",
            blind=[(check, ask) for check, ask, _ in blinded],
        ),
        **facts,
        **_past_the_filter(product, motion, first_check="product"),
        **({"refused_frames": blind} if blind else {}),
    }
    ruling["answers"]["references"] = first.answer
    # Judged in the photo form only while a shop owner's photo was still shown.
    ruling["wording"] = wording_version(
        sent=True,
        fix=fixing,
        photos=with_photos and not all(each.without_owner_photos for each in motion),
    )
    # The first question's own refusal is told by the notice above, not as the clip's.
    return ruling, answers


def _sent_ruling_facts(
    step: SceneStep, clip: ProducedItem, compared_with: ProducedItem | None
) -> dict[str, Any]:
    """What a ruling against a sent list says beside its verdict: its words and models, the
    pictures it was shown, the behaviour note it was told and, for a fix's clip, the clip it
    was compared with."""
    return {
        "wording": wording_version(
            sent=True,
            fix=compared_with is not None,
            photos=False,
        ),
        "models": {
            purpose.removeprefix("check_clip_"): catalog.MODEL_FOR_PURPOSE[purpose]
            for purpose in ("check_clip_references", "check_clip_product", "check_clip_motion")
        },
        "pictures": (clip.ruling or {}).get("pictures"),
        "behaviour": step.scene.job.product_behaviour,
        **({"compared_with": compared_with.pk} if compared_with is not None else {}),
    }


def _say_the_judge_is_unsure(step: SceneStep, ruling: dict[str, Any]) -> None:
    """Tell the chat, once, when the judge couldn't tell whether a clip is right because the
    references never show what it shows: the clip is kept, and the shop owner decides
    (#210 point 18). Only for a clip the ruling keeps as unsure: a clip it failed goes to the
    fixing agent. Seconds of the clip the safety filter refused are told in red, as every
    refusal is, said to be kept only when the clip is."""
    number = step.scene.number
    job = step.scene.job
    kept = ruling["verdict"] == "unsure"
    never: list[str | None] = [ruling["cannot_tell"]] if "cannot_tell" in ruling else []
    if ruling.get("checks", {}).get("product") == "unsure":
        never += [
            answer["references_never_show"]
            for answer in ruling["answers"]["product"]
            if answer and answer["verdict"] == "unsure"
        ]
    for what in dict.fromkeys(never) if kept else ():
        said = (what or "").strip().rstrip(".")
        if not said:
            # The judge said it can't tell without saying what is never shown.
            logger.warning("Scene %s's judge was unsure without saying why", number)
        text = (
            f"The clip judge can't tell whether scene {number}'s clip is right: the references "
            f"never show {said}, check this one yourself."
            if said
            else f"The clip judge can't tell whether scene {number}'s clip is right, check "
            "this one yourself."
        )
        if all(warning["text"] != text for warning in job.warnings):
            post_notice(job, text, Message.Level.INFO)
    seconds = sorted({t for each in ruling.get("refused_frames", []) for t in each["seconds"]})
    if seconds:
        post_refused_by_the_filter(
            job,
            f"scene {number}'s clip at {', '.join(f'{t:g} s' for t in seconds)}",
            "the clip judge couldn't check those seconds and the clip is kept for you to "
            "decide: check this one yourself"
            if kept
            else "the clip judge couldn't check those seconds",
        )


def _times(pictures: list[dict[str, Any]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(picture["times"]) for picture in pictures)


def _past_the_filter(
    sides: list[_Answered], motion: list[_Answered], *, first_check: str = "sides"
) -> dict[str, Any]:
    """What a ruling says of the safety filter (requirement 22), when it refused anything:
    "not_shown", each ask answered without reference pictures it refused, and which;
    "refused_by_the_filter", each ask it refused over the clip itself, unchecked."""
    asked = [(first_check, ask, each) for ask, each in enumerate(sides, 1)] + [
        ("motion", ask, each) for ask, each in enumerate(motion, 1)
    ]
    not_shown = [
        {"check": check, "ask": ask, "pictures": each.not_shown}
        for check, ask, each in asked
        if each.not_shown
    ]
    refused = [{"check": check, "ask": ask} for check, ask, each in asked if each.refused]
    return {
        **({"not_shown": not_shown} if not_shown else {}),
        **({"refused_by_the_filter": refused} if refused else {}),
    }


def _say_what_the_filter_refused(
    step: SceneStep, answered: list[_Answered], verdict: Verdict
) -> None:
    """Tell the chat in red of every picture the safety filter refused when the clip was
    judged: the reference pictures it was judged without, and the clip itself, when the
    filter refused to judge it at all; kept as it is only when the ruling, `verdict`, didn't
    fail it on the checks that were made."""
    job, number = step.scene.job, step.scene.number
    left = list(dict.fromkeys(name for each in answered for name in each.not_shown))
    if left:
        post_left_out_by_the_filter(job, left, f"scene {number}'s clip was judged")
    if any(each.refused for each in answered):
        post_refused_by_the_filter(
            job,
            f"scene {number}'s clip",
            "the clip judge couldn't check all of it"
            if verdict == "fail"
            else "the clip judge couldn't check all of it and it is kept as it is: check it "
            "yourself",
        )


def _ruling(
    sides: list[dict[str, Any] | None],
    motion: list[dict[str, Any] | None],
    clip: ProducedItem,
    *,
    compared_with: ProducedItem | None = None,
    photos: bool = False,
) -> dict[str, Any]:
    return {
        **judged(sides, motion),
        "wording": wording_version(fix=compared_with is not None, photos=photos),
        "models": {
            "sides": catalog.MODEL_FOR_PURPOSE["check_clip_sides"],
            "motion": catalog.MODEL_FOR_PURPOSE["check_clip_motion"],
        },
        "pictures": (clip.ruling or {}).get("pictures"),
        **({"compared_with": compared_with.pk} if compared_with is not None else {}),
    }


def _asked(
    step: SceneStep, clip: ProducedItem, conversation: list[ChatMessage]
) -> tuple[list[_Answered], list[_Answered], ProducedItem | None]:
    """Each check's answers about `clip`, in the order asked, all asked at once, and, for a
    fix's clip, the clip MOTION was shown beside it, if any."""
    planned = step.picture_step
    assert planned is not None and planned.photo is not None, (
        "a B-roll clip is made from a starting picture drawn from its main photo"
    )
    photo = planned.photo.file
    needs = _needs_photos(step)
    beside = 1 + len(needs)
    pictures = (clip.ruling or {}).get("pictures")
    old = complained_about(step)
    # A clip fix with the shop owner's photos has MOTION shown them too.
    photos: list[dict[str, Any]] = []
    owners = [
        Image(label=OWNER_PHOTO_LABEL.format(each["thing"]), key=str(each["file"]))
        for each in photos
    ]
    # MOTION shows a fix's clip beside the old one: each gets half the room, so each clip's
    # frames have this many other pictures beside them.
    beside_each_clip = MOST_PICTURES - (MOST_PICTURES - beside - len(owners)) // 2
    # None yet, cut before SIDES went to SIDES_FRAMES_PER_SECOND (then "stills"), or cut by
    # the old fix check, which kept only "frames".
    fresh = not pictures or pictures.keys() != {"sides", "frames"}
    # The old clip's frames are cut only with the new clip's, so both are cut to share.
    old_frames = (
        _frames_beside(old, beside=beside_each_clip, cut=fresh) if old is not None else None
    )
    if fresh and old_frames is None:
        pictures = cut_pictures(clip.file, beside=beside)
    elif fresh:
        pictures = cut_pictures(clip.file, beside=beside, sets=("sides",)) | cut_pictures(
            clip.file, beside=beside_each_clip, sets=("frames",)
        )
    assert pictures is not None
    if old is not None and old_frames is None:
        logger.warning("Scene %s's fix is judged without the old clip", step.scene.number)
    clip.ruling = {"pictures": pictures}
    clip.save(update_fields=["ruling"])
    owner: dict[str, Any] = {
        "conversation": conversation,
        "what_the_agent_understood_from_the_owner": (
            owner_change_of(step) if old is not None else None
        ),
    }
    fixing = old_frames is not None
    with_photos = fixing and bool(owners)
    motion = [
        _Ask(
            "check_clip_motion",
            f"{MOTION_CHECK}\n\n{MOTION_FIX_QUESTION}\n\n{MOTION_PHOTO_FIX_QUESTION}"
            if with_photos
            else f"{MOTION_CHECK}\n\n{MOTION_FIX_QUESTION}"
            if fixing
            else MOTION_CHECK,
            MotionPhotoFixCheckHandoff(
                **told,
                owner_change=owner_change_of(step),
                owner_photos=[str(each["thing"]) for each in photos],
            )
            if with_photos
            else MotionFixCheckHandoff(**told, owner_change=owner_change_of(step))
            if fixing
            else MotionCheckHandoff(**told),
            MotionPhotoFixVerdict if with_photos else MotionFixVerdict if fixing else MotionVerdict,
            _shown(
                photo,
                [*needs, *owners] if with_photos else needs,
                pictures["frames"],
                old=old_frames,
            ),
            references=beside + len(owners) if with_photos else beside,
        )
        for told in (
            {"ask": ask, "facts": _facts(step, with_use=True), "order": step.shows, **owner}
            for ask in range(1, ASKS + 1)
        )
    ]
    asks = [
        _Ask(
            "check_clip_sides",
            SIDES_CHECK,
            ClipCheckHandoff(ask=ask, facts=_facts(step, with_use=False), **owner),
            SidesVerdict,
            _shown(photo, needs, pictures["sides"]),
            references=beside,
        )
        for ask in range(1, ASKS + 1)
    ] + motion
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        asked = [
            pool.submit(contextvars.copy_context().run, closing_its_connection, _answer, step, each)
            for each in asks
        ]
        answers = [each.result() for each in asked]
    return answers[:ASKS], answers[ASKS:], old if fixing else None


def _frames_beside(old: ProducedItem, *, beside: int, cut: bool) -> list[dict[str, Any]] | None:
    """MOTION's frames of the clip `old`, to show before a fix's clip, as few pictures as fit
    beside `beside` others: those kept on `old`'s ruling, so a judge run again after the
    worker stopped shows the same pictures, or else, when `cut`, cut now and kept there.
    None when there are none: not kept and not to be cut, or they can't be cut."""
    kept = (old.ruling or {}).get("pictures_beside")
    if kept and kept["beside"] == beside:
        return kept["frames"]  # type: ignore[no-any-return]
    if not cut:
        return None
    try:
        frames = cut_pictures(old.file, beside=beside, sets=("frames",))["frames"]
    except Exception:
        logger.warning("The old clip %s's frames couldn't be cut", old.pk, exc_info=True)
        return None
    old.ruling = {**(old.ruling or {}), "pictures_beside": {"beside": beside, "frames": frames}}
    old.save(update_fields=["ruling"])
    return frames


@dataclass(frozen=True)
class _Ask:
    """One ask of one check: what it is asked for, told, handed and shown, and answers as.
    The first `references` pictures shown are the reference pictures beside the clip: the
    shop photo, the photos of what the scene needs and the shop owner's photos."""

    purpose: str
    instructions: str
    handoff: Handoff
    output: type[BaseModel]
    images: list[Image]
    references: int
    # For a clip judged against its sent list (#228), the time of each frame in each of the
    # last pictures shown, the clip's own: a picture of them the safety filter refuses is
    # left out, and the check is unsure for those seconds (Julian, Oct 29 00:10).
    frames: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True)
class _Answered:
    """One ask's answer, or None when none could be had; the reference pictures the safety
    filter refused, which it was answered without, by name; and whether the filter refused
    it even without them, over the clip itself."""

    answer: dict[str, Any] | None
    not_shown: list[str]
    refused: bool = False
    # Asked as a fix with no shop owner's photo, as the filter refused every one of them.
    without_owner_photos: bool = False
    # The seconds of the clip it was answered without, the filter refusing them.
    blind: tuple[float, ...] = ()
    # Whether the answer came but broke its form.
    unusable: bool = False


def _answer(step: SceneStep, ask: _Ask) -> _Answered:
    """One ask's answer, or None when it can't be had, for any reason: the clip is paid for,
    and the other asks' answers still count. A reference picture the safety filter refuses
    is left out and the ask made again without it (requirement 22); the clip's own frames
    never are, as a check that didn't see part of the clip can't pass it."""
    references = ask.images[: ask.references]

    def asking(shown: list[Image]) -> tuple[dict[str, Any], bool]:
        instructions, handoff, output = _as_shown(ask, shown)
        answer: BaseModel = call_model(
            job=step.scene.job,
            purpose=ask.purpose,
            instructions=instructions,
            handoff=handoff,
            output=output,
            images=shown,
            pay_once=True,
        )
        return answer.model_dump(mode="json"), output is not ask.output

    try:
        try:
            (answer, without), left_out = past_the_safety_filter(
                asking, ask.images, may_leave_out=lambda image: image in references
            )
        except BlockedBySafetyFilter:
            if not ask.frames:
                raise
            return _past_refused_frames(asking, ask)
        return _Answered(answer, [named(image) for image in left_out], without_owner_photos=without)
    except Exception as error:
        logger.warning(
            "%s ask %s about scene %s's clip went unchecked: %s",
            ask.purpose,
            getattr(ask.handoff, "ask", None),
            step.scene.number,
            error,
            # Expected when a model or the service misbehaves; anything else is a bug to see.
            exc_info=not isinstance(
                error, (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter, AnswerTookTooLong)
            ),
        )
        return _Answered(
            None,
            [],
            refused=isinstance(error, BlockedBySafetyFilter),
            unusable=isinstance(error, UnusableReply),
        )


def _past_refused_frames(
    asking: Callable[[list[Image]], tuple[dict[str, Any], bool]], ask: _Ask
) -> _Answered:
    """`ask` answered without the pictures of the clip's frames the safety filter refuses,
    found by halving (refused_by_halving), each half shown beside the rest; then the ask is
    made once on every frame not refused. Gives the seconds it didn't see, so the check is
    unsure for them (Julian, Oct 29 00:10), and no answer when even that last ask is refused:
    the check is still unsure, never unchecked, for a filter that refused what it was shown of
    the clip."""
    rest = ask.images[: len(ask.images) - len(ask.frames)]
    clip = ask.images[len(rest) :]
    times = dict(zip((image.key for image in clip), ask.frames, strict=True))
    refused = refused_by_halving(lambda group: refuses(asking, [*rest, *group]), clip)
    # Every half passing alone, though all of them together were refused: none can be named.
    blind = refused or clip
    shown = [image for image in clip if image not in blind]
    answer: dict[str, Any] | None = None
    without = False
    if shown:
        try:
            answer, without = asking([*rest, *shown])
        except BlockedBySafetyFilter:
            blind = clip
    if answer is None:
        logger.warning("%s couldn't be asked past the clip frames refused", ask.purpose)
    return _Answered(
        answer,
        [],
        without_owner_photos=without,
        blind=tuple(sorted(t for image in blind for t in times[image.key])),
    )


def _as_shown(ask: _Ask, shown: list[Image]) -> tuple[str, Handoff, type[BaseModel]]:
    """What `ask` is told, handed and answers as when it is shown `shown`: a fix's ask with
    the shop owner's photos names only those still shown, and with none of them left, is
    asked as a fix without photos is."""
    handoff = ask.handoff
    if not isinstance(handoff, (MotionPhotoFixCheckHandoff, SentMotionPhotoFixCheckHandoff)):
        return ask.instructions, handoff, ask.output
    labels = {image.label for image in shown}
    kept = [thing for thing in handoff.owner_photos if OWNER_PHOTO_LABEL.format(thing) in labels]
    if kept:
        return ask.instructions, handoff.model_copy(update={"owner_photos": kept}), ask.output
    unphotographed = handoff.model_dump(exclude={"owner_photos"})
    if isinstance(handoff, SentMotionPhotoFixCheckHandoff):
        return (
            f"{MOTION_FROM_REFERENCES}\n\n{MOTION_FIX_QUESTION}",
            SentMotionFixCheckHandoff(**unphotographed),
            MotionFixVerdict,
        )
    return (
        f"{MOTION_CHECK}\n\n{MOTION_FIX_QUESTION}",
        MotionFixCheckHandoff(**unphotographed),
        MotionFixVerdict,
    )


def named(image: Image) -> str:
    """A reference picture as a notice names it: its label, as words in a sentence."""
    label = image.label.rstrip(":")
    return label[:1].lower() + label[1:]


def _facts(step: SceneStep, *, with_use: bool) -> str:
    """The facts about the product the judge is told (context_text in the harness's
    prompts.py, with the colour and size the app knows too): its name, colour and size; for
    MOTION, how the scene uses it and what that changes; the parts the scene needs; and the
    shop's own text."""
    job = step.scene.job
    lines = [f"Product: {job.product_name}"]
    if job.product_colour:
        lines.append(f"Colour: {job.product_colour}")
    if job.product_size:
        lines.append(f"Size: {job.product_size}")
    if with_use and step.usage:
        lines.append(f"How the product is used in this scene: {step.usage}")
    if with_use and step.broll_result:
        lines.append(f"What that use changes: {step.broll_result}")
    needs = "; ".join(need["what"] for need in step.needs if need.get("what"))
    if needs:
        lines.append(f"Parts of the product this scene needs to show: {needs}")
    if job.page_text:
        lines.append(f"\n{PAGE_FRAME}\n<shop text>\n{job.page_text}\n</shop text>")
    return "\n".join(lines)


def _needs_photos(step: SceneStep) -> list[Image]:
    """A shop photo of each thing the scene needs that the main photo can't show, chosen as
    for its starting picture, at most NEEDS_PHOTOS_SHOWN, labelled with what it shows."""
    photos = {photo.position: photo for photo in step.scene.job.photos.all()}
    faces = {position: photo.has_face for position, photo in photos.items()}
    return [
        Image(label=NEEDS_PHOTO_LABEL.format(" and ".join(need.what)), key=photos[need.photo].file)
        for need in photos_for_needs(step.needs, faces, NEEDS_PHOTOS_SHOWN)
    ]


def _shown(
    photo: str,
    needs: list[Image],
    pictures: list[dict[str, Any]],
    *,
    old: list[dict[str, Any]] | None = None,
) -> list[Image]:
    """The shop photo, the photos of what the scene needs, then the pictures of the clip in
    order, each labelled with the time of each frame in it; with `old`, the pictures of the
    clip a fix was made for come before them."""
    return [Image(label=SHOP_PHOTO_LABEL, key=photo), *needs, *_frames_shown(pictures, old=old)]


def _frames_shown(
    pictures: list[dict[str, Any]], *, old: list[dict[str, Any]] | None = None
) -> list[Image]:
    """The pictures of the clip in order, each labelled with the time of each frame in it;
    with `old`, the pictures of the clip a fix was made for come before them."""
    shown: list[Image] = []
    clips = (
        [(FRAMES_LABEL, pictures)]
        if old is None
        else [
            (OLD_FRAMES_LABEL, old),
            (NEW_FRAMES_LABEL, pictures),
        ]
    )
    for heading, each in clips:
        for n, picture in enumerate(each):
            label = frame_label(picture["times"])
            if n == 0:
                label = f"{heading}\n{label}"
            shown.append(Image(label=label, key=picture["key"]))
    return shown


def frame_label(times: list[float]) -> str:
    """How a picture of a clip is labelled for a judge: the time of each frame in it."""
    if len(times) == 1:
        return f"Frame at {times[0]:g} s"
    return f"Frames at {', '.join(f'{t:g} s' for t in times)}, left to right"


# The most frames of a sent piece shown beside the references, thinned evenly (the judge
# build brief: "the piece at most 10 frames").
PIECE_FRAMES_SHOWN = 10


def piece_frames(piece: str, most: int = PIECE_FRAMES_SHOWN) -> list[dict[str, Any]]:
    """The frames of the video piece at `piece`, FRAMES_PER_SECOND a second thinned evenly to
    at most `most`, kept in the file store, each with its time in the piece. Cut once: the
    same piece gives the same frames under the same keys every time it is asked for, so a
    turn showing them again is the same turn (a paid answer replayed, a refused frame known)."""
    name = f"piece_frames_{hashlib.sha256(f'{piece}|{most}'.encode()).hexdigest()[:32]}.json"
    kept = file_store.save_once(name, lambda: _cut_piece(piece, most), whole=_a_frame_list)
    frames: list[dict[str, Any]] = json.loads(file_store.read(kept))
    return frames


def _cut_piece(piece: str, most: int) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "piece.mp4"
        video.write_bytes(file_store.read(piece))
        frames = _every_frame(video, Path(folder) / "frames", FRAMES_PER_SECOND)
        if len(frames) > most:
            frames = [frames[round(i * (len(frames) - 1) / max(most - 1, 1))] for i in range(most)]
        cut = [{"times": [t], "key": _side_by_side([path])} for path, t in frames]
    return json.dumps(cut).encode()


def _a_frame_list(data: bytes) -> bool:
    """Whether `data` is a whole list of frames, not one a stopped worker left half-saved."""
    try:
        return isinstance(json.loads(data), list)
    except ValueError:
        return False


def sent_pictures(sent: SentList, label: str) -> list[Image]:
    """The pictures a clip was sent, in their order, each labelled `label` and its label."""
    return [
        Image(label=f"{label}, Image {picture.image}: {picture.label}", key=picture.file)
        for picture in sent.pictures
    ]


# How the judge is shown and told a clip's sent list (#228), as the judge build brief labels
# what the video API got (section 2): each picture by its number with the prompt's sentence
# naming it, each piece's first frame with how its frames were cut and that sentence.
SENT_PICTURE_LABEL = "Image {}. The prompt says: {}"
SENT_PIECE_LABEL = (
    "Video {}, frames cut from the reference video, time in seconds from its start. The prompt "
    "says: {}"
)
SENT_PIECE_FRAME_LABEL = "Video {}, {}"


def prompt_says(prompt: str) -> dict[str, str]:
    """Each "Image N" and "Video N" the prompt names, with the first sentence of it naming
    it: the label the video API got (the judge test harness's rule)."""
    said: dict[str, str] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", prompt):
        for name in re.findall(r"\b(Image \d+|Video \d+)\b", sentence):
            said.setdefault(name, sentence.strip())
    return said


def _labels_sent(sent: SentList) -> tuple[dict[int, str], dict[int, str]]:
    """What the prompt said of each picture and each piece, by number: its sentence, or,
    for one the prompt doesn't name, its label in the sent list, or a piece's line."""
    said = prompt_says(sent.prompt)
    return (
        {p.image: said.get(f"Image {p.image}", p.label) for p in sent.pictures},
        {p.number: said.get(f"Video {p.number}", PIECE_LABEL) for p in sent.pieces},
    )


def sent_references(sent: SentList) -> list[dict[str, Any]]:
    """What the judge is shown of the clip's sent list, kept in the file store: each picture
    in its order, then each piece's frames, PIECE_FRAMES_SHOWN of them in all, shared evenly
    by the pieces and thinned evenly in each (the judge build brief: "the piece at most 10
    frames"). Each has its "label" and "key"."""
    pictures, pieces = _labels_sent(sent)
    most = max(1, PIECE_FRAMES_SHOWN // len(sent.pieces)) if sent.pieces else 0
    shown = [
        {"label": SENT_PICTURE_LABEL.format(n, pictures[n]), "key": picture.file}
        for n, picture in ((picture.image, picture) for picture in sent.pictures)
    ]
    for piece in sent.pieces:
        for i, frame in enumerate(piece_frames(piece.file, most)):
            label = SENT_PIECE_FRAME_LABEL.format(piece.number, frame_label(frame["times"]))
            if i == 0:
                label = f"{SENT_PIECE_LABEL.format(piece.number, pieces[piece.number])}\n{label}"
            shown.append({"label": label, "key": frame["key"]})
    return shown


def sent_said(sent: SentList) -> list[str]:
    """A line for each picture and piece of the clip's sent list, in its order, as the judge
    is handed them beside what it is shown: "Image N: <the prompt's sentence>" (the judge
    build brief's handoff)."""
    pictures, pieces = _labels_sent(sent)
    return [
        *(f"Image {n}: {said}" for n, said in pictures.items()),
        *(f"Video {n}: {said}" for n, said in pieces.items()),
    ]


def references_shown(sent: SentList, label: str) -> list[Image]:
    """What a clip was sent, as pictures: each picture in its order with its label, then the
    frames of each piece, every one labelled starting with `label`."""
    return [
        *sent_pictures(sent, label),
        *(
            Image(
                label=f"{label}, Video {piece.number}, {frame_label(frame['times'])}",
                key=frame["key"],
            )
            for piece in sent.pieces
            for frame in piece_frames(piece.file)
        ),
    ]


TRIMMED = {
    "moment_over_limit": f"the moment ran past the video model's {VIDEO_API_MOST_PIECE_SECONDS} s",
    "pieces_over_limit": f"the pieces ran past the video model's {VIDEO_API_MOST_PIECE_SECONDS} s "
    "together",
    "video_ended": "the moment ran past its video's end",
}


def references_said(sent: SentList) -> str:
    """What a clip was sent, in words, for whoever is shown `references_shown`: how many
    pictures, where each piece was cut from, what was left out and which recolour fell back."""
    said = [
        f"It was sent {len(sent.pictures)} pictures, each shown with its label, all in "
        f"{sent.colour}."
    ]
    for piece in sent.pieces:
        cut = f"page video {piece.video}, moment {piece.moment}"
        if piece.trim is None:
            said.append(f"Video {piece.number} is {cut}, {piece.start:g} s to {piece.end:g} s.")
        else:
            said.append(
                f"Video {piece.number} is {cut}, cut to {piece.start:g} s to {piece.end:g} s "
                f"of its {piece.moment_start:g} s to {piece.moment_end:g} s because "
                # A stored trim the set no longer holds is said as it was kept.
                f"{TRIMMED.get(piece.trim, piece.trim)}."
            )
    if sent.pieces:
        said.append("Each video is shown as its frames, only for the movement, not the look.")
    if sent.left_out:
        said.append("Left out of the clip: " + " ".join(picture.label for picture in sent.left_out))
    said += [
        f"Library picture {each.of} couldn't be made {each.colour}, so it went as it was."
        for each in sent.recolours
        if each.outcome == "fell_back"
    ]
    return " ".join(said)


PICTURE_SETS = {"sides": SIDES_FRAMES_PER_SECOND, "frames": FRAMES_PER_SECOND}


def cut_pictures(
    clip: str, *, beside: int, sets: Iterable[str] = PICTURE_SETS
) -> dict[str, list[dict[str, Any]]]:
    """The pictures the judge is shown of the clip at `clip`, kept in the file store, each
    with the time in seconds of each frame in it: "sides" for SIDES, SIDES_FRAMES_PER_SECOND
    a second; "frames" for MOTION, FRAMES_PER_SECOND a second, which the fix check shows too
    (frames.py in the harness). `sets` names the ones wanted, both unless told. Frames that
    wouldn't fit in one ask beside `beside` other pictures are joined side by side, as few in
    a row as fit."""
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "clip.mp4"
        video.write_bytes(file_store.read(clip))
        room = MOST_PICTURES - beside
        return {
            name: _kept(_every_frame(video, Path(folder) / name, PICTURE_SETS[name]), room)
            for name in sets
        }


def _kept(frames: list[tuple[Path, float]], room: int) -> list[dict[str, Any]]:
    """`frames` kept as at most `room` pictures."""
    together = math.ceil(len(frames) / room)
    groups = [frames[i : i + together] for i in range(0, len(frames), together)]
    return [
        {"times": [t for _, t in group], "key": _side_by_side([path for path, _ in group])}
        for group in groups
    ]


def _side_by_side(paths: list[Path]) -> str:
    """The frames at `paths` joined left to right into one picture, kept in the file store:
    a single frame as it is."""
    if len(paths) == 1:
        return file_store.save("clip_frame.jpg", paths[0].read_bytes())
    return file_store.save("clip_frames.jpg", joined([PIL.Image.open(path) for path in paths]))


def joined(pictures: list[PIL.Image.Image]) -> bytes:
    """`pictures` joined left to right into one JPEG, as the judge is shown frames that
    wouldn't fit one to a picture."""
    pictures = [picture.convert("RGB") for picture in pictures]
    together = PIL.Image.new(
        "RGB", (sum(p.width for p in pictures), max(p.height for p in pictures)), "white"
    )
    x = 0
    for picture in pictures:
        together.paste(picture, (x, 0))
        x += picture.width
    saved = io.BytesIO()
    together.save(saved, format="JPEG", quality=92)
    return saved.getvalue()


def _ffmpeg(*arguments: str) -> bytes:
    """What ffmpeg gives with `arguments`. Never reads stdin: an ffmpeg that does eats the
    input of whatever runs it."""
    ran = subprocess.run(
        [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=assembly.TIMEOUT_SECONDS,
    )
    if ran.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
    return ran.stdout


def _every_frame(video: Path, folder: Path, per_second: int) -> list[tuple[Path, float]]:
    """The frame nearest each 1/`per_second` second, from the start to the end."""
    folder.mkdir()
    _ffmpeg(
        "-i",
        str(video),
        "-vf",
        f"fps={per_second}:round=near",
        "-q:v",
        "2",
        str(folder / "%03d.jpg"),
    )
    files = sorted(folder.glob("*.jpg"))
    return [(file, round(i / per_second, 2)) for i, file in enumerate(files)]


def wording_version(*, fix: bool = False, photos: bool = False, sent: bool = False) -> str:
    """A version of everything the judge is told, kept with each ruling: its instructions,
    the frames around the shop's text and the conversation, what it is handed, how many
    frames a second each check is shown and how they are labelled, and its answer forms. It
    changes whenever any of them does. For a fix's clip (`fix`), also the question about the
    owner's change, the labels of the two clips, its handoff and its answer form; a clip
    judged without them keeps the version it had before they were written. For a clip judged
    against its sent list (`sent`), the first question, PRODUCT and MOTION's words for it, the
    behaviour note's frame, the sent list's labels and its handoffs and answer forms."""
    if sent:
        return _sent_wording_version(fix=fix, photos=photos)
    told = [
        f"sides {SIDES_FRAMES_PER_SECOND} frames a second, motion {FRAMES_PER_SECOND}",
        SIDES_CHECK,
        MOTION_CHECK,
        PAGE_FRAME,
        CONVERSATION_FRAME,
        SHOP_PHOTO_LABEL,
        NEEDS_PHOTO_LABEL,
        FRAMES_LABEL,
        *(
            json.dumps(form.model_json_schema(), sort_keys=True)
            for form in (ClipCheckHandoff, MotionCheckHandoff, SidesVerdict, MotionVerdict)
        ),
    ]
    if fix:
        told += [
            MOTION_FIX_QUESTION,
            OLD_FRAMES_LABEL,
            NEW_FRAMES_LABEL,
            *(
                json.dumps(form.model_json_schema(), sort_keys=True)
                for form in (MotionFixCheckHandoff, MotionFixVerdict)
            ),
        ]
    # With the shop owner's photos, also their question, label, handoff and form.
    if fix and photos:
        told += [
            MOTION_PHOTO_FIX_QUESTION,
            OWNER_PHOTO_LABEL,
            *(
                json.dumps(form.model_json_schema(), sort_keys=True)
                for form in (MotionPhotoFixCheckHandoff, MotionPhotoFixVerdict)
            ),
        ]
    return hashlib.sha256("\n".join(told).encode()).hexdigest()[:10]


def _sent_wording_version(*, fix: bool, photos: bool) -> str:
    told = [
        f"product {SIDES_FRAMES_PER_SECOND} frames a second, motion and the first question "
        f"{FRAMES_PER_SECOND}, at most {PIECE_FRAMES_SHOWN} of the pieces' frames",
        REFERENCES_QUESTION,
        PRODUCT_CHECK,
        MOTION_FROM_REFERENCES,
        BEHAVIOUR_FRAME,
        SENT_PICTURE_LABEL,
        SENT_PIECE_LABEL,
        SENT_PIECE_FRAME_LABEL,
        PIECE_LABEL,
        FRAMES_LABEL,
        *(
            json.dumps(form.model_json_schema(), sort_keys=True)
            for form in (
                SentClipCheckHandoff,
                SentMotionCheckHandoff,
                ReferencesVerdict,
                ProductVerdict,
                MotionVerdict,
            )
        ),
    ]
    if fix:
        told += [
            MOTION_FIX_QUESTION,
            OLD_FRAMES_LABEL,
            NEW_FRAMES_LABEL,
            *(
                json.dumps(form.model_json_schema(), sort_keys=True)
                for form in (SentMotionFixCheckHandoff, MotionFixVerdict)
            ),
        ]
    if fix and photos:
        told += [
            MOTION_PHOTO_FIX_QUESTION,
            OWNER_PHOTO_LABEL,
            *(
                json.dumps(form.model_json_schema(), sort_keys=True)
                for form in (SentMotionPhotoFixCheckHandoff, MotionPhotoFixVerdict)
            ),
        ]
    return hashlib.sha256("\n".join(told).encode()).hexdigest()[:10]
