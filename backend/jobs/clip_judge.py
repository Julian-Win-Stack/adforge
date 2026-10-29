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
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import PIL.Image
from django.conf import settings
from pydantic import BaseModel, Field

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway import catalog
from gateway.gateway import call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)

from . import assembly
from .library import VIDEO_API_MOST_PIECE_SECONDS, SentList
from .models import AgentFix, AgentFixTry, Fix, ProducedItem, SceneStep
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
# check's verdict alone (jobs.work.check_fix). Wording: Julian's yes, 2026-10-14 21:44.
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


# ---------------------------------------------------------------- the ruling

Verdict = Literal["pass", "fail", "not_checked"]


def judged(
    sides: Sequence[dict[str, Any] | None],
    motion: Sequence[dict[str, Any] | None],
) -> dict[str, Any]:
    """The judge's ruling on a clip from its answers, in the order asked: each a SidesVerdict
    or MotionVerdict as a dict, or None for an ask that went unchecked. Each check (SIDES, and
    each MOTION question) is settled by itself: it fails when an answer fails it, and is not
    checked when it has no usable answer. The clip fails when any check fails. Every failing
    answer counts, however soon its thing is back to normal: the half-second forgive rule is
    gone (Julian 2026-10-19 03:37).

    Gives "verdict" (pass, fail or not_checked), "checks" (each check's verdict),
    "failed_answers" (how many answers failed on anything, of all asked), "failures" (each
    failed answer's check, ask, problems and whole answer) and "answers" (every answer, as
    given)."""
    # Each check's name and, by ask, the part of each answer that holds its verdict.
    asked: list[tuple[str, list[tuple[int, dict[str, Any]]]]] = [
        ("sides", [(ask, answer) for ask, answer in enumerate(sides, 1) if answer])
    ] + [
        (question, [(ask, answer[question]) for ask, answer in enumerate(motion, 1) if answer])
        for question in MOTION_QUESTIONS
    ]
    checks: dict[str, Verdict] = {}
    problems: dict[tuple[str, int], list[str]] = {}
    for name, usable in asked:
        failing = [(ask, said) for ask, said in usable if said["verdict"] == "fail"]
        for ask, said in failing:
            which = "sides" if name == "sides" else "motion"
            problems.setdefault((which, ask), []).append(said["problem"])
        checks[name] = "fail" if failing else "pass" if usable else "not_checked"
    failures = [
        {
            "check": which,
            "ask": ask,
            "problems": said,
            # Never the answer to a fix's question: it is no fault of the clip (spec, part 2).
            "answer": _without_owner_change((sides if which == "sides" else motion)[ask - 1]),
        }
        for (which, ask), said in sorted(
            problems.items(), key=lambda item: (item[0][0] != "sides", item[0][1])
        )
    ]
    verdicts = set(checks.values())
    return {
        "verdict": "fail"
        if "fail" in verdicts
        else "not_checked"
        if "not_checked" in verdicts
        else "pass",
        "checks": checks,
        "failed_answers": len(failures),
        "failures": failures,
        "answers": {"sides": sides, "motion": motion},
    }


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
    """The problems behind a ruling's failed checks, SIDES first, each check's in the order
    asked: never one a ruling made before left out (left_out)."""
    left = left_out(ruling)
    answers = ruling.get("answers", {})
    problems = []
    for name in ["sides", *MOTION_QUESTIONS]:
        if ruling.get("checks", {}).get(name) != "fail":
            continue
        for ask, answer in enumerate(answers.get("sides" if name == "sides" else "motion", []), 1):
            said = answer and (answer if name == "sides" else answer[name])
            if said and said["verdict"] == "fail" and (name, ask) not in left:
                problems.append(said["problem"])
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
        # they noted, try 0, whichever try it is; a try at a fix's clip the judge failed
        # (an old plan's new clip), as that fix's clips are.
        fixing = agent_try.agent_fix
        if fixing.door == AgentFix.Door.OWNER:
            return fixing.first_clip
        step = fixing.step
    fix = step.fix
    if fix is None:
        return None
    # A fix plan's fix row, filmed again by the fixing agent, is judged for the change too.
    if fix.redo not in (Fix.Redo.CLIP, Fix.Redo.SCENE) and not during_the_clip(fix):
        return None
    return step.shown_before(ProducedItem.Kind.CLIP)


