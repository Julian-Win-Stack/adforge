"""The whole-plan rules for B-roll and talking scenes (spec #401, ticket #403), as functions
over the scenes, so the planner's prompt, its notice or refusal, a plan edit and a scene agent's
change_scene all hold a plan to the same rules in the same words.

Each rule's words are written once here: `PLAN_INSTRUCTIONS` and the producer's instructions
quote them. Only the rules in `REFUSED_AT_PLANNING` send a plan back (the #390 pattern); a plan
breaking any other goes ahead with a developer notice. One rule, `DIFFERENT_STYLE_AND_STATE`, is
only told, never checked."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

# How a talking scene is filmed: one of two camera setups, selfie or propped, with "talking
# while doing it" filmed propped (Julian, Nov 8 2026, thread "Test talking scene camera
# angles"). No mirrors (the judge flags them and the video model gets them wrong). The
# walking ban had no graded failure behind it and was deleted (Julian, Nov 9).
TalkingStyle = Literal["selfie", "propped", "talking while doing it"]
TALKING_STYLES: tuple[str, ...] = get_args(TalkingStyle)

# Where the product is in a talking scene. "Out of the box" is the product alone, no box
# shown; the two box states need a page photo of the packaging.
ProductState = Literal["in its box", "out of the box", "in use", "beside its box"]
PRODUCT_STATES: tuple[str, ...] = get_args(ProductState)
BOX_STATES = ("in its box", "beside its box")

# The one talking style a middle scene may have.
DOING_IT: TalkingStyle = "talking while doing it"


def _quoted(values: Iterable[str]) -> str:
    quoted = [f'"{value}"' for value in values]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


# The two camera setups a talking scene is filmed in, each written out in full: who holds the
# phone or where it stands, the framing, the camera height, the hands and the eyes.
SETUP_LOOKS: dict[str, str] = {
    "selfie": (
        "a vertical phone selfie video frame from her front camera, the phone held by her at "
        "arm's length, close to her, a little above her eye level and angled slightly down; "
        "her phone arm reaches out of the frame toward the camera, her shoulder and upper arm "
        "in view at the frame's edge; a medium close-up taken close to her face through the "
        "phone's wide front lens: her face takes up about a third of the frame's width, the top "
        "of her head near the top edge, her eyes in the upper third, the frame cut at mid-chest, "
        "a little off-centre; with her other hand she holds the product up beside her face at "
        "chest height, close to the lens and large in the lower third of the frame; she talks "
        "to the lens mid-sentence"
    ),
    "propped": (
        "a vertical phone video frame from a phone set down on a counter or table in front of "
        "her, nobody holding it, the camera a little below her eye level; a medium shot from "
        "the waist up, the frame cut at her waist, the edge of the counter or table along the "
        "bottom; both her hands are free and in frame; her eyes mostly on what her hands are "
        "doing, with glances at the lens"
    ),
}
# A talking scene of her using a skin product shows no applying the page never shows (root
# cause 4 part 1, Julian, Nov 10 2026 00:13: a B-roll give-up came back from the producer as
# a talking scene of her rubbing foam on). The same rule as planning.SPREAD_ON_BODY_RULE,
# for that talking style.
SPREAD_WHILE_TALKING = (
    "for a cream, foam, gel, serum, lotion, cleanser or makeup spread on the body that no page "
    "photo or video shows being applied, she shows the result where it touched or a dab on "
    "her fingertip instead"
)
# What each talking style and product state looks like, for the planner and for the
# starting picture drawn in them. "Talking while doing it" needs both hands free, so it is
# filmed propped.
STYLE_LOOKS: dict[str, str] = {
    "selfie": SETUP_LOOKS["selfie"],
    "propped": SETUP_LOOKS["propped"],
    DOING_IT: (
        f"{SETUP_LOOKS['propped']}; she uses the product on herself as she talks "
        f"({SPREAD_WHILE_TALKING})"
    ),
}
STATE_LOOKS: dict[str, str] = {
    "in its box": "the product still in its box, the box as the packaging photo shows it",
    "out of the box": "the product alone, out of its box, no box shown",
    "in use": "the product being used",
    "beside its box": "the product out, its box beside it as the packaging photo shows it",
}


def styles_said() -> str:
    """Each talking style with what it looks like, for the planner."""
    return "; ".join(f'"{style}": {looks}' for style, looks in STYLE_LOOKS.items())


# Each rule as the planner is told it and as a plan breaking it is noticed or sent back.
# The opening-kind rule, in the words the planner's prompt says it (jobs.planning); the plan's
# own validators and check_opening_kind refuse a first scene that isn't talking.
FIRST_IS_TALKING = "The first scene is always the person talking to camera."
AT_LEAST_ONE_BROLL = (
    "Every ad of three or more scenes has at least one B-roll scene, centred on the product. "
    "When a real demo is hard, film before and after using it, the feature up close or the "
    "feeling after, such as a greasy stovetop before a degreaser and spotless after it."
)
MIDDLE_IS_BROLL = (
    "The scenes between the first and the last are B-roll by default: one of them may be "
    f'talking only in the style "{DOING_IT}", with her using the product on herself '
    f"({SPREAD_WHILE_TALKING})."
)
# The last scene does the emotional after, then the casual ask, on camera (Julian, Nov 8
# 2026, thread "Design emotional after in ads").
LAST_IS_TALKING = (
    "The last scene is always her talking to camera: it says the after, then the casual ask."
)
# Said the same wherever showing the product to the lens comes up: the planner's prompt, its
# notice and the scene agent's instructions (Julian's OK, Nov 6 05:59: it narrows the proven
# talking-scene exception, docs/agents/proven-broll-rules.md).
LENS_ONLY = "only the first or the last scene may just show the product to the lens"
LENS_FIRST_OR_LAST = (
    f"{LENS_ONLY[0].upper()}{LENS_ONLY[1:]}: set shows_to_lens true for a scene whose action "
    "only shows the product to the lens."
)
STYLE_AND_STATE = (
    f"Each talking scene names its talking style, {_quoted(TALKING_STYLES)}, and its "
    f"product state, {_quoted(PRODUCT_STATES)}. No mirrors."
)
# Told to the planner and the producer, never checked: it compared labels, not pictures, and it
# refused 7 of round 12's 14 refused producer fixes, which then lost a price or a demo (Julian,
# Nov 8 2026, thread "Stop rule drift after planning"). Telling the producer each talking
# scene's style and state (agents/producer.py:_script) is how it keeps to it.
DIFFERENT_STYLE_AND_STATE = (
    "A talking scene's camera setup differs from the previous talking scene's setup (selfie "
    f'or propped; "{DOING_IT}" is filmed propped), and its product state differs from the '
    "previous talking scene's state, even with B-roll between them."
)
BOX_NEEDS_PACKAGING = (
    'The box states "in its box" and "beside its box" need a page photo that shows the '
    "packaging, given "
    'by its library number as packaging_photo; "out of the box" means the product alone, no '
    "box shown, and needs none."
)
# Told to the planner, never checked: the code can't tell which library photo shows
# packaging, so the planner decides from the photos' labels.
BOX_AND_PRODUCT = (
    "When a library photo shows the product's packaging, the talking scene with the closing "
    'line shows the box ("in its box" or "beside its box", with that photo as '
    'packaging_photo), and another talking scene shows the product itself ("out of the box" '
    'or "in use"). With no packaging photo, no talking scene is in a box state.'
)

# Every rule, in the order the planner is told them.
RULES = (
    AT_LEAST_ONE_BROLL,
    MIDDLE_IS_BROLL,
    LAST_IS_TALKING,
    LENS_FIRST_OR_LAST,
    STYLE_AND_STATE,
    DIFFERENT_STYLE_AND_STATE,
    BOX_NEEDS_PACKAGING,
    BOX_AND_PRODUCT,
)


@dataclass(frozen=True)
class PlanScene:
    """What the whole-plan rules read of one scene. `style` and `state` are blank or None
    when not given; a B-roll scene's are never read."""

    number: int
    broll: bool
    style: str | None
    state: str | None
    shows_to_lens: bool
    packaging_photo: int | None


