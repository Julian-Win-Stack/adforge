"""The fixing agent's scene text (round 7 items 2, 8 and 12): it may change how the product is
used in the scene, as it changes the line, what it shows and its result; the fact check sees
only text that hasn't passed yet, and never refuses a move for text that already passed and
is unchanged; and end_result_only's answer quotes the scene's line for the agent to decide
whether it still fits. The agent, the fact check, the voice, the judge, the pictures and the
clips are faked at the gateway."""

from typing import Any

import pytest

from agents import fixing_agent
from gateway.fake import FakeModel, turn
from jobs.models import AgentFix, Scene, SceneStep

from .conftest import facts_ok, handoffs
from .test_fixing_agent import (
    STIRS,
    failed_clip,  # noqa: F401 (a fixture)
    film_again,
    fixed,
    told,
)
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import LINE_2, checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

STIRRED = "A spoon stirs the tea in the mug, round and round."
NEW_LINE = "Stir it, sip it: stoneware made to last."


def wrong(blamed: str) -> dict[str, Any]:
    """The fact check failing scene 2's `blamed` part ("line", "shows" or "both")."""
    return {
        "decision": "checked",
        "reason": "One claim isn't on the page.",
        "question": None,
        "lines": [
            {
                "scene": 2,
                "verdict": "wrong",
                "wrong": blamed,
                "problem": "The page never says it.",
                "page_says": "A stoneware mug.",
            }
        ],
    }


def checks_made() -> int:
    return len(handoffs("fact_check"))


def tries() -> list[Any]:
    return list(AgentFix.objects.get().tries.order_by("number"))


# --- usage (item 2) -------------------------------------------------------------------------


def test_film_again_changes_the_scenes_usage_and_fact_checks_it(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[film_again(shows=STIRS, usage=STIRRED)]))
    before = checks_made()

    fixed(failed_clip)

    assert Scene.objects.get(number=2).usage == STIRRED
    [check] = handoffs("fact_check")[before:]
    assert check["lines"][0]["usage"] == STIRRED
    [answer] = told()
    assert "Changed: shows, usage." in answer
    # What was put back on a give-up includes it, as the try's scene row does.
    assert tries()[0].scene_row["usage"] == STIRRED


def test_a_usage_left_out_keeps_the_scenes(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    usage = "Hot tea is poured into the mug from a teapot."
    Scene.objects.filter(number=2).update(usage=usage)
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    fixed(failed_clip)

    assert Scene.objects.get(number=2).usage == usage
    assert "Changed: only the prompts." in told()[0]


def test_the_agent_is_told_it_may_change_how_the_product_is_used() -> None:
    said = " ".join(fixing_agent.INSTRUCTIONS.split())
    assert "how the product is used in it" in said


# --- text that already passed (item 8) ------------------------------------------------------


def test_a_fact_check_blaming_the_unchanged_line_that_passed_never_refuses_the_move(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    # The plan's check passed scene 2's line; the agent changes only what it shows.
    fake_model.respond("fact_check", wrong("line"))
    fake_model.respond("fix_scene", turn(calls=[film_again(shows=STIRS)]))

    outcome = fixed(failed_clip)

    [answer] = told()
    assert not answer.startswith("Refused")
    assert [each.number for each in outcome.tries] == [1]
    assert Scene.objects.get(number=2).fact_checked


def test_a_fact_check_blaming_the_new_text_still_refuses_it(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", wrong("shows"), facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(shows=STIRS)]),
        turn(calls=[film_again(shows=STIRRED)]),
    )

    fixed(failed_clip)

    refused, filmed = told()
    assert refused.startswith("Refused: the fact check failed it: The page never says it.")
    assert not filmed.startswith("Refused")


def test_both_blamed_refuses_when_either_part_is_new(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", wrong("both"), facts_ok(2))
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(shows=STIRS)]),
        turn(calls=[film_again(shows=STIRRED)]),
    )

    fixed(failed_clip)

    assert told()[0].startswith("Refused: the fact check failed it")


def test_text_put_back_as_it_passed_before_is_not_checked_again(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene",
        # A new line, checked once; then the plan's line back, which passed before.
        turn(calls=[film_again(line=NEW_LINE)]),
        turn(calls=[film_again(line=LINE_2)]),
    )
    fake_model.respond("check_clip_motion", failing(FLOATS))
    before = checks_made()

    outcome = fixed(failed_clip)

    checked_lines = [check["lines"][0]["line"] for check in handoffs("fact_check")[before:]]
    assert checked_lines == [NEW_LINE]
    assert [each.number for each in outcome.tries] == [1, 2]
    assert Scene.objects.get(number=2).line == LINE_2
    assert Scene.objects.get(number=2).fact_checked


# --- end_result_only (item 12) --------------------------------------------------------------

FULL = "The mug stands full of hot tea, steam rising from it."


def end_result_only(**changes: Any) -> tuple[str, dict[str, Any]]:
    return "end_result_only", {**film_again()[1], "shows": FULL, "video_prompt": FULL, **changes}


def test_end_result_only_keeping_the_line_first_quotes_it_for_the_agent_to_decide(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "fix_scene", turn(calls=[end_result_only()]), turn(calls=[end_result_only()])
    )

    outcome = fixed(failed_clip)

    asked, filmed = told()
    assert asked == f'The scene\'s line says: "{LINE_2}" {fixing_agent.LINE_STILL_FITS}'
    # Nothing is forced: the same call again films it, the line as it was.
    assert [(each.number, each.tool) for each in outcome.tries] == [(1, "end_result_only")]
    assert filmed.startswith("Try 1.")
    assert Scene.objects.get(number=2).line == LINE_2


def test_end_result_only_with_a_new_line_films_at_once(
    fake_model: FakeModel,
    failed_clip: SceneStep,  # noqa: F811
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("fix_scene", turn(calls=[end_result_only(line=NEW_LINE)]))

    outcome = fixed(failed_clip)

    [filmed] = told()
    assert filmed.startswith("Try 1.")
    assert [each.tool for each in outcome.tries] == ["end_result_only"]
    assert Scene.objects.get(number=2).line == NEW_LINE
