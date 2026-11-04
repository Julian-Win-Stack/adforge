"""Judge a clip against its references. PRODUCT alone settles a new clip's verdict;
MOTION describes it and, after an owner note, checks the requested change (#350).
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

from . import assembly, notices
from .library import VIDEO_API_MOST_PIECE_SECONDS, SentList, sent_list_of
from .models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneAgentRun, SceneStep
from .notices import post_left_out_by_the_filter, post_notice, post_refused_by_the_filter
from .sent_labels import PIECE_LABEL
from .threads import closing_its_connection

logger = logging.getLogger(__name__)

# Each check is asked once and fails when its answer fails it (Julian 2026-10-22 21:09, "drop
# the vote"; docs/adr/0008-clip-judge-asks-once.md). On round 6's 43 graded clips one ask
# matched his grades as often as 3 asks and a vote (28 vs 29): the judge's errors are its bar,
# made by every ask alike, not noise a vote evens out (runs/gemini-judge-test/one-ask-vs-vote.md).
ASKS = 1

# MOTION's frames: the frame nearest each 1/FRAMES_PER_SECOND second, from the start.
FRAMES_PER_SECOND = 5
# PRODUCT's frames, the same way: twice as many, so it sees the moment a product is edge-on and
# the frames right before and after, where a turn's direction shows (5 stills missed every
# phone flip; 5 a second missed the direction: harness arms sides5, C8 and F1). Kept under
# "sides" in a ruling's pictures, the name of the check they were first cut for.
PRODUCT_FRAMES_PER_SECOND = 10

# ---------------------------------------------------------------- clip description

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


# Only for reading stored rulings made before #350. Never asked on new clips.
LEGACY_MOTION_QUESTIONS = ("real_motion", "sudden_change", "does_what_was_ordered")


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


SCENE_MOTION_FIX_QUESTION = """\
This clip was made again for a requested change. The producer's instruction is labelled \
producer_instruction; owner_quotes are the shop owner's words, checked word for word. \
Before the new clip's frames you are shown frames of the old clip. Answer one more question, \
owner_change: is the requested change clearly there in the new clip? Use the checked owner's \
words for what they asked and the producer's instruction for how it was handed to the scene. \
Compare the old clip with the new one only for that change. Nothing else about the old clip \
matters, and other differences between the two clips are not a fail. Answer "fixed" only if \
the change is clearly made. Answer "not fixed" if what the owner complained about is still \
there, or the change is only partly made, and say in one sentence what is still wrong."""


FIX_TRANSCRIPT_GUIDANCE = (
    "The words_heard_in_the_new_clip come from an automatic transcript of this new clip. "
    "Settle changes to the spoken words from those heard words; settle visible changes "
    "from the frames. For the words, judge only whether the owner's requested change "
    "was made. Ignore small slips a listener would not notice, including a slightly "
    "mispronounced product name."
)


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


# ---------------------------------------------------------------- a clip with its sent list

# A clip that carries the sent list its video API got (#210 point 18, #228) is judged against
# exactly that: the sent pictures, each piece's frames and the prompt, then the clip. Its words
# are the judge build brief's (handoffs/judge-build/brief.md, section 3), word for word from the
# hand test's harness (runs/hand-rounds/judge-test/run.py): PRODUCT replaces SIDES, and MOTION
# keeps today's words but for its last paragraph.

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


# A comment, not a docstring: a docstring would be sent to the judge in the answer's schema.
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


# The clip description with its sent list: replace the shop-photo input description.
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

    @model_validator(mode="after")
    def _not_shown_says_what(self) -> Self:
        if self.verdict != "enough" and not (self.references_never_show or "").strip():
            raise ValueError("A clip it can't tell about must say what the references never show.")
        return self


# Said to PRODUCT and MOTION after their words when the first question found one detail the
# references never show (spec round-7-unlisted item 11, Julian Oct 30 02:57): the checks still
# run, on everything else.
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