@dataclass(frozen=True)
class Broken:
    """A rule a plan breaks: which rule, and where, such as "Scene 2 is ...", said before the
    rule's own words."""

    rule: str
    where: str

    @property
    def why(self) -> str:
        return f"{self.where} {self.rule}"


# How a plan edit or a scene agent's change that breaks a rule is refused, before why.
WOULD_BREAK = "the plan would break a rule:"


def said(broken: Sequence[Broken]) -> str | None:
    """Every broken rule, each said once after every place that breaks it, in the order of
    `broken`, or None for none: one answer names all of them, so a plan breaking several is
    fixed in one go."""
    rules: dict[str, list[str]] = {}
    for each in broken:
        rules.setdefault(each.rule, []).append(each.where)
    return " ".join(" ".join([*wheres, rule]) for rule, wheres in rules.items()) or None


def rules_broken(scenes: Sequence[PlanScene]) -> list[Broken]:
    """Every whole-plan rule `scenes`, in the order they play, break, first broken first."""
    scenes = sorted(scenes, key=lambda scene: scene.number)
    return [
        *_no_broll(scenes),
        *_middle_talking(scenes),
        *_last_broll(scenes),
        *_lens_in_the_middle(scenes),
        *_style_or_state_missing(scenes),
        *_box_with_no_packaging(scenes),
    ]


# The whole-plan rules a new plan is still sent back for. Every other rule is only told: none
# refused a plan in round 12, so it goes ahead and what it breaks is a developer notice (Julian,
# Nov 9 2026, rules review: a check that never fired is deleted, its rule stays told).
REFUSED_AT_PLANNING = (LAST_IS_TALKING,)


def refused_at_planning(scenes: Sequence[PlanScene]) -> str | None:
    """Why a new plan of `scenes` is sent back: every rule in REFUSED_AT_PLANNING it breaks,
    or None."""
    return said([each for each in rules_broken(scenes) if each.rule in REFUSED_AT_PLANNING])


