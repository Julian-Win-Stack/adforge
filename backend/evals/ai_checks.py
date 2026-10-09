"""The AI-read checks of the rule evals (decisions/rule-evals-plan.md): one yes/no judge per
rule, each a cheap text model reading what the app wrote for one B-roll scene, or for the
whole plan. A judge counts only once it agrees with Julian's grades on cases it wasn't tuned
on (decisions/2026-10-09-rule-evals-best-practice-check.md): the graded cases in
rule_cases.json are split in two, "tune" to write the judge's question against and "held
out" to measure it, and each rule is reported with how many fails it caught and how many
passes it let through, out of how many labels."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gateway.types import Handoff, Judgement, ModelReply

CASES = Path(__file__).resolve().parent / "rule_cases.json"

# The model every judge uses: cheap, as the plan asks, and already deployed for the app.
JUDGE_MODEL = "gpt-5-mini"
# A judge asked again when Azure times out, as it does under load.
TRIES = 3

JUDGE_INSTRUCTIONS = """\
You check one rule on what an ad app wrote for a B-roll clip: a short silent video clip \
played while a voice says the scene's line. A picture model draws the clip's starting \
picture from "picture_prompt" and the photos whose jobs are in "photo_jobs", then a video \
model animates it from "video_prompt". When a picture is shown, it is the starting picture \
the app really drew, or the photo it really started the clip from. "plan" is every scene of \
the ad, when the rule is about the plan. "shop_answers" is what the shop owner answered the \
app's questions.
Decide "pass" when what the app wrote, and the picture when there is one, keep the rule, \
"fail" when they break it. Judge only this rule, and only from what you are given: nothing \
else about the clip counts. Give one sentence saying why, quoting the words that decide it.
The rule:
"""

# Each rule as the judge is told it: what keeps it and what breaks it, from Julian's grades.
RULES: dict[str, str] = {
    "A2": "A claim that needs two end states, such as a bag worn two ways, is two B-roll "
    "scenes back to back, each opening already in its state. Fail when one scene films the "
    "change from one state to the other, such as putting a strap on or taking it off.",
    "A3": "A B-roll films only the main step of using the product, the one that shows it "
    "working; the voice carries the other steps. Fail when the video prompt films several "
    "steps, such as spraying, waiting and then scrubbing.",
    "A4c": "The prompts never ask for two things that can't both be true at once, of the "
    "product or the scene. Fail when two orders contradict each other.",
    "A5": "A result shows only where the product, or the tool used with it, touches, and it "
    "happens as the touch passes, at a real-time pace. Fail when the result spreads beyond "
    "where it touched, or appears all at once, by itself, or sped up. Pass when the scene "
    "has no result.",
    "A6": "Every photo sent with a job other than the product's looks or the presenter is "
    "there only for how something looks: a texture, a colour, a print. Fail when a photo is "
    "sent for an action, a movement or a pose, such as how a drop or a pour looks.",
    "A8": "What must be right in the clip, such as where the camera is, a height, the place, "
    "or how an object looks before the action, is said plainly in the picture prompt, not "
    "only in the video prompt. Fail when the video prompt needs something the picture "
    "prompt leaves out.",
    "A9": "The product may stand still in view while a tool used with it does the work, such "
    "as a bottle beside a toilet while a brush scrubs. Fail only when nothing in the clip "
    "acts at all.",
    "A11": "The side of the product facing the camera at the start faces it at the end: the "
    "product is never turned, flipped or spun to show another side. Fail when the video "
    "prompt turns, flips or spins the product, or shows a side the picture doesn't show.",
    "A13": "The clip shows only the proof of what the line claims, the moment that proves "
    "it. Fail when it adds damage, harm or anything going wrong, such as a crack line "
    "appearing, or shows more than the proof.",
    "A14": "The camera never moves: a hand or the product does. Fail when the camera pushes "
    "in, zooms, pans, tilts, follows, circles or moves in any way, or the camera's move is "
    "the scene's only action.",
    "A15": "A claim in a line that a camera could see, something the product does, is shown "
    "in a B-roll scene, not only said by the talking person. Fail when a talking scene's "
    "line claims something visible, such as a neck that reaches 360 degrees, and no B-roll "
    "scene shows it.",
    "A17": 'When "shows" names a person or a hand, they are in the video prompt, doing what '
    '"shows" says. Fail when the video prompt leaves them out.',
    "C1": "Every B-roll sells the product: it does something that shows a result, a benefit "
    "or proof. Fail when its one action only holds, places, sets down, stands up, carries or "
    "rests the product, which means nothing to a viewer.",
    "C2": "A scene about the result shows the result itself, such as a clean toilet bowl "
    "after cleaning, so a viewer sees the product worked. Fail when a result line is shown "
    "only by the product next to something already clean, with no evidence it did it. Pass "
    "when the scene isn't about a result.",
    "C3": "Any real-life size, height, distance or count the action needs to prove its claim, "
    "such as how high a phone is dropped from, is stated in the prompts at its real value. "
    'Fail when the action needs one and the prompts leave it vague, such as "above the '
    'floor", so the clip may show it far smaller. Pass when no such number matters.',
    "C4": "When the app can't know how the result looks, such as a surface before and after "
    "cleaning, it asks the shop owner for a photo, and if they have none, the middle scenes "
    "are talking scenes, not B-roll filler. Fail when a result look is needed and the app "
    "didn't ask, or the owner had no photo and the plan still has B-roll that shows nothing.",
}


class RuleVerdict(Judgement):
    decision: Literal["pass", "fail"]


class CaseToJudge(Handoff):
    scene: dict[str, Any] | None
    plan: list[dict[str, Any]] | None
    shop_answers: str


@dataclass(frozen=True)
class Case:
    id: str
    rule: str
    label: Literal["pass", "fail"]
    source: str
    handoff: CaseToJudge
    # The picture the clip really started from, shown to a scene's judge (Julian 2026-10-09,
    # decisions/checker-context.md); None for a plan's judge, or a clip that started from none.
    start_picture: Path | None
    # Every other case of a rule's passes, and of its fails, by id: the judge's question is
    # never written against a held-out case, so its score there is a fair one.
    held_out: bool


def load_cases(path: Path = CASES) -> list[Case]:
    raw = json.loads(path.read_text())["cases"]
    held_out: set[str] = set()
    for group in {(case["rule"], case["label"]) for case in raw}:
        same = sorted(case["id"] for case in raw if (case["rule"], case["label"]) == group)
        held_out.update(same[1::2])
    return [
        Case(
            id=case["id"],
            rule=case["rule"],
            label=case["label"],
            source=case["source"],
            handoff=CaseToJudge(
                scene=case.get("scene"),
                plan=case.get("plan"),
                shop_answers=case.get("shop_answers") or "",
            ),
            start_picture=Path(case["start_picture"]) if case.get("start_picture") else None,
            held_out=case["id"] in held_out,
        )
        for case in raw
    ]


Ask = Callable[[Case], RuleVerdict]


@dataclass(frozen=True)
class Score:
    rule: str
    fails: int
    fails_caught: int
    passes: int
    passes_kept: int

    def line(self) -> str:
        return (
            f"{self.rule}: caught {self.fails_caught}/{self.fails} fails, "
            f"kept {self.passes_kept}/{self.passes} passes"
        )


def score(cases: list[Case], ask: Ask) -> tuple[list[Score], list[tuple[Case, RuleVerdict]]]:
    """Each rule's score on `cases`, and every case its judge got wrong, with what it said."""
    wrong = []
    tally: dict[str, list[int]] = {}
    for case in cases:
        verdict = ask(case)
        counts = tally.setdefault(case.rule, [0, 0, 0, 0])
        index = 0 if case.label == "fail" else 2
        counts[index] += 1
        if verdict.decision == case.label:
            counts[index + 1] += 1
        else:
            wrong.append((case, verdict))
    return [Score(rule, *counts) for rule, counts in sorted(tally.items())], wrong


def ask_model(case: Case, model: str = JUDGE_MODEL) -> ModelReply[RuleVerdict]:
    """The judge for `case`'s rule, asked of `model` for real, with what it cost in tokens.
    Needs Django set up: it goes through the gateway's model provider, as the app's calls
    do, but is recorded nowhere."""
    from adforge.retry import OutsideServiceDown
    from gateway.gateway import _provider, shrunk_image
    from gateway.types import Image, LoadedImage, ModelRequest

    images: tuple[LoadedImage, ...] = ()
    if case.start_picture is not None:
        picture = Image(label="The starting picture", key=str(case.start_picture))
        images = (shrunk_image(picture, case.start_picture.read_bytes()),)

    request = ModelRequest(
        purpose="rule_eval",
        model=model,
        instructions=JUDGE_INSTRUCTIONS + RULES[case.rule],
        handoff=case.handoff,
        output=RuleVerdict,
        images=images,
    )
    for tries in range(TRIES):
        try:
            return _provider().complete(request)
        except OutsideServiceDown:
            if tries == TRIES - 1:
                raise
    raise AssertionError("unreachable")
