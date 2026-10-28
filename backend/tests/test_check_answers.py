"""What the planning checks' models may answer: a shortened script, a fact check's verdicts
and a rewritten scene, read against the job they are for. Plain checks of the answers;
tests/test_producer_checks.py keeps one case of each through the chat."""

from typing import Any

import pydantic
import pytest

from jobs.checks import LineToCheck, fact_check_for, rewritten_scene_for, shortened_script_for

from .conftest import FACTS_OK, facts_ok
from .test_producer_checks import DOES_A_JOB, POUR, SAID_OVER, shows_wrong

# The mug's script with scene 2 showing the pour.
SCRIPT = [
    LineToCheck(scene=1, line="Meet the Stoneware Mug from Kiln & Co."),
    LineToCheck(scene=2, line=SAID_OVER, shows=POUR),
    LineToCheck(scene=3, line="Yours for $24.00."),
]


def shortened(*kept: int) -> dict[str, Any]:
    return {"lines": [{"scene": scene, "line": "Short."} for scene in kept]}


def test_a_shortened_script_that_keeps_the_rules_is_read() -> None:
    assert shortened_script_for(SCRIPT).model_validate(shortened(1, 3)).lines


@pytest.mark.parametrize(
    ("kept", "broken_rule"),
    [
        pytest.param(
            [2, 3],
            "The first scene is the person talking to camera",
            id="opening on a scene that shows the product",
        ),
        pytest.param([1, 3, 2], "Give the lines in the order they play", id="out of order"),
        pytest.param([1, 1], "Give the lines in the order they play", id="a scene twice"),
        pytest.param([1, 4], "There's no scene 4: the script has 3.", id="a scene not sent"),
    ],
)
def test_a_shortened_script_that_breaks_the_rules_cant_be_read(
    kept: list[int], broken_rule: str
) -> None:
    with pytest.raises(pydantic.ValidationError, match=broken_rule):
        shortened_script_for(SCRIPT).model_validate(shortened(*kept))


def test_a_fact_check_that_keeps_the_rules_is_read() -> None:
    answer = shows_wrong("The page doesn't mention tea.", passing=(1, 3))
    assert fact_check_for([1, 2, 3], showing=[2]).model_validate(answer).lines


@pytest.mark.parametrize(
    ("verdict", "told"),
    [
        pytest.param(
            shows_wrong("The page doesn't mention tea.", passing=(1, 3), wrong="both"),
            'Scene 2 has no "shows": only its line can be wrong.',
            id="a talking scene's line and shows found wrong",
        ),
        pytest.param(
            {
                **FACTS_OK,
                "lines": [
                    *facts_ok(1, 3)["lines"],
                    {
                        "scene": 2,
                        "verdict": "wrong",
                        "wrong": None,
                        "problem": "Unsupported.",
                        "page_says": "Nothing.",
                    },
                ],
            },
            'A "wrong" line needs what is wrong, its problem and what the page says.',
            id="wrong without saying what",
        ),
    ],
)
def test_a_fact_check_that_doesnt_say_which_part_is_wrong_cant_be_read(
    verdict: dict[str, Any], told: str
) -> None:
    # The mug's three scenes, all said to camera.
    with pytest.raises(pydantic.ValidationError, match=told):
        fact_check_for([1, 2, 3], showing=[]).model_validate(verdict)


def test_a_rewritten_scene_that_keeps_the_rules_is_read() -> None:
    rewrite = rewritten_scene_for(2, shows_something=True, photo_count=2)
    assert rewrite.model_validate(DOES_A_JOB).shows == POUR


@pytest.mark.parametrize(
    ("rewrite", "told"),
    [
        pytest.param(
            {**DOES_A_JOB, "result": None},
            'A "does a job" scene needs its result: what you can see at its end.',
            id="a job without its result",
        ),
        pytest.param(
            {
                **DOES_A_JOB,
                "person_shown": "has face",
                "needs": [{"what": f"detail {n}", "photos": [2]} for n in range(1, 5)],
            },
            "Scene 2 would send 6 pictures",
            id="too many pictures",
        ),
    ],
)
def test_a_rewritten_scene_that_breaks_the_broll_rules_cant_be_read(
    rewrite: dict[str, Any], told: str
) -> None:
    with pytest.raises(pydantic.ValidationError, match=told):
        rewritten_scene_for(2, shows_something=True, photo_count=2).model_validate(rewrite)
