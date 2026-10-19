"""The plan's critic: once the planner plans, each B-roll scene is checked before anything is
drawn or filmed (round 5 causes 1 and 2, runs/round-5/root-cause). Is the product used the way
it is really used, and does the scene's one action last the whole clip? A failing scene goes
back to the planner once with the critic's notes; one that fails again is said to camera.
Julian 2026-10-19 03:37: "yes" to the critic's two questions."""

import logging
import math
from typing import Literal

from adforge.retry import OutsideServiceDown
from gateway.gateway import call_model
from gateway.types import AnswerTookTooLong, Image, Judgement, UnusableReply

from .models import Job
from .planning import PLAN_INSTRUCTIONS, Plan, PlanHandoff, ProducerDecision

logger = logging.getLogger(__name__)

CRITIC_INSTRUCTIONS = """\
You check one B-roll scene of a plan for a short vertical video ad, before anything is drawn \
or filmed. A video model will make the scene as one clip of one continuous shot from a still \
camera, lasting the number of seconds you are given. A video model invents whatever nobody \
showed it, and it breaks the laws of physics when an action is over before its clip ends. \
You get the product page's visible text, the conversation with the shop owner, each message \
labelled "user" for the shop owner or "producer", the whole plan, the number of the scene to \
check, how many seconds its clip lasts, and the product photos, each labelled with its \
number. Answer two questions about that scene.
- shown_how: is the product used in this scene the way it is really used? "a photo shows it" \
when a product photo shows it used that way; "the text says how it is held and moved" when \
the page's text or the shop owner's own words say which hand holds it, what moves and what \
it touches; "anyone can picture it" when it is an ordinary use anyone can picture from the \
words, such as pouring from a bottle or zipping up a jacket; "not shown how" when none of \
these holds, because naming a use, listing its steps or describing it in general words \
doesn't show how; "not used in it" when the scene doesn't show the product being used.
- fills_clip: does the scene's one action keep going for the whole clip, by its own course, \
such as a steady pour or a walk, or by what it naturally sets off in the same scene, such as \
a cushion tossed onto a sofa that bounces and settles? False when it is over in a moment, \
even though it is an action, so the video would have to invent more to fill the time.
Set decision to "checked". The reason says in one plain sentence what you found, for the \
planner to fix the scene."""

REPLAN_INSTRUCTIONS = (
    PLAN_INSTRUCTIONS
    + """
This time you also get your earlier plan for this ad and a checker's notes on some of its \
B-roll scenes. Plan the ad again: change each scene a note names so the note no longer holds, \
and keep every other scene as it was in the earlier plan. For a scene the page doesn't show \
how to do, prove its line with another single action that is shown how, or ask the shop \
owner as above, in one question naming each use; when they already said to go ahead without \
one, the person says that line to camera. For a scene whose action is over before its clip \
ends, give it another single action that lasts the whole clip, or the person says that line \
to camera. The notes are not the shop owner's words: never take a fact from them."""
)

# A line spoken at an easy pace, before the voice that says it exists to measure: an estimate
# for the critic only. A clip is never shorter than 5 seconds.
WORDS_PER_SECOND = 2.5
SHORTEST_CLIP_SECONDS = 5


class CriticHandoff(PlanHandoff):
    plan: Plan
    scene_to_check: int
    clip_seconds: int


class SceneCheck(Judgement):
    decision: Literal["checked"]
    shown_how: Literal[
        "a photo shows it",
        "the text says how it is held and moved",
        "anyone can picture it",
        "not shown how",
        "not used in it",
    ]
    fills_clip: bool


class ReplanHandoff(PlanHandoff):
    earlier_plan: Plan
    checker_notes: list[str]


def clip_seconds(line: str) -> int:
    return max(SHORTEST_CLIP_SECONDS, math.ceil(len(line.split()) / WORDS_PER_SECOND))


def _note(number: int, check: SceneCheck) -> str | None:
    """What the planner is told about a failing scene, or None when it passes."""
    if check.shown_how == "not shown how":
        return f"Scene {number}: the page doesn't show how this is done. {check.reason}"
    if not check.fills_clip:
        return f"Scene {number}: its one action is over before the clip ends. {check.reason}"
    return None


def _notes(
    job: Job, handoff: PlanHandoff, plan: Plan, numbers: list[int], images: list[Image]
) -> dict[int, str]:
    """The critic's note on each of the scenes `numbers` that fails, by its number. A scene
    the critic couldn't answer for is kept unchecked, and said so in the log."""
    notes = {}
    for number in numbers:
        scene = plan.scenes[number - 1]
        try:
            check = call_model(
                job=job,
                purpose="check_plan_scene",
                instructions=CRITIC_INSTRUCTIONS,
                handoff=CriticHandoff(
                    **handoff.model_dump(),
                    plan=plan,
                    scene_to_check=number,
                    clip_seconds=clip_seconds(scene.line),
                ),
                output=SceneCheck,
                images=images,
            )
        except (UnusableReply, OutsideServiceDown, AnswerTookTooLong) as error:
            logger.warning("Scene %s of the plan went unchecked by the critic: %s", number, error)
            continue
        if (note := _note(number, check)) is not None:
            notes[number] = note
    return notes


def _broll(plan: Plan) -> list[int]:
    return [number for number, scene in enumerate(plan.scenes, start=1) if scene.shows]


def _said_to_camera(plan: Plan, numbers: set[int]) -> Plan:
    """The plan with scenes `numbers` said to camera. A second state left with no first is
    a scene of its own."""
    scenes = []
    for number, scene in enumerate(plan.scenes, start=1):
        if number in numbers:
            scene = scene.model_copy(
                update=dict(
                    shows=None,
                    broll_kind=None,
                    person_shown=None,
                    usage=None,
                    result=None,
                    needs=[],
                    thin_film=False,
                    second_state=False,
                )
            )
        elif scene.second_state and number - 1 in numbers:
            scene = scene.model_copy(update=dict(second_state=False))
        scenes.append(scene)
    return Plan.model_validate({**plan.model_dump(), "scenes": [s.model_dump() for s in scenes]})


def checked(
    job: Job,
    decision: ProducerDecision,
    handoff: PlanHandoff,
    output: type[ProducerDecision],
    images: list[Image],
) -> ProducerDecision:
    """The planner's decision once the critic has checked each of its B-roll scenes: as it
    was when every scene passes; otherwise the planner's second try, which may ask the shop
    owner, with any scene that fails again said to camera."""
    plan = decision.plan
    if plan is None:
        return decision
    notes = _notes(job, handoff, plan, _broll(plan), images)
    if not notes:
        return decision
    again = call_model(
        job=job,
        purpose="replan_ad",
        instructions=REPLAN_INSTRUCTIONS,
        handoff=ReplanHandoff(
            **handoff.model_dump(), earlier_plan=plan, checker_notes=list(notes.values())
        ),
        output=output,
        images=images,
    )
    if again.plan is None:
        return again
    rechecked = [number for number in _broll(again.plan) if number in notes]
    failed = set(_notes(job, handoff, again.plan, rechecked, images))
    if not failed:
        return again
    return again.model_copy(update=dict(plan=_said_to_camera(again.plan, failed)))