def during_the_clip(fix: Fix) -> list[dict[str, Any]]:
    """The shop owner's photos `fix` takes things from that only show during the clip: the
    clip judge asks about them, even for a picture fix, whose picture check can't see them
    (Julian 20:25, "we always check whether the owner's words are applied right")."""
    return [photo for photo in fix.photos if photo["use"] == "clip"]


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
    clip is judged as any other, and the fix is left unchecked."""
    try:
        sides, motion, old = _asked(step, clip, conversation)
        ruling = _ruling(
            sides,
            motion,
            clip,
            compared_with=old,
            photos=bool(old and step.fix and during_the_clip(step.fix)),
        )
    except Exception as error:
        logger.exception("Scene %s's clip couldn't be judged", step.scene.number)
        ruling = {
            **_ruling([None] * ASKS, [None] * ASKS, clip),
            "error": str(error),
        }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    return ruling


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
) -> tuple[list[dict[str, Any] | None], list[dict[str, Any] | None], ProducedItem | None]:
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
    photos = during_the_clip(step.fix) if old is not None and step.fix is not None else []
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
        "what_the_agent_understood_from_the_owner": step.fix.fix if step.fix else None,
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
                owner_change=step.fix.fix,  # type: ignore[union-attr]
                owner_photos=[str(each["thing"]) for each in photos],
            )
            if with_photos
            else MotionFixCheckHandoff(**told, owner_change=step.fix.fix)  # type: ignore[union-attr]
            if fixing
            else MotionCheckHandoff(**told),
            MotionPhotoFixVerdict if with_photos else MotionFixVerdict if fixing else MotionVerdict,
            _shown(
                photo,
                [*needs, *owners] if with_photos else needs,
                pictures["frames"],
                old=old_frames,
            ),
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
    """One ask of one check: what it is asked for, told, handed and shown, and answers as."""

    purpose: str
    instructions: str
    handoff: ClipCheckHandoff
    output: type[SidesVerdict] | type[MotionVerdict]
    images: list[Image]


def _answer(step: SceneStep, ask: _Ask) -> dict[str, Any] | None:
    """One ask's answer, or None when it can't be had, for any reason: the clip is paid for,
    and the other asks' answers still count."""
    try:
        answer: BaseModel = call_model(
            job=step.scene.job,
            purpose=ask.purpose,
            instructions=ask.instructions,
            handoff=ask.handoff,
            output=ask.output,
            images=ask.images,
            pay_once=True,
        )
        return answer.model_dump(mode="json")
    except Exception as error:
        logger.warning(
            "%s ask %s about scene %s's clip went unchecked: %s",
            ask.purpose,
            ask.handoff.ask,
            step.scene.number,
            error,
            # Expected when a model or the service misbehaves; anything else is a bug to see.
            exc_info=not isinstance(
                error, (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter, AnswerTookTooLong)
            ),
        )
        return None


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
    shown = [Image(label=SHOP_PHOTO_LABEL, key=photo), *needs]
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
    at most `most`, kept in the file store, each with its time in the piece."""
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "piece.mp4"
        video.write_bytes(file_store.read(piece))
        frames = _every_frame(video, Path(folder) / "frames", FRAMES_PER_SECOND)
        if len(frames) > most:
            frames = [frames[round(i * (len(frames) - 1) / max(most - 1, 1))] for i in range(most)]
        return [{"times": [t], "key": _side_by_side([path])} for path, t in frames]


def references_shown(sent: SentList, label: str) -> list[Image]:
    """What a clip was sent, as pictures: each picture in its order with its label, then the
    frames of each piece, every one labelled starting with `label`."""
    return [
        *(
            Image(label=f"{label}, Image {picture.image}: {picture.label}", key=picture.file)
            for picture in sent.pictures
        ),
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


def wording_version(*, fix: bool = False, photos: bool = False) -> str:
    """A version of everything the judge is told, kept with each ruling: its instructions,
    the frames around the shop's text and the conversation, what it is handed, how many
    frames a second each check is shown and how they are labelled, and its answer forms. It
    changes whenever any of them does. For a fix's clip (`fix`), also the question about the
    owner's change, the labels of the two clips, its handoff and its answer form; a clip
    judged without them keeps the version it had before they were written."""
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
