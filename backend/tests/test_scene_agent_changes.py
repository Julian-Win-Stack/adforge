"""Scene row changes through the chat, with outside models scripted."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, facts_ok, handoffs
from .test_producer_broll import LINE_2, calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
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


def test_a_new_line_is_checked_voiced_and_heard_then_giveup_restores_the_plan(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("produce", turn(says="I will pass on that note."))
    say("Keep the handle visible.")
    line = "This mug holds three hundred and fifty millilitres."
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
    assert "The new line was voiced and heard" in result
    assert line in fake_model.spoken
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
    assert line not in fake_model.spoken


def test_a_line_over_fifteen_seconds_is_refused_before_filming(
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
    assert "over the 15 s a clip lasts" in result


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


@pytest.mark.parametrize("failure_at", ["voice", "speak", "transcript", "too_long"])
def test_new_line_failures_are_reported_without_hiding_the_original_cause(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    sentry_events: list[dict[str, Any]],
    failure_at: str,
) -> None:
    from jobs.models import Job, ProducedItem

    from .conftest import results_of

    reason = "Voice service failed during the new line."
    if failure_at == "voice":
        ProducedItem.objects.filter(kind="voice").update(voice_deleted=True)
        fake_model.respond("design_voice", RuntimeError(reason))
    elif failure_at == "speak":
        fake_model.respond("speak_line", RuntimeError(reason))
    elif failure_at == "transcript":
        fake_model.respond("transcribe_line", RuntimeError(reason))
    else:
        fake_model.words_per_second = 0.25
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line="This mug holds a warm drink.")]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Change its line.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    answer = results_of("change_scene")[-1]
    warnings = [w for w in Job.objects.get().warnings if w["kind"] == "step_not_made"]
    events = [e for e in sentry_events if e.get("fingerprint") == ["notice", "step_not_made"]]
    if failure_at == "too_long":
        assert "over the 15 s" in answer
        assert warnings == events == []
    else:
        assert "Refused:" in answer
        assert len(warnings) == len(events) == 1
        assert reason in warnings[0]["text"]
        assert reason in events[0]["extra"]["text"]
