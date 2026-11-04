"""Scene row changes through the chat, with outside models scripted."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, facts_ok, handoffs, paid_for
from .test_producer_broll import BROLL_PLAN, calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
LINE_2 = BROLL_PLAN["plan"]["scenes"][1]["line"]
ACTION = "Tea is stirred in the mug with a spoon."


def change(**fields: Any) -> tuple[str, dict[str, Any]]:
    return "change_scene", {
        "line": LINE_2,
        "shows": ACTION,
        "usage": "",
        "result": "",
        "broll_kind": "showcase",
        "person_shown": "no face",
        **fields,
    }


def test_changed_action_shows_the_kept_line_once_then_changes_without_filming(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[change()]),
        turn(calls=[("give_up", {"reason": "Need another product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the action.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    requests = handoffs("scene_agent")
    assert f'The scene\'s line says: "{LINE_2}"' in requests[1]["conversation"][-1]["result"]
    assert "Nothing was changed" in requests[1]["conversation"][-1]["result"]
    assert "The scene row changed" in str(requests[2])
    assert handoffs("fact_check")[-1]["lines"][0]["shows"] == ACTION


def test_a_new_line_is_checked_without_voice_spending_then_giveup_restores_the_plan(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="I will pass on that note."))
    say("Keep the handle visible.")
    line = "This mug holds three hundred and fifty millilitres of your favourite tea."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line)]),
        turn(calls=[("give_up", {"reason": "Need a different product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Change the scene.",
                "quotes": ["Keep the handle visible."],
            },
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "The scene row changed" in result
    assert "Nothing was drawn or filmed" in result
    assert not {"design_voice", "measure_voice", "speak_line", "transcribe_line"} & set(paid_for())
    assert handoffs("fact_check")[-1]["lines"][0]["line"] == line
    assert handoffs("fact_check")[-1]["conversation"] == [
        {"by": "user", "text": "Keep the handle visible."}
    ]
    # The producer sees the restored original row on its next turn.
    snapshot = handoffs("produce")[-1]["snapshot"]
    assert LINE_2 in snapshot
    assert line not in snapshot
    assert ACTION not in snapshot


def test_a_fact_check_failure_refuses_the_change_with_the_reason(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    line = "It keeps tea hot for hours."
    fake_model.respond(
        "fact_check",
        {
            "decision": "checked",
            "reason": "Claim not on page.",
            "question": None,
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "wrong": "line",
                    "problem": "Heat retention is not stated.",
                    "page_says": "It holds 350 ml.",
                }
            ],
        },
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line)]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the scene.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "Refused: the fact check failed it: Heat retention is not stated." in result
    assert "make_broll_clip" not in paid_for()


def test_a_line_over_fourteen_seconds_is_refused_before_filming(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line="This mug holds tea. " * 10)]),
        turn(calls=[("give_up", {"reason": "Need a shorter line."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the scene.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "more than 14 seconds" in result


def test_an_action_another_scene_films_is_refused_without_another_check(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        (
            "edit_scene",
            {
                "scene": 3,
                "shows": ACTION,
                "broll_kind": "showcase",
                "person_shown": "no face",
                "usage": "",
                "result": "",
            },
        ),
    )
    checks = len(handoffs("fact_check"))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[("give_up", {"reason": "That action belongs to another scene."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Change the action.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    result = handoffs("scene_agent")[1]["conversation"][-1]["result"]
    assert "Refused: scene 3 already films this action." in result
    assert len(handoffs("fact_check")) == checks
