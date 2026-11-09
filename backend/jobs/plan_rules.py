"""The whole-plan rules for B-roll and talking scenes (spec #401, ticket #403), as functions
over the scenes, so the planner's prompt, its refusal, a plan edit and a scene agent's
change_scene all hold a plan to the same rules in the same words.

Each rule's words are written once here: `PLAN_INSTRUCTIONS` quotes them and a plan that
breaks one is sent back with them (the #390 pattern)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

# How a talking scene is filmed: the presenter's pose and framing. No mirrors (the judge flags
# them and the video model gets them wrong; Julian, Nov 6). The walking ban had no graded
# failure behind it and was deleted (Julian, Nov 9).
TalkingStyle = Literal["selfie", "propped waist-up", "seated close-up", "talking while doing it"]
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


# What each talking style and product state looks like, for the planner and for the
# starting picture drawn in them.
STYLE_LOOKS: dict[str, str] = {
    "selfie": "she holds the phone out in one hand, the product in her other hand",
    "propped waist-up": "the phone stands propped up and films her from the waist up",
    "seated close-up": "she sits and is filmed close, from the chest up",
    DOING_IT: "she uses the product on herself as she talks",
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


# Each rule as the planner is told it and as a plan breaking it is sent back.
AT_LEAST_ONE_BROLL = (
    "Every ad of three or more scenes has at least one B-roll scene, centred on the product. "
    "When a real demo is hard, film before and after using it, the feature up close or the "
    "feeling after, such as dry lips before a lip balm and soft lips after it."
)
MIDDLE_IS_BROLL = (
    "The scenes between the first and the last are B-roll by default: one of them may be "
    f'talking only in the style "{DOING_IT}", with her using the product on herself.'
)
# The last scene does the emotional after, then the casual ask, on camera (Julian, Nov 8
# 2026, thread "Design emotional after in ads").
LAST_IS_TALKING = (
    "The last scene is always her talking to camera: it says the after, then the casual ask."
)
# Said the same wherever showing the product to the lens comes up: the planner's prompt, its
# refusal and the scene agent's instructions (Julian's OK, Nov 6 05:59: it narrows the proven
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
DIFFERENT_STYLE_AND_STATE = (
    "A talking scene's style differs from the previous talking scene's style, and its "
    "product state differs from the previous talking scene's state, even with B-roll between "
    "them."
)
BOX_NEEDS_PACKAGING = (
    'The box states "in its box" and "beside its box" need a page photo that shows the '
    "packaging, given "
    'by its library number as packaging_photo; "out of the box" means the product alone, no '
    "box shown, and needs none."
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
        *_same_style_or_state(scenes),
        *_box_with_no_packaging(scenes),
    ]


def rule_broken(scenes: Sequence[PlanScene]) -> str | None:
    """Why `scenes` break the whole-plan rules, every one they break, or None."""
    return said(rules_broken(scenes))


def newly_broken(before: Sequence[PlanScene], after: Sequence[PlanScene]) -> str | None:
    """Why `after` breaks whole-plan rules that `before` didn't, every one, or None. An edit
    of a plan made before a rule existed is held only to the rules it kept."""
    kept = {broken.rule for broken in rules_broken(before)}
    return said([broken for broken in rules_broken(after) if broken.rule not in kept])


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


def _same_style_or_state(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    talking = [scene for scene in scenes if not scene.broll]
    for previous, scene in zip(talking, talking[1:], strict=False):
        same = [
            what
            for what, here, there in (
                ("talking style", scene.style, previous.style),
                ("product state", scene.state, previous.state),
            )
            if here and here == there
        ]
        if same:
            yield Broken(
                DIFFERENT_STYLE_AND_STATE,
                f"Scenes {previous.number} and {scene.number} are talking scenes with the same "
                f"{' and the same '.join(same)}.",
            )


def _box_with_no_packaging(scenes: Sequence[PlanScene]) -> Iterable[Broken]:
    for scene in scenes:
        if not scene.broll and scene.state in BOX_STATES and scene.packaging_photo is None:
            yield Broken(
                BOX_NEEDS_PACKAGING,
                f'Scene {scene.number} is "{scene.state}" with no packaging photo.',
            )
