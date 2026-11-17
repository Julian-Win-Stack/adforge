"""Judge a clip against its references and check for pass-throughs in its motion.
PRODUCT and PASS_THROUGH settle its verdict, for first clips and owner-note remakes alike.
The model and frame rates stay the same. Historical SIDES and motion rulings remain readable.
"""

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
from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway import catalog
from gateway.gateway import (
    call_model,
    past_the_safety_filter,
    refused_by_filter,
    refused_by_halving,
)
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    UnusableReply,
)
from jobs.presenter_words import presenter_words

from . import assembly, notices
from .library import PAGE_VIEW_TIER, VIDEO_API_MOST_PIECE_SECONDS, SentList, sent_list_of
from .models import AgentFixTry, ProducedItem, Scene, SceneAgentRun, SceneStep
from .notices import post_left_out_by_the_filter, post_notice, post_refused_by_the_filter
from .sent_labels import PIECE_LABEL
from .threads import closing_its_connection

logger = logging.getLogger(__name__)

# Each check is asked once and fails when its answer fails it (Julian 2026-10-22 21:09, "drop
# the vote"; docs/adr/0008-clip-judge-asks-once.md). On round 6's 43 graded clips one ask
# matched his grades as often as 3 asks and a vote (28 vs 29): the judge's errors are its bar,
# made by every ask alike, not noise a vote evens out (runs/gemini-judge-test/one-ask-vs-vote.md).
ASKS = 1

# PASS_THROUGH and the first question: the frame nearest each 1/FRAMES_PER_SECOND second.
FRAMES_PER_SECOND = 5
# PRODUCT's frames, the same way: twice as many, so it sees the moment a product is edge-on and
# the frames right before and after, where a turn's direction shows (5 stills missed every
# phone flip; 5 a second missed the direction: harness arms sides5, C8 and F1). Kept under
# "sides" in a ruling's pictures, the name of the check they were first cut for.
PRODUCT_FRAMES_PER_SECOND = 10

# ---------------------------------------------------------------- historical clip descriptions

MOTION_CHECK = """\
Describe a short video clip for a product ad before it is shown to the shop owner.

You get frames from the clip, in order, each labelled with its time in seconds, so you can \
follow what moves between them. You also get the scene's order: one sentence saying what the \
clip should show.

Fill in the look form from the frames: what is in the clip; how everything is at the first \
frame; then every movement and change of the product, of the hands touching it, and of the \
one thing the action changes (a surface, a liquid, a level, hair, a shape), step by step, \
with the time of the frame where you see it; and how everything is at the last frame. \
Describe what is on the frames, not what the product is for or what usually happens. \
Everything in the frame is your concern, not only the product: write down anything else \
that moves or changes too. Describe the action ordered and the action actually seen.

You may also be shown one shop photo of the product, and facts about it from the shop. They \
tell you what the product is, what it is made of, what is inside and how it is built. Use \
them only to understand and describe what you see."""


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


class MotionVerdict(BaseModel):
    look: ClipLook
    ordered_action: str = Field(
        description="The main action the order asks for, in your words, without camera or "
        "framing words."
    )
    seen_action: str = Field(description="The main action the clip actually does with the product.")


PASS_THROUGH_CHECK = """\
You check a short AI-made video clip for a product ad before it is shown to the shop owner.

You get the scene's order: one sentence saying what the clip should show. Then frames from \
the clip, in order, each labelled with its time in seconds, so you can follow what moves \
between them.

Your one question: at any moment, does any moving thing (the product or any part of it, \
such as a strap, a lid or a cord; a hand; or any other body part) pass through a body part \
or through a solid object, or appear on the other side of one without going around it?

Look at every frame. Follow each moving thing from frame to frame, and check where it is \
against the body parts and solid objects next to it: does it get from one side of one of \
them to the other without going around, over or under it?

Sides: a part that ends up on the other side of a body part or a solid object that stayed \
in its way has passed through it, even when the moment it crossed is hidden behind a hand \
or happens between two frames. It may only get there by going around an edge (over, under \
or beside it). A part fixed at both ends (a strap, loop or handle) moves as one piece: \
follow its whole length, not only the end nearest you.

Answer "yes" if anything passes through a body part or a solid object, or ends up on its \
other side with no way around. Ordinary touching, a hand in front of something, or a part \
going behind something are not a yes.

Give the times of the frames where you see it, and say in one sentence what passes through \
what. On "no", leave seconds empty and say what you checked."""


class PassThroughHandoff(Handoff):
    order: str


class PassThroughAnswer(BaseModel):
    answer: Literal["yes", "no"]
    seconds: list[float] = Field(description="Frame times where it is seen; empty on no.")
    what: str = Field(description="One sentence: what passes through what, or what you checked.")

    @model_validator(mode="after")
    def _usable_answer(self) -> Self:
        if not self.what.strip():
            raise ValueError("Say what passes through what, or what you checked.")
        if (self.answer == "yes") != bool(self.seconds):
            raise ValueError("A yes must name frame times; a no must leave them empty.")
        if any(not math.isfinite(t) or t < 0 for t in self.seconds):
            raise ValueError("Frame times must be finite, non-negative seconds.")
        return self