class FixTranscriptHandoff(SentMotionCheckHandoff):
    words_heard_in_the_new_clip: str | None = None

    @model_serializer(mode="wrap")
    def _words_only_when_heard(self, dump: SerializerFunctionWrapHandler) -> Any:
        dumped = super()._detail_only_when_there_is_one(dump)
        if dumped.get("words_heard_in_the_new_clip") is None:
            dumped.pop("words_heard_in_the_new_clip", None)
        return dumped


class SentMotionFixCheckHandoff(FixTranscriptHandoff):
    """And, for a clip made again for the shop owner's clip fix, their change word for word."""

    owner_change: str


class SceneMotionFixCheckHandoff(FixTranscriptHandoff):
    """The producer's instruction and checked owner quotes for the clip's captured run."""

    producer_instruction: str
    owner_quotes: list[str]


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
) -> dict[str, Any]:
    """Settle PRODUCT alone, keeping MOTION's description and owner-change answer.

    A failed product fails the clip; an unsure or partially refused product check is unsure;
    an unreadable product answer is not checked. A skipped product stays unsure (#290).
    MOTION cannot fail a clip or leave it unsure or unchecked, even when it can't answer.
    """
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
    return {
        "verdict": verdict,
        "checks": {} if product_skipped else {"product": verdict},
        "failed_answers": len(failures),
        "failures": failures,
        "answers": {"product": first, "motion": motion},
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
# On a fix's clip, MOTION's frames of each clip.
OLD_FRAMES_LABEL = "Frames from the old clip, the one the owner complained about, in order:"
NEW_FRAMES_LABEL = "Frames from the new clip, in order:"


SCENE_REFERENCES_FRAME = (
    "Starting picture: what the video model was given. Then you are shown the real product "
    "photos that the video model did not see, each with the line the picture model was given. "
    "Use those photos and the behaviour note to learn the real product; the starting picture "
    "is drawn, not evidence of the real product. Then the clip is shown as frames in order, "
    "each labelled with its time in seconds. The video prompt is also provided."
)


TALKING_SCENE_CONTEXT = "The woman talks on camera in this clip; her mouth moving is expected."


def check_words(words: str, *, scene: bool = False, talking: bool = False) -> str:
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
    return f"{words}\n\n{TALKING_SCENE_CONTEXT}" if talking else words


def _scene_run(step: SceneStep) -> SceneAgentRun | None:
    filmed = AgentFixTry.objects.filter(step=step, agent_fix__is_scene_agent=True).first()
    if filmed is None or filmed.sent is None:
        return None
    sent = SentList.model_validate(filmed.sent)
    if sent.scene_run is None:
        return None
    return SceneAgentRun.objects.get(pk=sent.scene_run)


def complained_about(step: SceneStep) -> ProducedItem | None:
    """For a step that makes a scene's new clip for the shop owner's clip fix, or for a fix
    with their photo of a thing that only shows during the clip, the clip they complained
    about: the one they saw last before the step started. None for any other step, or when
    the scene has no clip from before it."""
    if step.kind != SceneStep.Kind.CLIP:
        return None
    run = _scene_run(step)
    if run is not None:
        for clip in (
            step.scene.produced.filter(kind=ProducedItem.Kind.CLIP)
            .exclude(step=step)
            .order_by("-created_at", "-pk")
        ):
            previous = sent_list_of(clip)
            if previous is None or previous.scene_run != run.pk:
                return clip
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


def judge_clip(step: SceneStep, clip: ProducedItem) -> dict[str, Any]:
    """Judge the B-roll clip `step` made against the sent list its video API got, keeping the
    ruling on the clip and giving it (_judge_against_sent). Every B-roll clip keeps its sent
    list before it is judged. Scene-agent clips keep the starting picture and the separate
    real-product photos used to draw it; old clips keep only their video inputs."""
    sent = sent_list_of(clip)
    assert sent is not None, "a B-roll clip keeps its sent list before it is judged"
    return _judge_against_sent(step, clip, sent)


def _judge_against_sent(step: SceneStep, clip: ProducedItem, sent: SentList) -> dict[str, Any]:
    """Judge a B-roll clip that carries `sent`, the sent list its video API got (#210 point
    18), keeping the ruling on the clip and giving it. Every ask is shown the sent pictures in
    their order and each piece's frames (sent_references), then the clip's frames, and told the
    prompt and the planner's note on how this product behaves. The first question
    (REFERENCES_QUESTION) asks whether those show enough to judge the clip's action: when they
    don't show the product, PRODUCT is skipped and only MOTION is asked (#290): the ruling
    stays "unsure" with "cannot_tell" saying what the references never show, the clip is kept
    and the shop owner is told to check it themselves. MOTION describes the clip and checks
    a requested owner change, without settling the clip's verdict (#350). When
    they show it but not one detail, both checks are asked, told to leave that detail out,
    and the ruling's "detail_not_shown" names it for the shop owner.
    Else PRODUCT and MOTION are each asked ASKS times, all at once, and an unsure PRODUCT
    keeps the clip for the shop owner to decide. The ruling also gives "behaviour" (the note it
    was told) and, in "pictures", "references" (what it was shown of the sent list)."""
    answered: list[_Answered] = []
    try:
        ruling, answered = _asked_against_sent(step, clip, sent)
    except Exception as error:
        logger.exception("Scene %s's clip couldn't be judged", step.scene.number)
        ruling = {
            **judged([None] * ASKS, [None] * ASKS),
            **_sent_ruling_facts(step, clip, None),
            "error": str(error),
        }
    clip.ruling = ruling
    clip.save(update_fields=["ruling"])
    _say_what_the_filter_refused(step, answered)
    _say_the_judge_is_unsure(step, ruling)
    return ruling


def _asked_against_sent(
    step: SceneStep, clip: ProducedItem, sent: SentList
) -> tuple[dict[str, Any], list[_Answered]]:
    """_judge_against_sent's ruling and every answer it was given."""
    pictures: dict[str, Any] | None = (clip.ruling or {}).get("pictures")
    fresh = not pictures
    old = complained_about(step)
    if fresh:
        references = sent_references(sent)
        beside = len(references)
        beside_each_clip = MOST_PICTURES - (MOST_PICTURES - beside) // 2
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
        beside_each_clip = MOST_PICTURES - (MOST_PICTURES - len(pictures["references"])) // 2
        old_frames = _frames_beside(old, beside=beside_each_clip, cut=False) if old else None
    assert pictures is not None
    if old is not None and old_frames is None:
        logger.warning("Scene %s's fix is judged without the old clip", step.scene.number)
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
    fixing = old_frames is not None
    facts = _sent_ruling_facts(step, clip, old if fixing else None)

    asking_first = _Ask(
        "check_clip_references",
        check_words(REFERENCES_QUESTION, scene=sent.mode is not None, talking=talking),
        SentMotionCheckHandoff(ask=1, **told),
        ReferencesVerdict,
        [*shown, *_frames_shown(pictures["frames"])],
        references=0,
    )
    first = _answer(step, asking_first)
    if first.unusable:
        # An answer that broke its form (a "not shown" that doesn't say what is never shown)
        # is asked for once more.
        first = _answer(step, asking_first)
    if first.answer is None:
        post_notice(
            job,
            notices.JUDGE_FIRST_QUESTION,
            f"The clip judge couldn't ask whether scene {step.scene.number}'s references show "
            "enough to judge its clip, so it ran the product check and clip description anyway.",
            Message.Level.PROBLEM,
            scene=step.scene.number,
        )
    # References that never show the product skip the product check, which counts as
    # passing, but leave the clip unsure (#290); still describe it and check an owner change.
    cannot_tell = (
        first.answer["references_never_show"]
        if first.answer is not None and first.answer["verdict"] == "product not shown"
        else None
    )
    # One detail the references never show is left out of both checks (item 11).
    detail = (
        first.answer["references_never_show"]
        if first.answer is not None and first.answer["verdict"] == "detail not shown"
        else None
    )
    left_out = f"\n\n{DETAIL_LEFT_OUT}" if detail is not None else ""
    if detail is not None:
        told["detail_the_references_never_show"] = detail
    motion_context = check_words(
        MOTION_FROM_REFERENCES, scene=sent.mode is not None, talking=talking
    )
    run = _scene_run(step)
    fix_question = SCENE_MOTION_FIX_QUESTION if run is not None else MOTION_FIX_QUESTION
    # Transcription has already finished before the word check and judge run. Read only
    # this clip's kept transcript; never start another call or wait for one here.
    heard = (
        ProducedItem.objects.filter(kind=ProducedItem.Kind.TRANSCRIPT, made_from=clip)
        .order_by("-created_at", "-pk")
        .values_list("text", flat=True)
        .first()
        if fixing
        else None
    )
    if heard:
        fix_question += f"\n\n{FIX_TRANSCRIPT_GUIDANCE}"
    motion_words = f"{motion_context}\n\n{fix_question}" if fixing else motion_context
    product_asks = [
        _Ask(
            "check_clip_product",
            f"{check_words(PRODUCT_CHECK, scene=sent.mode is not None, talking=talking)}{left_out}",
            SentClipCheckHandoff(ask=ask, **{k: v for k, v in told.items() if k != "order"}),
            ProductVerdict,
            [*shown, *_frames_shown(pictures["sides"])],
            references=len(shown),
            frames=_times(pictures["sides"]),
        )
        for ask in range(1, ASKS + 1)
        if cannot_tell is None
    ]
    asks = product_asks + [
        _Ask(
            "check_clip_motion",
            f"{motion_words}{left_out}",
            (
                SceneMotionFixCheckHandoff(
                    ask=ask,
                    **told,
                    words_heard_in_the_new_clip=heard,
                    producer_instruction=run.instruction,
                    owner_quotes=[quote["text"] for quote in run.quotes],
                )
                if run is not None
                else SentMotionFixCheckHandoff(
                    ask=ask,
                    **told,
                    words_heard_in_the_new_clip=heard,
                    owner_change=owner_change_of(step),
                )
            )
            if fixing
            else SentMotionCheckHandoff(ask=ask, **told),
            MotionFixVerdict if fixing else MotionVerdict,
            [*shown, *_frames_shown(pictures["frames"], old=old_frames)],
            references=len(shown),
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
    product, motion = answers[: len(product_asks)], answers[len(product_asks) :]
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
            blind=[(check, ask) for check, ask, _ in blinded],
            product_skipped=cannot_tell is not None,
        ),
        **facts,
        **_past_the_filter(product, motion),
        **({"refused_frames": blind} if blind else {}),
        **({"detail_not_shown": detail} if detail is not None else {}),
    }
    if cannot_tell is not None:
        ruling["cannot_tell"] = cannot_tell
    ruling["answers"]["references"] = first.answer
    # The first question's own refusal is told by the notice above, not as the clip's.
    return ruling, answers


def _sent_ruling_facts(
    step: SceneStep, clip: ProducedItem, compared_with: ProducedItem | None
) -> dict[str, Any]:
    """What a ruling against a sent list says beside its verdict: its words and models, the
    pictures it was shown, the behaviour note it was told and, for a fix's clip, the clip it
    was compared with."""
    sent = sent_list_of(clip)
    return {
        "wording": wording_version(
            fix=compared_with is not None,
            scene=sent is not None and sent.mode is not None,
            talking=(step.scene.kind == Scene.Kind.TALKING),
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
    fixing agent. A scene-agent clip whose checks all failed to answer is also kept for
    the owner to check. A clip judged with one detail left out, as the references never show it, is
    told the same way when it passes, naming the detail. Seconds of the clip the safety filter
    refused are told in red, as every refusal is, said to be kept only when the clip is."""
    number = step.scene.number
    job = step.scene.job
    if ruling["verdict"] == "not_checked" and _scene_run(step) is not None:
        _ask_to_watch(job, number, ": I couldn't check this clip; please check it yourself")
    kept = ruling["verdict"] == "unsure"
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
        if all(warning["text"] != text for warning in job.warnings):
            post_notice(job, notices.CHECK_IT_YOURSELF, text, Message.Level.INFO, scene=number)
    detail = (ruling.get("detail_not_shown") or "").strip().rstrip(".")
    if detail and ruling["verdict"] == "pass":
        text = (
            f"Please watch scene {number}'s clip: I checked it, but your photos never show "
            f"{detail}, so I couldn't check that part."
        )
        if all(warning["text"] != text for warning in job.warnings):
            post_notice(job, notices.CHECK_IT_YOURSELF, text, Message.Level.INFO, scene=number)
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
            _ask_to_watch(job, number, f" at {at}: I couldn't check those seconds")


def _times(pictures: list[dict[str, Any]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(picture["times"]) for picture in pictures)


def _past_the_filter(product: list[_Answered], motion: list[_Answered]) -> dict[str, Any]:
    """What a ruling says of the safety filter (requirement 22), when it refused a reference
    picture: "not_shown", each ask answered without reference pictures it refused, and which.
    A refused frame of the clip is the ruling's "refused_frames"; an ask is never refused
    whole, as its refused frames are left out (_past_refused_frames)."""
    asked = [("product", ask, each) for ask, each in enumerate(product, 1)] + [
        ("motion", ask, each) for ask, each in enumerate(motion, 1)
    ]
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


def _ask_to_watch(job: Job, number: int, why: str) -> None:
    """Ask the shop owner, once, to watch scene `number`'s clip, kept though the safety filter
    stopped part of its check, saying `why` (" at 3 s: I couldn't check those seconds"). The
    refusal itself is the developer's, recorded apart."""
    text = f"Please watch scene {number}'s clip{why}."
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, notices.CHECK_IT_YOURSELF, text, Message.Level.INFO, scene=number)


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
        if ask.purpose == "check_clip_motion":
            post_notice(
                step.scene.job,
                notices.CLIP_DESCRIPTION_UNAVAILABLE,
                f"The clip judge couldn't describe scene {step.scene.number}'s clip or check "
                "any requested change. The product check still decides its verdict.",
                Message.Level.PROBLEM,
                scene=step.scene.number,
            )
        return _Answered(None, [], unusable=isinstance(error, UnusableReply))


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
STARTING_PICTURE_LABEL = "Starting picture: what the video model was given."
PICTURE_PHOTO_LABEL = "Real product photo {}, sent to the picture model, not the video model: {}"
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
                {"label": PICTURE_PHOTO_LABEL.format(n, photo.label), "key": photo.file}
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


def wording_version(*, fix: bool = False, scene: bool = False, talking: bool = False) -> str:
    """A version of everything the judge is told, kept with each ruling: the first question,
    PRODUCT and MOTION's words, the behaviour note's frame, how many frames a second each check
    is shown, the sent list's labels and its handoffs and answer forms. It changes whenever
    any of them does. For a fix's clip (`fix`), also the question about the owner's change,
    the labels of the two clips, its handoff and its answer form. Talking clips also include
    the sentence that tells the judge to expect on-camera talking."""
    told = [
        f"product {PRODUCT_FRAMES_PER_SECOND} frames a second, motion and the first question "
        f"{FRAMES_PER_SECOND}, at most {PIECE_FRAMES_SHOWN} of the pieces' frames",
        REFERENCES_QUESTION,
        PRODUCT_CHECK,
        MOTION_FROM_REFERENCES,
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
                MotionVerdict,
            )
        ),
    ]
    if scene:
        told += [SCENE_REFERENCES_FRAME, STARTING_PICTURE_LABEL, PICTURE_PHOTO_LABEL]
    if talking:
        told.append(TALKING_SCENE_CONTEXT)
    if fix:
        told += [
            SCENE_MOTION_FIX_QUESTION if scene else MOTION_FIX_QUESTION,
            FIX_TRANSCRIPT_GUIDANCE,
            OLD_FRAMES_LABEL,
            NEW_FRAMES_LABEL,
            *(
                json.dumps(form.model_json_schema(), sort_keys=True)
                for form in (
                    SceneMotionFixCheckHandoff if scene else SentMotionFixCheckHandoff,
                    MotionFixVerdict,
                )
            ),
        ]
    return hashlib.sha256("\n".join(told).encode()).hexdigest()[:10]
