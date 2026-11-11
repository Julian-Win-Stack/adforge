"""Whether a scene's new action still matches the ad's other scenes (Julian, Nov 8 2026).

Each scene has its own agent, which sees the plan but none of the other scenes' work. When an
agent changes its scene's action after failed tries, the new action can bring another size of
the product, or other objects, than the scenes around it (round 12: Gorilla's wood changed in
every scene). One quick text check reads the new action beside the other scenes' current
actions; a real mismatch refuses the change, as a broken plan rule does. Julian doesn't want
it too strict: a scene agent's run gets MOST_REFUSALS refusals, then a mismatch goes through
with a developer notice. No answer never blocks."""

import logging
from typing import Literal

from pydantic import BaseModel

from adforge.retry import OutsideServiceDown
from agents import loop
from agents.loop import Refused
from agents.models import ToolCall
from chat.models import Message
from gateway.gateway import call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Judgement,
    UnusableReply,
)
from jobs import library_items, notices, work
from jobs.models import Scene, SceneAgentRun
from jobs.planning import ChatMessage

from . import media

logger = logging.getLogger(__name__)

# How many changes the check refuses in one scene agent's run; after that it only records
# (Julian, Nov 8).
MOST_REFUSALS = 3
# What a refusal starts with, so a run's refusals can be counted.
MISMATCH = "the new action doesn't match the ad's other scenes:"

COHESION_INSTRUCTIONS = (
    "You check one scene of a short vertical video ad for one product. Every scene is made "
    "on its own, and the agent making one scene has just changed what it shows. You get "
    "the product: its name, the photos of the one exact product the ad shows (one version, "
    "one size) and its real size; the ad's other scenes in the order they play, each with "
    "its kind (talking: she says the line on camera; B-roll: hands and product only), its "
    "line and its action; and the changed scene's old action and its new line and action. "
    "Answer one question: does the new action still fit the other scenes, so that a viewer "
    "watching them one after another sees one product and one story? It doesn't fit only "
    "when one of these is clearly true and the owner did not ask for that difference:\n"
    "- It shows another version, size or pack of the product than the photos and the real "
    "size, or the product at a clearly different size.\n"
    "- An object another scene shows, such as the material being worked on or a pet, is in "
    "it but different: another number, material, colour, size or kind, or replaced by "
    "another object doing the same job.\n"
    "- It breaks the story's order, such as the finished result in a scene before the one "
    "that shows the problem.\n"
    "Read the whole owner conversation, whose messages are labelled user. Their latest "
    "requested change takes precedence over the old scene rows, planned photos and real "
    "size for the things they asked to change. A requested difference matches even while "
    "another scene covered by that note still has its old action. Never tell the agent "
    "to restore something the owner asked to replace. This exempts only requested "
    "differences; still check unrelated size changes, swapped objects and story drift as "
    "above. With no owner change, use the original checks unchanged.\n"
    "Everything else fits: another camera angle or framing, a closer or wider shot, a "
    "simpler action, fewer objects, a new object that takes no other scene's object's "
    "place, and an action different from the old one. When in doubt, it fits.\n"
    'Decide "matches" when it fits and "mismatch" when it doesn\'t. For "mismatch", the '
    "reason names what doesn't match, the scene it doesn't match and what to write "
    "instead, in one sentence."
)


class OtherScene(BaseModel):
    number: int
    kind: str
    line: str
    shows: str


class CohesionHandoff(Handoff):
    conversation: list[ChatMessage]
    product_name: str
    product_photos: list[str]
    real_size: str
    other_scenes: list[OtherScene]
    changed_scene: int
    old_action: str
    new_line: str
    new_action: str
    new_usage: str
    new_result: str


class Cohesion(Judgement):
    decision: Literal["matches", "mismatch"]


# What may stop the check from answering: none of it blocks the change.
NO_ANSWER = (UnusableReply, OutsideServiceDown, AnswerTookTooLong, BlockedBySafetyFilter)


def checked(scene: Scene, old_action: str, run: SceneAgentRun) -> str | None:
    """Refused when `scene`'s unsaved new action doesn't match the ad's other scenes, while
    the scene agent's `run` has refusals left. Past them, what didn't match, for a developer
    notice once the change is saved (let_through); None when it matches or the check gave no
    answer (that notice is posted here)."""
    verdict = _ask(scene, old_action)
    if verdict is None or verdict.decision == "matches":
        return None
    refused = ToolCall.objects.filter(
        agent_fix=run.agent_fix,
        tool="change_scene",
        created_at__gte=run.created_at,
        result__startswith=f"{loop.REFUSED} {MISMATCH}",
    ).count()
    if refused < MOST_REFUSALS:
        raise Refused(f"{MISMATCH} {verdict.reason}")
    return verdict.reason


def let_through(scene: Scene, reason: str) -> None:
    """The developer notice for a saved change the check found not matching, as its run's
    refusals were used."""
    notices.post_notice(
        scene.job,
        notices.SCENE_COHESION_LET_THROUGH,
        f"Scene {scene.number}'s new action went through though the cohesion check found it "
        f"doesn't match the other scenes, as this run's {MOST_REFUSALS} cohesion refusals are "
        f'used. New action: "{scene.shows}". What didn\'t match: {reason}',
        Message.Level.PROBLEM,
    )


def _ask(scene: Scene, old_action: str) -> Cohesion | None:
    job = scene.job
    library = {item.number: item for item in library_items.originals(job)}
    try:
        return call_model(
            job=job,
            purpose="check_scene_cohesion",
            instructions=COHESION_INSTRUCTIONS,
            handoff=CohesionHandoff(
                conversation=work._conversation(job),
                product_name=job.product_name,
                product_photos=[
                    library_items.library_line(library[number])
                    for number in job.product_photos
                    if number in library
                ],
                real_size=media.real_size(job),
                other_scenes=[
                    OtherScene(
                        number=other.number,
                        kind=other.kind,
                        line=other.line,
                        shows=other.shows or "the presenter says it to camera",
                    )
                    for other in job.scenes.exclude(pk=scene.pk).order_by("number")
                ],
                changed_scene=scene.number,
                old_action=old_action,
                new_line=scene.line,
                new_action=scene.shows,
                new_usage=scene.usage,
                new_result=scene.result,
            ),
            output=Cohesion,
        )
    except NO_ANSWER as error:
        logger.warning("Job %s: the cohesion check didn't answer (%s)", job.pk, error)
        notices.post_notice(
            job,
            notices.SCENE_COHESION_LET_THROUGH,
            f"The cohesion check didn't answer for scene {scene.number}'s new action "
            f"({error.__class__.__name__}), so it goes through unchecked.",
            Message.Level.PROBLEM,
        )
        return None