# Only for reading stored rulings made before #350. Never asked on new clips.
LEGACY_MOTION_QUESTIONS = ("real_motion", "sudden_change", "does_what_was_ordered")


# ---------------------------------------------------------------- a clip with its sent list

# A clip that carries the sent list its video API got (#210 point 18, #228) is judged against
# exactly that: the sent pictures, each piece's frames and the prompt, then the clip. Its words
# are the judge build brief's (handoffs/judge-build/brief.md, section 3), word for word from the
# hand test's harness (runs/hand-rounds/judge-test/run.py): PRODUCT replaces SIDES, and MOTION
# keeps today's words but for its last paragraph.

HIDDEN_PART_RULE = (
    "Before you call a part changed, missing or a different shape, check why it looks that way. "
    "A part that is partly hidden (behind a hand, an ear, hair, clothing or another object), "
    "turned away from the camera, seen edge-on or from a new angle, or blurred by fast movement "
    "looks thinner, shorter or a different outline without being different. That is not a fault. "
    "Fail only on what you can clearly see: a visible part that is itself wrong, or a part that "
    "should be in clear view and is not."
)

# Narrowed for #708 (spec #706, Julian Nov 17, 2026): only shape, colour and extra pieces;
# text, logos and what the product does are no longer faults.
PRODUCT_CHECK = f"""\
You check an AI-made video clip before it is used in a product ad. The video API that made the \
clip was given reference pictures of the product, sometimes a reference video of the real \
product in use, and a prompt. You are given exactly the same, and then the clip as frames in \
order, each labelled with its time in seconds. Your one question: compared with the references, \
did the product's shape or colour change, or did an extra piece appear?

Work in three steps.
1. Learn the product from the references. From the pictures and the video frames write down: \
its parts and where each sits; each side you can see and its shape; its colours; and how many \
of it the scene has. Write only what the references show. Also write what the references never \
show (a side or a state), because you cannot know what a real one looks like there.
2. Look at the clip, frame by frame. For each frame write what of the product shows, and \
whether it has any of these faults:
- a part the real product does not have;
- a part missing, or a part that vanishes between frames;
- a part changing shape between frames;
- the product changing colour between frames;
- a second copy of the product where the scene has one;
- a separate extra piece appearing.

{HIDDEN_PART_RULE}

3. Decide.
- "fail" when you are sure, from the references, that one of those faults is on screen. Name \
the fault in product terms, with the reference you compared against and the frame times, so \
that someone who has not watched the clip can fix it.
- "unsure" when the clip shows a side or a state of the product that the references never \
show, so you cannot know what a real one looks like there. Say what the references never show. \
Unsure is not a fail: the shop owner decides.
- "pass" when none of those faults is on screen.

These are not faults and not your question:
- any text, lettering, logo, label or printed art on the product, at any size, garbled or not;
- what the product does: its action, what comes out of it, its effect on what it touches, how \
it moves, and whether the action works;
- lighting, angle, distance, size in frame and blur, and a colour that differs from the \
references by itself (only a change between frames is a fault);
- the person, the place and the camera."""


# A comment, not a docstring: a docstring would be sent to the judge in the answer's schema.
class ClipFrame(BaseModel):
    time: float = Field(description="The frame's time in seconds, as labelled.")
    product_shows: str = Field(description="What of the product shows, in a few words.")
    differs: str = Field(description="What differs from the references in this frame, or empty.")


class ProductVerdict(BaseModel):
    product_from_references: str = Field(
        description="The real product as the references show it: its parts and where each "
        "sits, its sides and their shape, its colours, how many the scene has. Then what the "
        "references never show."
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
        description="On unsure: the side or state the clip shows that the references never "
        "show. Null otherwise."
    )


