"""A scene's rules by its place in the ad, written once and sent to every step that can
change a scene after planning (Julian, Nov 8 2026, thread "Stop rule drift after planning",
part 3): the fact check's rewrite, the line fitter, the script shortener, the producer and
the scene agent. Each rule is an existing one, in the words the planner is told it; nothing
here refuses anything."""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from .models import Job, Scene
from .plan_rules import LENS_FIRST_OR_LAST, MIDDLE_IS_BROLL
from .planning import (
    ACTION_IN_NO_OTHER_SCENE,
    BROLL_ONE_ACTION_INSTRUCTIONS,
    BROLL_SELLS_RULE,
    HOW_TO_METHOD,
    KEEPS_THE_HOOK,
    KEEPS_THE_LAST_SCENE,
    RESULT_SHOWN,
)

# A B-roll scene's rules, in the order they are said.
BROLL_RULES = (
    BROLL_SELLS_RULE,
    BROLL_ONE_ACTION_INSTRUCTIONS.strip(),
    ACTION_IN_NO_OTHER_SCENE[0].upper() + ACTION_IN_NO_OTHER_SCENE[1:],
    RESULT_SHOWN,
    HOW_TO_METHOD,
    LENS_FIRST_OR_LAST,
)
# A talking scene between the first and the last.
MIDDLE_TALKING_RULES = (MIDDLE_IS_BROLL,)
# The rules here that are whole-plan rules too: a step told the whole-plan rules, such as
# the producer, leaves them out of the per-scene ones so it isn't told them twice.
ALSO_WHOLE_PLAN_RULES = (LENS_FIRST_OR_LAST, MIDDLE_IS_BROLL)


@dataclass(frozen=True)
class Place:
    """Where a scene is in the ad, and whether it is B-roll."""

    number: int
    broll: bool


def rules_for(places: Iterable[Place], scenes: int, *, leave_out: Collection[str] = ()) -> str:
    """The rules of the scenes at `places` in an ad of `scenes` scenes, each group said once
    with the scenes it is for, leaving out the rules in `leave_out` (a step that already
    carries one in its own words). Blank when no rule applies."""
    places = list(places)
    numbers = sorted({place.number for place in places})
    broll = [place.number for place in places if place.broll]
    middle_talking = [
        place.number for place in places if not place.broll and 1 < place.number < scenes
    ]
    groups: list[tuple[str, tuple[str, ...]]] = []
    if 1 in numbers:
        groups.append(("Scene 1, the hook", (KEEPS_THE_HOOK,)))
    if scenes in numbers:
        groups.append((f"Scene {scenes}, the last scene", (KEEPS_THE_LAST_SCENE,)))
    if broll:
        groups.append((_scenes_named(broll, "a B-roll scene"), BROLL_RULES))
    if middle_talking:
        groups.append(
            (_scenes_named(middle_talking, "a middle talking scene"), MIDDLE_TALKING_RULES)
        )
    return _said(groups, leave_out)


def rules_by_place(*, leave_out: Collection[str] = ()) -> str:
    """Every place's rules, for a step that can write or change any scene: the producer."""
    return _said(
        [
            ("The first scene, the hook", (KEEPS_THE_HOOK,)),
            ("The last scene", (KEEPS_THE_LAST_SCENE,)),
            ("A B-roll scene", BROLL_RULES),
            ("A talking scene between the first and the last", MIDDLE_TALKING_RULES),
        ],
        leave_out,
    )


def _scenes_named(numbers: list[int], kind: str) -> str:
    if len(numbers) == 1:
        return f"Scene {numbers[0]}, {kind}"
    listed = ", ".join(str(number) for number in numbers[:-1])
    return f"Scenes {listed} and {numbers[-1]}, each {kind}"


def _said(groups: list[tuple[str, tuple[str, ...]]], leave_out: Collection[str]) -> str:
    said = []
    for who, rules in groups:
        kept = [rule for rule in rules if rule not in leave_out]
        if kept:
            said.append(f"{who}: " + " ".join(kept))
    if not said:
        return ""
    return "Keep each scene's rules for its place in the ad.\n" + "\n".join(said)


def scene_rules_of(job: Job, scenes: Iterable[Scene], *, leave_out: Collection[str] = ()) -> str:
    """The rules of `job`'s `scenes` by their places in its ad."""
    return rules_for(
        [Place(scene.number, scene.kind == Scene.Kind.BROLL) for scene in scenes],
        job.scenes.count(),
        leave_out=leave_out,
    )