def told_rules_broken(scenes: Sequence[PlanScene]) -> str | None:
    """Every whole-plan rule a new plan of `scenes` breaks that isn't sent back for, or None."""
    return said([each for each in rules_broken(scenes) if each.rule not in REFUSED_AT_PLANNING])


def newly_broken(before: Sequence[PlanScene], after: Sequence[PlanScene]) -> str | None:
    """Why `after` breaks whole-plan rules that `before` didn't, every one, or None. An edit
    of a plan made before a rule existed is held only to the rules it kept."""
    return said(_newly(before, after))


def _newly(before: Sequence[PlanScene], after: Sequence[PlanScene]) -> list[Broken]:
    """The rules `after` breaks that `before` kept, as newly_broken holds an edit to them."""
    kept = {broken.rule for broken in rules_broken(before)}
    return [broken for broken in rules_broken(after) if broken.rule not in kept]


# The whole-plan rules a change to one scene after planning is still refused for: the last
# scene's after (LAST_IS_TALKING) and what a talking scene's starting picture is drawn from.
# Every other rule such a change newly breaks is let through with a developer notice, so a
# scene agent's fallback or an edit isn't refused and the scene lost (Julian, Nov 9 2026,
# rules review rule 1: round 12's KONG scene 3 was dropped by the lens rule).
STILL_REFUSE_A_SCENE_CHANGE = (LAST_IS_TALKING, STYLE_AND_STATE, BOX_NEEDS_PACKAGING)


@dataclass(frozen=True)
class SceneChangeBreaks:
    """What a change to one scene newly breaks: why it is `refused`, and the rules it is
    `let_through` with, each said as `said` says them, or None for none."""

    refused: str | None
    let_through: str | None


def a_scene_change_breaks(
    before: Sequence[PlanScene], after: Sequence[PlanScene], *, owner_asked: bool = False
) -> SceneChangeBreaks:
    """What changing one scene, making the plan `after` where it was `before`, newly breaks:
    a rule in STILL_REFUSE_A_SCENE_CHANGE refuses it, as newly_broken holds an edit to it.
    Any other break `before` didn't have, in that place, is let through, so a second scene
    breaking a rule already let through is recorded too."""
    refused_rules = (
        (STYLE_AND_STATE, BOX_NEEDS_PACKAGING) if owner_asked else STILL_REFUSE_A_SCENE_CHANGE
    )
    had = set(rules_broken(before))
    return SceneChangeBreaks(
        refused=said([each for each in _newly(before, after) if each.rule in refused_rules]),
        let_through=said(
            [
                each
                for each in rules_broken(after)
                if each not in had and each.rule not in refused_rules
            ]
        ),
    )


def broll_can_talk(scenes: Sequence[PlanScene], number: int) -> str | None:
    """How B-roll scene `number` of `scenes` could be said to camera instead by the rules:
    "talking" for the first or last scene, the style "talking while doing it" for a middle
    one, or None when it is the plan's only B-roll scene."""
    if not any(scene.broll and scene.number != number for scene in scenes):
        return None
    numbers = sorted(scene.number for scene in scenes)
    return "talking" if number in (numbers[0], numbers[-1]) else DOING_IT


def _middle(scenes: Sequence[PlanScene]) -> Sequence[PlanScene]:
    return scenes[1:-1]


def _no_broll(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    # An ad of one or two scenes is its first and last scenes alone, which always talk
    # (Julian, Nov 8 2026: the last scene talking wins over B-roll in a two-scene ad).
    if len(scenes) > 2 and not any(scene.broll for scene in scenes):
        yield Broken(AT_LEAST_ONE_BROLL, "The plan has no B-roll scene.")


def _middle_talking(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    for scene in _middle(scenes):
        if not scene.broll and scene.style != DOING_IT:
            yield Broken(
                MIDDLE_IS_BROLL,
                f"Scene {scene.number} is a middle scene talking in another style.",
            )


def _last_broll(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    # A one-scene ad's only scene is its first, held to talking by its own rule.
    if len(scenes) > 1 and scenes[-1].broll:
        yield Broken(LAST_IS_TALKING, f"Scene {scenes[-1].number}, the last, is B-roll.")


def _lens_in_the_middle(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    for scene in _middle(scenes):
        if scene.shows_to_lens:
            yield Broken(
                LENS_FIRST_OR_LAST,
                f"Scene {scene.number} only shows the product to the lens.",
            )


def _style_or_state_missing(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    for scene in scenes:
        if scene.broll:
            continue
        if scene.style not in TALKING_STYLES or scene.state not in PRODUCT_STATES:
            yield Broken(
                STYLE_AND_STATE,
                f"Scene {scene.number} is a talking scene without a talking style or a product "
                f"state from the lists.",
            )


def _box_with_no_packaging(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    for scene in scenes:
        if not scene.broll and scene.state in BOX_STATES and scene.packaging_photo is None:
            yield Broken(
                BOX_NEEDS_PACKAGING,
                f'Scene {scene.number} is "{scene.state}" with no packaging photo.',
            )