# Historical clip description with its sent list: replace the shop-photo input description.
MOTION_SHOP_PHOTO_PARAGRAPH = (
    "You may also be shown one shop photo of the product, and facts about it from the shop. They "
    "tell you what the product is, what it is made of, what is inside and how it is built. Use "
    "them only to understand and describe what you see."
)
assert MOTION_CHECK.count(MOTION_SHOP_PHOTO_PARAGRAPH) == 1
MOTION_REFERENCES_PARAGRAPH = (
    "Before the clip you are shown what the video API that made the clip was given: the "
    "reference pictures, each labelled as in the prompt, frames from the reference video when "
    "there was one, and the prompt itself. They tell you what the product is, what it is made "
    "of, how it is built and how a real one moves and behaves. Use them to know how a real one "
    "moves, and to describe what you see."
)
MOTION_FROM_REFERENCES = MOTION_CHECK.replace(
    MOTION_SHOP_PHOTO_PARAGRAPH, MOTION_REFERENCES_PARAGRAPH
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
- "product not shown" when the clip shows a side, a state or an action of the product that \
neither the references nor the note show, so nobody could know from them what the real product \
looks like or does in the clip. Say what they never show, in a few words that finish the \
sentence "the references never show ...".
- With "product not shown", also say asked_for: whether the scene's order, the prompt sent \
to the video API, or the starting picture shows or asks for that side, state or action. \
Answer true when any of those asks for it, false when none does.
- "detail not shown" when the references and the note show the product and how it behaves in \
the clip's action, but one detail the clip shows, such as a small part, a side of one part or a \
state of one part, is never shown, so the rest of the clip can still be judged. Name that \
detail, in a few words that finish the sentence "the references never show ...".
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
    verdict: Literal["enough", "product not shown", "detail not shown"]
    references_never_show: str | None = Field(
        description="On product not shown: what the references and the note never show. On "
        'detail not shown: the one detail they never show. In a few words that finish "the '
        'references never show ...". Null otherwise.'
    )
    asked_for: bool | None = Field(
        default=None,
        description="With product not shown: does the scene's order, prompt sent to the "
        "video API, or starting picture show or ask for that side, state or action? "
        "True if any does; false if none does. Null for other verdicts.",
    )

    @model_validator(mode="after")
    def _not_shown_says_what(self) -> Self:
        if self.verdict != "enough" and not (self.references_never_show or "").strip():
            raise ValueError("A clip it can't tell about must say what the references never show.")
        if self.verdict == "product not shown" and self.asked_for is None:
            raise ValueError("Product not shown must say whether the scene asked for it.")
        return self

    @model_serializer(mode="wrap")
    def _asked_for_only_when_answered(self, dump: SerializerFunctionWrapHandler) -> Any:
        dumped = dump(self)
        if self.asked_for is None:
            dumped.pop("asked_for", None)
        return dumped


# Said to PRODUCT after its words when the first question found one detail the
# references never show (spec round-7-unlisted item 11, Julian Oct 30 02:57): the checks still
# runs, on everything else.
DETAIL_LEFT_OUT = (
    "The references never show one detail of the product the clip shows, named as "
    '"detail_the_references_never_show". Nobody can know what the real one looks like there, '
    "so leave that detail out: it is neither a fault nor a reason to be unsure. Judge "
    "everything else in the clip."
)


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
    # The detail the first question found the references never show, left out of the check
    # (DETAIL_LEFT_OUT); handed over only when there is one.
    detail_the_references_never_show: str | None = None

    @model_serializer(mode="wrap")
    def _detail_only_when_there_is_one(self, dump: SerializerFunctionWrapHandler) -> Any:
        dumped = dump(self)
        if dumped.get("detail_the_references_never_show") is None:
            dumped.pop("detail_the_references_never_show", None)
        return dumped


class SentMotionCheckHandoff(SentClipCheckHandoff):
    """And the scene's order: what the clip should show."""

    order: str


# ---------------------------------------------------------------- the ruling

Verdict = Literal["pass", "fail", "unsure", "not_checked"]

# The first check of a ruling: PRODUCT, for a clip judged against its sent list (#228); SIDES
# in a ruling made before every B-roll clip had one, which is still read. A ruling's answers
# keep each under its own name.
FIRST_CHECKS = ("sides", "product")


def judged(
    first: Sequence[dict[str, Any] | None],
    motion: Sequence[dict[str, Any] | None],
    *,
    blind: Iterable[tuple[str, int]] = (),
    product_skipped: bool = False,
    pass_through: Sequence[dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Combine PRODUCT and PASS_THROUGH, retaining historical MOTION answers when supplied.

    A failed product fails the clip; an unsure or partially refused product check is unsure;
    an unreadable product answer is not checked. A skipped product stays unsure (#290).
    A pass-through fails the clip even if PRODUCT was skipped. Missing or partially refused
    pass-through answers leave it unsure. Omit the new check when settling historical inputs.
    MOTION cannot fail a clip or leave it unsure or unchecked, even when it can't answer.
    """
    blind = tuple(blind)
    usable = [(ask, answer) for ask, answer in enumerate(first, 1) if answer]
    failing = [(ask, answer) for ask, answer in usable if answer["verdict"] == "fail"]
    unsure = any(answer["verdict"] == "unsure" for _, answer in usable) or any(
        check == "product" for check, _ in blind
    )
    verdict: Verdict
    if product_skipped:
        verdict = "unsure"
    elif failing:
        verdict = "fail"
    elif unsure:
        verdict = "unsure"
    else:
        verdict = "pass" if usable else "not_checked"
    failures = (
        []
        if product_skipped
        else [
            {
                "check": "product",
                "ask": ask,
                "problems": [_problem(answer)],
                "answer": _without_owner_change(answer),
            }
            for ask, answer in failing
        ]
    )
    checks: dict[str, Verdict] = {} if product_skipped else {"product": verdict}
    answers = {"product": first, "motion": motion}
    if pass_through is not None:
        answers["pass_through"] = pass_through
        crossing = [
            (ask, answer)
            for ask, answer in enumerate(pass_through, 1)
            if answer and answer["answer"] == "yes"
        ]
        crossing_verdict: Verdict = (
            "fail"
            if crossing
            else "unsure"
            if not pass_through
            or any(answer is None for answer in pass_through)
            or any(check == "pass_through" for check, _ in blind)
            else "pass"
        )
        checks["pass_through"] = crossing_verdict
        failures += [
            {
                "check": "pass_through",
                "ask": ask,
                "problems": [_pass_through_problem(answer)],
                "answer": answer,
            }
            for ask, answer in crossing
        ]
        if "fail" in (verdict, crossing_verdict):
            verdict = "fail"
        elif "unsure" in (verdict, crossing_verdict) or verdict == "not_checked":
            verdict = "unsure"
    return {
        "verdict": verdict,
        "checks": checks,
        "failed_answers": len(failures),
        "failures": failures,
        "answers": answers,
    }


def _pass_through_problem(answer: dict[str, Any]) -> str:
    at = ", ".join(f"{t:g} s" for t in answer["seconds"])
    return f"{answer['what']} (At {at}.)"


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


def _without_owner_change(answer: dict[str, Any] | None) -> dict[str, Any] | None:
    return answer and {key: said for key, said in answer.items() if key != "owner_change"}


def still_failing(ruling: dict[str, Any]) -> list[str]:
    """The problems behind a ruling's failed checks, PRODUCT (or an old ruling's SIDES) first,
    each check's in the order asked: never one a ruling made before left out (left_out)."""
    left = left_out(ruling)
    answers = ruling.get("answers", {})
    problems = []
    for name in [*FIRST_CHECKS, *LEGACY_MOTION_QUESTIONS]:
        if ruling.get("checks", {}).get(name) != "fail":
            continue
        first = name in FIRST_CHECKS
        for ask, answer in enumerate(answers.get(name if first else "motion", []), 1):
            said = answer and (answer if first else answer[name])
            if said and said["verdict"] == "fail" and (name, ask) not in left:
                problems.append(_problem(said))
    if ruling.get("checks", {}).get("pass_through") == "fail":
        problems += [
            _pass_through_problem(answer)
            for ask, answer in enumerate(answers.get("pass_through", []), 1)
            if answer and answer["answer"] == "yes" and ("pass_through", ask) not in left
        ]
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
FRAMES_LABEL = "Frames from the clip, in order:"


SCENE_REFERENCES_FRAME = (
    "Starting picture: what the video model was given. Then you are shown the real product "
    "photos that the video model did not see, each with the line the picture model was given. "
    "Use those photos and the behaviour note to learn the real product; the starting picture "
    "is drawn, not evidence of the real product. Then the clip is shown as frames in order, "
    "each labelled with its time in seconds. The video prompt is also provided."
)


TALKING_SCENE_CONTEXT = "The woman talks on camera in this clip; her mouth moving is expected."


def check_words(words: str, *, scene: bool = False, talking: bool = False, gender: str = "") -> str:
    """Keep the checks' questions, describing the inputs and expected on-camera talking."""
    if scene:
        if words in (PRODUCT_CHECK, REFERENCES_QUESTION):
            words = (
                SCENE_REFERENCES_FRAME
                + " Your one question:"
                + words.split("Your one question:", 1)[1]
            )
        else:
            words = words.replace(MOTION_REFERENCES_PARAGRAPH, SCENE_REFERENCES_FRAME)
    return (
        f"{words}\n\n{presenter_words(gender).render(TALKING_SCENE_CONTEXT)}" if talking else words
    )


def _scene_run(step: SceneStep) -> SceneAgentRun | None:
    filmed = AgentFixTry.objects.filter(step=step, agent_fix__is_scene_agent=True).first()
    if filmed is None or filmed.sent is None:
        return None
    sent = SentList.model_validate(filmed.sent)
    if sent.scene_run is None:
        return None
    return SceneAgentRun.objects.get(pk=sent.scene_run)


def judge_clip(step: SceneStep, clip: ProducedItem) -> dict[str, Any]:
    """Judge the B-roll clip `step` made against the sent list its video API got, keeping the
    ruling on the clip and giving it (_judge_against_sent). Every B-roll clip keeps its sent
    list before it is judged. Scene-agent clips keep the starting picture and the separate
    real-product photos used to draw it; old clips keep only their video inputs."""
    sent = sent_list_of(clip)
    assert sent is not None, "a B-roll clip keeps its sent list before it is judged"
    return _judge_against_sent(step, clip, sent)


def _judge_against_sent(step: SceneStep, clip: ProducedItem, sent: SentList) -> dict[str, Any]:
    """Judge first clips and remakes with PRODUCT and PASS_THROUGH only.
    The first question may skip PRODUCT for an unshown requested state, or leave out a
    missing detail. PASS_THROUGH always runs on the current clip's frames and scene order.
    """
    answered: list[_Answered] = []
    try:
        ruling, answered = _asked_against_sent(step, clip, sent)
    except Exception as error:
        logger.exception("Scene %s's clip couldn't be judged", step.scene.number)
        _say_pass_through_unavailable(step)
        ruling = {
            **judged([None] * ASKS, [None] * ASKS, pass_through=[None] * ASKS),
            **_sent_ruling_facts(step, clip),
            "error": str(error),
        }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    _say_what_the_filter_refused(step, answered)
    _say_the_judge_is_unsure(step, clip, ruling)
    return ruling


def _asked_against_sent(
    step: SceneStep, clip: ProducedItem, sent: SentList
) -> tuple[dict[str, Any], list[_Answered]]:
    """_judge_against_sent's ruling and every answer it was given."""
    pictures: dict[str, Any] | None = (clip.ruling or {}).get("pictures")
    if not pictures:
        references = sent_references(sent)
        pictures = {
            **cut_pictures(clip.file, beside=len(references)),
            "references": references,
        }
    clip.ruling = {"pictures": pictures}
    clip.save(update_fields=["ruling"])
    shown = [Image(label=each["label"], key=each["key"]) for each in pictures["references"]]
    job = step.scene.job
    talking = step.scene.kind == Scene.Kind.TALKING
    told: dict[str, Any] = {
        "product": job.product_name,
        "prompt_sent_to_the_video_api": sent.prompt,
        "references": sent_said(sent),
        "clip_seconds": clip.seconds,
        "how_this_product_behaves": job.product_behaviour,
        "order": step.shows,
    }
    facts = _sent_ruling_facts(step, clip)

    asking_first = _Ask(
        "check_clip_references",
        check_words(
            REFERENCES_QUESTION,
            scene=sent.mode is not None,
            talking=talking,
            gender=job.person_gender,
        ),
        SentMotionCheckHandoff(ask=1, **told),
        ReferencesVerdict,
        [*shown, *_frames_shown(pictures["frames"])],
        references=0,
    )
    first = _answer(step, asking_first)
    first_broke_its_form = first.unusable
    if first_broke_its_form:
        # An answer that broke its form (a "not shown" that doesn't say what is never shown)
        # is asked for once more.
        first = _answer(step, asking_first)
    first_still_unusable = first_broke_its_form and first.answer is None
    if first.answer is None:
        post_notice(
            job,
            notices.JUDGE_FIRST_QUESTION,
            f"The clip judge couldn't ask whether scene {step.scene.number}'s references show "
            "enough to judge its clip, "
            + (
                "as its first answer broke its form and the retry gave no usable answer. "
                "The clip stays unsure for the shop owner to check."
                if first_still_unusable
                else "so it ran the product check anyway."
            ),
            Message.Level.PROBLEM,
            scene=step.scene.number,
        )
    # Skip PRODUCT only when the scene requested what the references never show (#365).
    # An unrequested state must reach PRODUCT; the first question itself never fails it.
    cannot_tell = (
        first.answer["references_never_show"]
        if first.answer is not None
        and first.answer["verdict"] == "product not shown"
        and first.answer["asked_for"]
        else None
    )
    product_skipped = cannot_tell is not None or first_still_unusable
    # One detail the references never show is left out of PRODUCT (item 11).
    detail = (
        first.answer["references_never_show"]
        if first.answer is not None and first.answer["verdict"] == "detail not shown"
        else None
    )
    left_out = f"\n\n{DETAIL_LEFT_OUT}" if detail is not None else ""
    # Judge colour only against the starting picture and the agent's photos (spec #514).
    views = (
        f"\n\n{JUDGE_COLOUR_RULE}"
        if any(photo.tier == PAGE_VIEW_TIER for photo in sent.picture_photos)
        else ""
    )
    if detail is not None:
        told["detail_the_references_never_show"] = detail
    product_asks = [
        _Ask(
            "check_clip_product",
            check_words(
                PRODUCT_CHECK,
                scene=sent.mode is not None,
                talking=talking,
                gender=job.person_gender,
            )
            + views
            + left_out,
            SentClipCheckHandoff(ask=ask, **{k: v for k, v in told.items() if k != "order"}),
            ProductVerdict,
            [*shown, *_frames_shown(pictures["sides"])],
            references=len(shown),
            frames=_times(pictures["sides"]),
        )
        for ask in range(1, ASKS + 1)
        if not product_skipped
    ]
    pass_through_asks = [
        _Ask(
            "check_clip_pass_through",
            PASS_THROUGH_CHECK,
            PassThroughHandoff(order=step.shows),
            PassThroughAnswer,
            _frames_shown(pictures["frames"]),
            references=0,
            frames=_times(pictures["frames"]),
        )
    ]
    asks = product_asks + pass_through_asks
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        asked = [
            pool.submit(contextvars.copy_context().run, closing_its_connection, _answer, step, each)
            for each in asks
        ]
        answers = [each.result() for each in asked]
    product = answers[: len(product_asks)]
    pass_through = answers[len(product_asks) :]
    blinded = [
        (check, ask, each.blind)
        for check, said in (
            ("product", product),
            ("pass_through", pass_through),
        )
        for ask, each in enumerate(said, 1)
        if each.blind
    ]
    blind = [
        {"check": check, "ask": ask, "seconds": list(seconds)} for check, ask, seconds in blinded
    ]
    ruling = {
        **judged(
            [each.answer for each in product],
            [],
            blind=[(check, ask) for check, ask, _ in blinded],
            product_skipped=product_skipped,
            pass_through=[each.answer for each in pass_through],
        ),
        **facts,
        **_past_the_filter(product),
        **({"refused_frames": blind} if blind else {}),
        **({"detail_not_shown": detail} if detail is not None else {}),
        **({"references_answer_unusable": True} if first_still_unusable else {}),
    }
    if cannot_tell is not None:
        ruling["cannot_tell"] = cannot_tell
    ruling["answers"]["references"] = first.answer
    # The first question's own refusal is told by the notice above, not as the clip's: it
    # never leaves a reference out (references=0), so a refusal leaves it unanswered and
    # PRODUCT runs, which leaves the picture out and says so.
    return ruling, answers


def _sent_ruling_facts(step: SceneStep, clip: ProducedItem) -> dict[str, Any]:
    """What a ruling against a sent list says beside its verdict: its words and models, the
    pictures it was shown and the behaviour note it was told."""
    sent = sent_list_of(clip)
    return {
        "wording": wording_version(
            scene=sent is not None and sent.mode is not None,
            talking=(step.scene.kind == Scene.Kind.TALKING),
            gender=step.scene.job.person_gender,
        ),
        "models": {
            purpose.removeprefix("check_clip_"): catalog.MODEL_FOR_PURPOSE[purpose]
            for purpose in (
                "check_clip_references",
                "check_clip_product",
                "check_clip_pass_through",
            )
        },
        "pictures": (clip.ruling or {}).get("pictures"),
        "behaviour": step.scene.job.product_behaviour,
    }


def _say_the_judge_is_unsure(step: SceneStep, clip: ProducedItem, ruling: dict[str, Any]) -> None:
    """Tell the chat, once, when the judge couldn't tell whether a clip is right because the
    references never show what it shows: the clip is kept, and the shop owner decides
    (#210 point 18). Only for a clip the ruling keeps as unsure: a clip it failed goes to the
    fixing agent. A scene-agent clip whose checks all failed to answer is also kept for
    the owner to check. A clip judged with one detail left out, as the references never show it, is
    told the same way when it passes, naming the detail. Seconds of the clip the safety filter
    refused are told in red, as every refusal is, said to be kept only when the clip is."""
    number = step.scene.number
    job = step.scene.job
    if (
        ruling["verdict"] == "not_checked"
        or ruling["verdict"] == "unsure"
        and ruling.get("checks", {}).get("product") == "not_checked"
    ) and _scene_run(step) is not None:
        _ask_to_watch(clip, ": I couldn't check this clip; please check it yourself")
    kept = ruling["verdict"] == "unsure"
    if kept and ruling.get("references_answer_unusable"):
        _ask_to_watch(clip, ": I couldn't check whether this clip is right")
    if kept and ruling.get("checks", {}).get("pass_through") == "unsure":
        _ask_to_watch(
            clip, ": I couldn't fully check for things passing through a body or solid object"
        )
    never: list[str | None] = [ruling["cannot_tell"]] if "cannot_tell" in ruling else []
    never += [
        answer["references_never_show"]
        for answer in ruling.get("answers", {}).get("product", [])
        if answer and answer["verdict"] == "unsure"
    ]
    for what in dict.fromkeys(never) if kept else ():
        said = (what or "").strip().rstrip(".")
        if not said:
            # The judge said it can't tell without saying what is never shown.
            logger.warning("Scene %s's judge was unsure without saying why", number)
        text = (
            f"Please watch scene {number}'s clip: I couldn't check whether it's right, as "
            f"your photos never show {said}."
            if said
            else f"Please watch scene {number}'s clip: I couldn't check whether it's right."
        )
        notices.ask_to_watch(clip, text)
    detail = (ruling.get("detail_not_shown") or "").strip().rstrip(".")
    if detail and ruling["verdict"] == "pass":
        text = (
            f"Please watch scene {number}'s clip: I checked it, but your photos never show "
            f"{detail}, so I couldn't check that part."
        )
        notices.ask_to_watch(clip, text)
    seconds = sorted({t for each in ruling.get("refused_frames", []) for t in each["seconds"]})
    if seconds:
        at = ", ".join(f"{t:g} s" for t in seconds)
        post_refused_by_the_filter(
            job,
            f"scene {number}'s clip at {at}",
            "the clip judge couldn't check those seconds"
            + (" and the clip is kept for the shop owner to decide" if kept else ""),
        )
        if kept:
            _ask_to_watch(clip, f" at {at}: I couldn't check those seconds")


def _times(pictures: list[dict[str, Any]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(picture["times"]) for picture in pictures)


def _past_the_filter(product: list[_Answered]) -> dict[str, Any]:
    """What a ruling says of the safety filter (requirement 22), when it refused a reference
    picture: "not_shown", each ask answered without reference pictures it refused, and which.
    A refused frame of the clip is the ruling's "refused_frames"; an ask is never refused
    whole, as its refused frames are left out (_past_refused_frames)."""
    asked = [("product", ask, each) for ask, each in enumerate(product, 1)]
    not_shown = [
        {"check": check, "ask": ask, "pictures": each.not_shown}
        for check, ask, each in asked
        if each.not_shown
    ]
    return {"not_shown": not_shown} if not_shown else {}


def _say_what_the_filter_refused(step: SceneStep, answered: list[_Answered]) -> None:
    """Tell the chat in red of every reference picture the safety filter refused when the
    clip was judged, which it was judged without. Refused seconds of the clip are told by
    _say_the_judge_is_unsure."""
    job, number = step.scene.job, step.scene.number
    left = list(dict.fromkeys(name for each in answered for name in each.not_shown))
    if left:
        post_left_out_by_the_filter(job, left, f"scene {number}'s clip was judged")


def _ask_to_watch(clip: ProducedItem, why: str) -> None:
    """Ask the shop owner, once, to watch `clip`, kept though the safety filter
    stopped part of its check, saying `why` (" at 3 s: I couldn't check those seconds"). The
    refusal itself is the developer's, recorded apart."""
    assert clip.scene is not None
    text = f"Please watch scene {clip.scene.number}'s clip{why}."
    notices.ask_to_watch(clip, text)


@dataclass(frozen=True)
class _Ask:
    """One ask of one check: what it is asked for, told, handed and shown, and answers as.
    The first `references` pictures shown are the reference pictures beside the clip: what
    its video API was sent."""

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
    """One ask's answer, or None when none could be had; and the reference pictures the
    safety filter refused, which it was answered without, by name."""

    answer: dict[str, Any] | None
    not_shown: list[str]
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

    def asking(shown: list[Image]) -> dict[str, Any]:
        answer: BaseModel = call_model(
            job=step.scene.job,
            purpose=ask.purpose,
            instructions=ask.instructions,
            handoff=ask.handoff,
            output=ask.output,
            images=shown,
            pay_once=True,
        )
        return answer.model_dump(mode="json")

    try:
        try:
            answer, left_out = past_the_safety_filter(
                asking, ask.images, may_leave_out=lambda image: image in references
            )
        except BlockedBySafetyFilter:
            if not ask.frames:
                raise
            return _past_refused_frames(asking, ask)
        return _Answered(answer, [named(image) for image in left_out])
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
        if ask.purpose == "check_clip_pass_through":
            _say_pass_through_unavailable(step)
        return _Answered(None, [], unusable=isinstance(error, UnusableReply))


def _say_pass_through_unavailable(step: SceneStep) -> None:
    post_notice(
        step.scene.job,
        notices.CLIP_PASS_THROUGH_UNAVAILABLE,
        f"The clip judge couldn't check scene {step.scene.number}'s clip for things "
        "passing through a body or solid object. This check stays unsure.",
        Message.Level.PROBLEM,
        scene=step.scene.number,
    )


def _past_refused_frames(asking: Callable[[list[Image]], dict[str, Any]], ask: _Ask) -> _Answered:
    """`ask` answered without the pictures of the clip's frames the safety filter refuses,
    found by halving (refused_by_halving), each half shown beside the rest; then the ask is
    made once on every frame not refused. Gives the seconds it didn't see, so the check is
    unsure for them (Julian, Oct 29 00:10), and no answer when even that last ask is refused:
    the check is still unsure, never unchecked, for a filter that refused what it was shown of
    the clip."""
    rest = ask.images[: len(ask.images) - len(ask.frames)]
    clip = ask.images[len(rest) :]
    times = dict(zip((image.key for image in clip), ask.frames, strict=True))
    refused = refused_by_halving(lambda group: refused_by_filter(asking, [*rest, *group]), clip)
    # Every half passing alone, though all of them together were refused: none can be named.
    blind = refused or clip
    shown = [image for image in clip if image not in blind]
    answer: dict[str, Any] | None = None
    if shown:
        try:
            answer = asking([*rest, *shown])
        except BlockedBySafetyFilter:
            blind = clip
    if answer is None:
        logger.warning("%s couldn't be asked past the clip frames refused", ask.purpose)
    return _Answered(
        answer,
        [],
        blind=tuple(sorted(t for image in blind for t in times[image.key])),
    )


def named(image: Image) -> str:
    """A reference picture as a notice names it: its label, as words in a sentence."""
    label = image.label.rstrip(":")
    return label[:1].lower() + label[1:]


def _frames_shown(pictures: list[dict[str, Any]]) -> list[Image]:
    """The current clip's frames in order, labelled with each frame's time."""
    return [
        Image(
            label=(f"{FRAMES_LABEL}\n" if n == 0 else "") + frame_label(picture["times"]),
            key=picture["key"],
        )
        for n, picture in enumerate(pictures)
    ]


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
        frames = cut_frames(video, Path(folder) / "frames", FRAMES_PER_SECOND)
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
STARTING_PICTURE_LABEL = "Starting picture: what the video model was given."
PICTURE_PHOTO_LABEL = "Real product photo {}, sent to the picture model, not the video model: {}"
# A page view code added beside the agent's picks (spec #514, library.PAGE_VIEW_TIER).
PAGE_VIEW_LABEL = (
    "Real product photo {}, an extra view from the page, sent to the picture model, not the "
    "video model. It shows: {}"
)
# How the product check treats the page views' colour (spec #514): shape, parts and sides
# from them; colour only from the starting picture and the agent's photos. Told only when
# the clip has views.
JUDGE_COLOUR_RULE = (
    'Some real product photos are extra views from the page, each marked "an extra view '
    "from the page\". Use them to learn the product's shape, parts and sides. Judge the "
    "product's colour only against the starting picture and the photos the agent picked, "
    "never against an extra view."
)
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
    frames"). Scene-agent clips instead show the starting picture then its real-product
    photos with the agent's lines (spec #303 section 4b). Each has its "label" and "key"."""
    if sent.mode is not None:
        assert sent.starting_picture is not None, "a scene clip keeps its starting picture"
        return [
            {"label": STARTING_PICTURE_LABEL, "key": sent.starting_picture.file},
            *(
                {
                    "label": (
                        PAGE_VIEW_LABEL if photo.tier == PAGE_VIEW_TIER else PICTURE_PHOTO_LABEL
                    ).format(n, photo.label),
                    "key": photo.file,
                }
                for n, photo in enumerate(sent.picture_photos, 1)
            ),
        ]
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
    if sent.mode is not None:
        return [each["label"] for each in sent_references(sent)]
    pictures, pieces = _labels_sent(sent)
    return [
        *(f"Image {n}: {said}" for n, said in pictures.items()),
        *(f"Video {n}: {said}" for n, said in pieces.items()),
    ]


def references_shown(sent: SentList, label: str) -> list[Image]:
    """What a clip was sent, as pictures: each picture in its order with its label, then the
    frames of each piece, every one labelled starting with `label`."""
    if sent.mode is not None:
        return [
            Image(label=f"{label}, {each['label']}", key=each["key"])
            for each in sent_references(sent)
        ]
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
    if sent.mode is not None:
        return (
            "The video model was sent the starting picture"
            + (" and a page-video piece." if sent.pieces else " alone.")
            + " The real-product photos shown after it were drawing inputs, not video inputs."
        )
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


PICTURE_SETS = {"sides": PRODUCT_FRAMES_PER_SECOND, "frames": FRAMES_PER_SECOND}


def cut_pictures(
    clip: str, *, beside: int, sets: Iterable[str] = PICTURE_SETS
) -> dict[str, list[dict[str, Any]]]:
    """The pictures the judge is shown of the clip at `clip`, kept in the file store, each
    with the time in seconds of each frame in it: "sides" for PRODUCT, PRODUCT_FRAMES_PER_SECOND
    a second; "frames" for PASS_THROUGH and the first question, FRAMES_PER_SECOND a second
    (frames.py in the harness). `sets` names the ones wanted, both unless told. Frames that
    wouldn't fit in one ask beside `beside` other pictures are joined side by side, as few in
    a row as fit."""
    with tempfile.TemporaryDirectory() as folder:
        video = Path(folder) / "clip.mp4"
        video.write_bytes(file_store.read(clip))
        room = MOST_PICTURES - beside
        return {
            name: _kept(cut_frames(video, Path(folder) / name, PICTURE_SETS[name]), room)
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


def cut_frames(video: Path, folder: Path, per_second: int) -> list[tuple[Path, float]]:
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


def wording_version(*, scene: bool = False, talking: bool = False, gender: str = "") -> str:
    """Version the active checks' words, frame rates, labels, handoffs and answer forms.
    Talking clips also include the sentence that tells the judge to expect on-camera talking.
    """
    told = [
        f"product {PRODUCT_FRAMES_PER_SECOND} frames a second, pass-through and the first question "
        f"{FRAMES_PER_SECOND}, at most {PIECE_FRAMES_SHOWN} of the pieces' frames",
        REFERENCES_QUESTION,
        PRODUCT_CHECK,
        PASS_THROUGH_CHECK,
        BEHAVIOUR_FRAME,
        DETAIL_LEFT_OUT,
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
                PassThroughHandoff,
                PassThroughAnswer,
            )
        ),
    ]
    if scene:
        told += [
            SCENE_REFERENCES_FRAME,
            STARTING_PICTURE_LABEL,
            PICTURE_PHOTO_LABEL,
            PAGE_VIEW_LABEL,
            JUDGE_COLOUR_RULE,
        ]
    if talking:
        told.append(presenter_words(gender).render(TALKING_SCENE_CONTEXT))
    return hashlib.sha256("\n".join(told).encode()).hexdigest()[:10]
