"""A fix-up changes only what failed, and every step that can change a scene after planning
is sent that scene's rules by its place in the ad (Julian, Nov 8 2026, thread "Stop rule
drift after planning", parts 1 to 3). Through the chat, with outside models scripted."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings

from gateway.fake import FakeModel, turn
from jobs.models import QUALITY_FIELDS, Job
from jobs.plan_rules import LENS_FIRST_OR_LAST, MIDDLE_IS_BROLL
from jobs.planning import (
    ACTION_IN_NO_OTHER_SCENE,
    BROLL_SELLS_RULE,
    HOW_TO_METHOD,
    KEEPS_THE_HOOK,
    KEEPS_THE_LAST_SCENE,
    RESULT_SHOWN,
)

from .conftest import HeldSteps, facts_ok, handoffs, results_of
from .test_h3_planning import h3_plan, script_plan
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_producer_checks import failing
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

NEW_LINE = (
    "Okay, this mug holds three hundred fifty millilitres of tea, and honestly I love how it feels."
)
NEW_ACTION = "a hand lifts the mug from the saucer"


def scene_2_failed(wrong: str) -> dict[str, Any]:
    """The fact check failing scene 2's line, its action or both, and passing the others."""
    check = failing(2, "The page doesn't say that.", passing=(1, 3))
    check["lines"][-1]["wrong"] = wrong
    return check


def asked(fake_model: FakeModel, purpose: str) -> list[str]:
    """The instructions sent for each answer or agent turn for `purpose`, oldest first."""
    answers = [request.instructions for request in fake_model.asked if request.purpose == purpose]
    turns = [
        request.instructions for request in fake_model.turns_taken if request.purpose == purpose
    ]
    return answers or turns


def broll_rules_in(told: str) -> bool:
    return all(
        rule in told
        for rule in (
            BROLL_SELLS_RULE,
            "A B-roll scene films one action",
            ACTION_IN_NO_OTHER_SCENE[1:],
            RESULT_SHOWN,
            HOW_TO_METHOD,
        )
    )


def test_a_fix_up_for_a_failed_action_keeps_the_line_word_for_word(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    planned = plan["plan"]["scenes"][1]
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", scene_2_failed("shows"), facts_ok(2))
    # The rewrite changes its line as well, as round 12's did.
    fake_model.respond("rewrite_line", {**planned, "line": NEW_LINE, "shows": NEW_ACTION})

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.line, scene.shows) == (planned["line"], NEW_ACTION)
    (told,) = asked(fake_model, "rewrite_line")
    assert "Only its action failed: give back its line word for word" in told
    # Scene 2 is a B-roll scene in the middle: its rules, not the hook's or the last scene's.
    assert broll_rules_in(told)
    assert KEEPS_THE_HOOK not in told
    assert KEEPS_THE_LAST_SCENE not in told


def test_a_fix_up_for_a_failed_line_keeps_the_action_and_its_details(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    planned = plan["plan"]["scenes"][1]
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", scene_2_failed("line"), facts_ok(2))
    # The rewrite changes the action and its details as well, as round 12's did.
    fake_model.respond(
        "rewrite_line",
        {
            **planned,
            "line": NEW_LINE,
            "shows": NEW_ACTION,
            "broll_kind": "showcase",
            "usage": "lifted from the saucer",
            "result": None,
        },
    )

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=2)
    assert scene.line == NEW_LINE
    assert scene.shows == planned["shows"]
    # The fact check saw scene 2 again with the planned action and details.
    rechecked = handoffs("fact_check")[1]["lines"][0]
    assert (rechecked["line"], rechecked["shows"]) == (NEW_LINE, planned["shows"])
    assert rechecked["usage"] == (planned.get("usage") or None)
    assert scene.broll_kind == planned["broll_kind"]
    for field in QUALITY_FIELDS:
        if field in planned:
            assert getattr(scene, field) == planned[field], field
    (told,) = asked(fake_model, "rewrite_line")
    assert "Only its line failed: change only the words of the line the problem names" in told


