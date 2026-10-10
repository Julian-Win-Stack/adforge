"""Prompt rules proven by Julian's grades, frozen (decisions/rule-evals-plan.md, table A).
Each sentence below is pinned word for word in the prompt it lives in, so changing one fails
here: a proven rule changes only when Julian says so, and then its line here changes with it.
Each case names the grade that proved the rule. Not frozen on purpose: the camera style of
the video prompt's opener, "One continuous shot" (#117) and the "while" sentence (round 2 N5
not graded yet)."""

import pytest

from jobs.planning import PLAN_INSTRUCTIONS
from jobs.scenes import (
    BROLL_KIND_RULES,
    MAIN_PHOTO_JOB,
    broll_prompt_instructions,
    broll_video_prompt,
)

PLANNER = PLAN_INSTRUCTIONS
# Every kind gets the same shared rules; "does a job" is one of them.
BROLL_PROMPT = broll_prompt_instructions("does a job")

FROZEN = [
    pytest.param(
        PLANNER,
        "Putting the product on or taking it off, attaching, fitting or adjusting it is a "
        "movement of its own: a scene films it or what comes after it, never both.",
        id="A2 two end states are two clips (#5 bag PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "When a claim needs two end states, give each its own B-roll scene, back to back, "
        "each with its own line and its one action, and mark the second as the second state.",
        id="A2 two end states are two scenes, the second marked (#5 bag PERFECT)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Never film the fiddly change between two states, such as clipping, unclipping or "
        "folding: open with it done.",
        id="A2 the change between two states is never filmed (#5 bag PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "When the page or the line lists steps for using the product, film only the main step, "
        "the one that shows it working, such as the cloth wiping a stain away rather than "
        "spraying, waiting and rinsing, and write that B-roll line about that step.",
        id="A3 main step only (#8 toilet PERFECT)",
    ),
    pytest.param(
        BROLL_PROMPT,
        'When "shows" lists more than one, film the one where the product, or the tool used '
        "with it, does the work, not a step before it, such as putting the product on, or "
        "after it, such as rinsing. The voice carries the rest.",
        id="A3 the voice carries the rest (#8 toilet PERFECT)",
    ),
    pytest.param(
        BROLL_PROMPT,
        'Never write seconds or timings, or words that slow it down, such as "slowly" or "gently".',
        id="A4 no timings (locked list)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "never move the camera or say where or how close it ends.",
        id="A4 no forced end zoom (locked list)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Never ask for two things that can't both be true at once, of the product or the scene, "
        'such as "upright" and "nozzle pointing down", or "squeeze" and "no gel". Check every '
        "order against the product photos and against your other orders.",
        id="A4 no contradictions (locked list)",
    ),
    pytest.param(
        BROLL_KIND_RULES["does a job"],
        "The result shows only where the product, or the tool used with it, touches, and "
        "nothing else changes. It happens as the touch passes, at a real-time pace: never all "
        "at once, sped up or time-compressed.",
        id="A5 result only where touched, nothing instant (toilet, Oct 8)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "Anything that must be right goes in the starting picture, said plainly: say where the "
        "camera is, its height and angle; each object the action happens to, other than the "
        "product, with every part named as a real, ordinary one, like one you'd buy in any "
        "shop; the problem the product fixes, as it looks before; exact counts, and left or "
        "right; and any hand already in place for the action, holding what it uses.",
        id="A8 what must be right is in the start picture (#12 V1 FAIL, V2 GOOD)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "The product may stand in view, label to the camera, when holding it would bend its "
        "shape, while the tool used with it does the work.",
        id="A9 product may stand while a tool works (#8 5s scrub PERFECT)",
    ),
    pytest.param(
        PLANNER,
        "A hand never turns, flips or spins the product, and it never spins by itself, to show "
        "it or another side of it:",
        id="A11 never turn or flip, planner (#12 V3 and round 1 #12 FAIL)",
    ),
    pytest.param(
        BROLL_PROMPT,
        "The side of the product facing the camera in the starting picture faces it at the "
        "end: never turn, flip or spin the product, and after a drop it lands that side up.",
        id="A11 never turn or flip, video prompt (#12 V3 and round 1 #12 FAIL)",
    ),
    pytest.param(
        PLANNER,
        "The one verb is what a hand or the product does, never a camera move, such as the "
        "camera pushing in.",
        id="A14 a hand or the product moves, never the camera (round 1 #8 s2 FAIL)",
    ),
    pytest.param(
        PLANNER,
        "A claim about something the product, or a part of it, does that a camera could see "
        "is a B-roll scene that shows it.",
        id="A15 a visible claim is shown in B-roll (round 1 #8 s3 FAIL)",
    ),
    pytest.param(
        PLANNER,
        "The first scene is always the person talking to camera.",
        id="A16 first scene talking (round 1, all 5 ads)",
    ),
    pytest.param(
        BROLL_PROMPT,
        'When "shows" names a person or a hand, they are in the clip, doing what it says.',
        id="A17 a person named in shows is in the clip (#5 s3 FAIL)",
    ),
]


@pytest.mark.parametrize(("prompt", "sentence"), FROZEN)
def test_a_proven_rule_is_still_in_its_prompt_word_for_word(prompt: str, sentence: str) -> None:
    assert sentence in prompt


def test_every_kind_of_b_roll_prompt_gets_the_frozen_shared_rules() -> None:
    shared = "never move the camera or say where or how close it ends."
    assert all(shared in broll_prompt_instructions(kind) for kind in BROLL_KIND_RULES)


def test_the_video_prompt_asks_for_real_time_speed() -> None:
    # A1: in the old opener that passed #12 and #8 and in today's. Only "real-time speed" is
    # frozen: the camera style that follows is open until a test compares.
    assert "at real-time speed." in broll_video_prompt("A hand pours the sauce.", 5)


def test_the_main_photo_is_sent_for_its_looks_only() -> None:
    # A6: Oct 7 #12 drop leak: a photo's pose is copied whatever its job says, so the main
    # photo's job is only how the product looks.
    assert MAIN_PHOTO_JOB == "the product, only how it looks"