def test_a_fix_up_of_the_first_scene_is_sent_the_hook_rule(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    script_plan(fake_model, product_page_url, plan, checks=True)
    check = failing(1, "The page doesn't say that.", passing=(2, 3))
    fake_model.respond("fact_check", check, facts_ok(1))
    fake_model.respond("rewrite_line", {**plan["plan"]["scenes"][0], "line": NEW_LINE})

    say(product_page_url)

    (told,) = asked(fake_model, "rewrite_line")
    assert KEEPS_THE_HOOK in told
    assert KEEPS_THE_LAST_SCENE not in told
    assert BROLL_SELLS_RULE not in told


def test_lengthening_a_broll_line_is_sent_its_rules_and_adds_no_new_product_fact(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = "Holds 350 ml."
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), facts_ok(2), facts_ok(2))
    fake_model.respond(
        "lengthen_line", {"line": "It holds 350 ml."}, {"line": "This mug holds 350 ml."}
    )

    say(product_page_url)

    told = asked(fake_model, "lengthen_line")[0]
    assert broll_rules_in(told)
    assert KEEPS_THE_HOOK not in told
    flat = " ".join(told.split())
    assert "Never add a new fact about the product, even one the page or the shop owner state" in (
        flat
    )
    assert "add only what the page or the shop owner's own words state" not in flat


def test_shortening_a_line_is_sent_its_scenes_rules(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    plan = h3_plan()
    plan["plan"]["scenes"][1]["line"] = " ".join(["tea"] * 44)
    script_plan(fake_model, product_page_url, plan, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3), *[facts_ok(2)] * 3)
    fake_model.respond("shorten_line", *[{"line": " ".join(["tea"] * n)} for n in (42, 41, 40)])

    say(product_page_url)

    told = asked(fake_model, "shorten_line")[0]
    assert broll_rules_in(told)
    assert KEEPS_THE_HOOK not in told
    assert KEEPS_THE_LAST_SCENE not in told


def test_shortening_the_script_is_sent_every_scenes_rules(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    # Scene 2 of 4 is a B-roll scene, scene 3 a middle talking scene.
    script_plan(fake_model, product_page_url, h3_plan(4), target=15, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))
    fake_model.respond(
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet the Stoneware Mug for a good cup of tea every day."},
                {"scene": 3, "line": "It is hand thrown and holds 350 ml of tea every day."},
                {
                    "scene": 4,
                    "line": "Buy the Stoneware Mug for $24.00 from Kiln & Co today online.",
                },
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3))

    say(product_page_url)

    told = asked(fake_model, "shorten_script")[0]
    assert KEEPS_THE_HOOK in told
    assert KEEPS_THE_LAST_SCENE in told
    assert broll_rules_in(told)
    assert "Scene 2, a B-roll scene:" in told
    assert f"Scene 3, a middle talking scene: {MIDDLE_IS_BROLL}" in told


def test_the_producer_is_sent_every_places_rules(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], settings: Settings
) -> None:
    script_plan(fake_model, product_page_url, h3_plan())

    say(product_page_url)

    told = asked(fake_model, "produce")[0]
    assert KEEPS_THE_HOOK in told
    assert KEEPS_THE_LAST_SCENE in told
    assert broll_rules_in(told)


def test_the_scene_agent_is_sent_its_scenes_rules(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent", turn(calls=[("give_up", {"reason": "Need another product view."})])
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Make it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    # Scene 2 is a B-roll scene in the middle. The proven B-roll rule and lens first or last
    # are in the scene agent's own words already, so they aren't said twice.
    told = asked(fake_model, "scene_agent")[0]
    assert "When you change your scene: Keep each scene's rules" in told
    assert "Scene 2, a B-roll scene: A B-roll scene films one action" in told
    for rule in (ACTION_IN_NO_OTHER_SCENE[1:], RESULT_SHOWN, HOW_TO_METHOD):
        assert rule in told
    assert told.count(BROLL_SELLS_RULE) == 1
    assert LENS_FIRST_OR_LAST not in told
    assert KEEPS_THE_HOOK not in told
    assert KEEPS_THE_LAST_SCENE not in told


def test_the_scene_agent_is_sent_its_scenes_new_rules_after_a_kind_change(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import Scene

    # Scene 2 talks while she uses the mug, then its agent makes it B-roll.
    Scene.objects.filter(number=2).update(
        kind=Scene.Kind.TALKING, talking_style="talking while doing it", product_state="in use"
    )
    scene = Scene.objects.get(number=2)
    to_broll = {
        **{
            name: getattr(scene, name)
            for name in ("line", "shows", "usage", "result", "broll_kind", "person_shown")
        },
        "kind": "broll",
        "kind_why": "The inside of the mug needs a close view.",
    }
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", to_broll)]),
        turn(calls=[("give_up", {"reason": "Need another product view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Film it close.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert handoffs("scene_agent")[1]["conversation"][-1]["result"].startswith(
        "The scene row changed: kind."
    )
    first, then = asked(fake_model, "scene_agent")[:2]
    assert f"Scene 2, a middle talking scene: {MIDDLE_IS_BROLL}" in first
    assert "Scene 2, a B-roll scene:" not in first
    assert "Scene 2, a B-roll scene:" in then
    assert MIDDLE_IS_BROLL not in then
