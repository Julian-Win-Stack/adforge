"""Owner notes requiring an overlay or exceeding the video model's abilities."""

from collections.abc import Callable
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Scene

from .conftest import HeldSteps, chat, facts_ok, handoffs, paid_for, recorded
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
LIMITS_DOC = Path(__file__).resolve().parents[2] / "docs" / "h3-limits.md"


def documented_limits() -> list[str]:
    lines = LIMITS_DOC.read_text().splitlines()
    return [line for line in lines if line.startswith("- **Hard limit:**")]


def instructions_given(fake_model: FakeModel, purpose: str) -> list[str]:
    return [each.instructions for each in fake_model.turns_taken if each.purpose == purpose]


def test_both_agents_are_told_the_documented_limits_and_no_count_limit(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    limits = documented_limits()
    assert limits
    assert not any("count" in line or "product photo" in line for line in limits)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("give_up", {"reason": "Keep the existing clip for now."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Make it.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    for purpose in ("produce", "scene_agent"):
        given = instructions_given(fake_model, purpose)
        assert given
        assert all(line in each for each in given for line in limits)
        assert all("hand_back" in each for each in given)
        assert not any("exact count" in each for each in given)
        assert not any(old in each for each in given for old in ("cannot_film", "needs_producer"))


def test_overlay_note_edits_a_busy_scene_without_drawing_or_filming(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:  # noqa: F811
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {"scene": 3, "instruction": "Make this scene.", "quotes": ["Go on"]},
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Go on")
    before = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("edit_scene", {"scene": 3, "overlay": "Free shipping"})]),
        turn(says="Added Free shipping on screen."),
    )
    say("Scene 3: put 'Free shipping' on screen")
    assert Scene.objects.get(number=3).overlay == "Free shipping"
    assert paid_for() == before


def test_hard_limit_ends_before_paid_work_and_producer_explains_once(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    reason = "I can't make tiny print readable because the video model cannot preserve its letters."
    reply = reason + " I can put those words in the text overlay instead."
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "hand_back",
                    {"reason": reason, "alternative": "Put the words in the text overlay."},
                )
            ]
        ),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": "Make the tiny label readable.",
                                "quotes": ["Make the tiny label readable"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Make the tiny label readable")
    before = paid_for()
    fake_model.respond("produce", turn(says=reply))
    steps.run_held()
    assert [purpose for purpose in paid_for()[len(before) :] if purpose != "scene_agent"] == []
    assert reason in str(handoffs("produce")[-1])
    shown = str(chat(api, session_id))
    assert shown.count(reason) == 1
    assert "text overlay instead" in shown
    assert [text for _, text in recorded(api, session_id) if reason in text] == [
        f"Scene 2: Handed back: {reason} Alternative: Put the words in the text overlay."
    ]


def test_overlay_note_handed_back_by_scene_agent_is_completed_by_producer(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "hand_back",
                    {
                        "reason": "On-screen words use the overlay.",
                        "alternative": "Set Free shipping as this scene's overlay with edit_scene.",
                    },
                )
            ]
        ),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 3,
                                "instruction": "Put Free shipping on screen.",
                                "quotes": ["Scene 3: put 'Free shipping' on screen"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Scene 3: put 'Free shipping' on screen")
    before = paid_for()
    fake_model.respond(
        "produce",
        turn(calls=[("edit_scene", {"scene": 3, "overlay": "Free shipping"})]),
        turn(says="Added Free shipping on screen."),
    )
    steps.run_held()
    assert Scene.objects.get(number=3).overlay == "Free shipping"
    assert [purpose for purpose in paid_for()[len(before) :] if purpose != "scene_agent"] == []
    assert "Handed back: On-screen words" in str(handoffs("produce")[-1])


def test_overlay_edit_during_scene_fact_check_survives_the_agent_save(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_agent_changes import change

    def edit_while_check_waits() -> dict[str, object]:
        fake_model.respond(
            "produce",
            turn(calls=[("edit_scene", {"scene": 2, "overlay": "Free shipping"})]),
            turn(says="Added Free shipping on screen."),
        )
        say("Scene 2: put 'Free shipping' on screen")
        assert Scene.objects.get(number=2).overlay == "Free shipping"
        fake_model.respond("produce", turn(says="The scene is ready."))
        return facts_ok(2)

    fake_model.respond("fact_check", edit_while_check_waits)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                change(
                    line="This mug holds three hundred and fifty millilitres of your favourite tea."
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Keep the existing clip for now."})]),
    )
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": "Change what the scene shows and says.",
                                "quotes": ["Change scene 2"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="On it."),
    )
    say("Change scene 2")
    steps.run_held()
    assert Scene.objects.get(number=2).overlay == "Free shipping"
    assert "The scene row changed" in str(handoffs("scene_agent")[-1])


def test_hand_back_puts_the_scene_back(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_agent_changes import ACTION, change

    shown_before = Scene.objects.get(number=2).shows
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[change()]),
        turn(calls=[change()]),
        turn(calls=[("hand_back", {"reason": "Tiny print.", "alternative": "Use the overlay."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Change it.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert shown_before != ACTION
    assert Scene.objects.get(number=2).shows == shown_before
    assert "Handed back: Tiny print." in str(handoffs("produce")[-1])


def test_hand_back_is_refused_once_a_picture_is_drawn(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("hand_back", {"reason": "Tiny print.", "alternative": "Use the overlay."})]),
        turn(calls=[("give_up", {"reason": "Keep the existing clip for now."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Make it.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    refused = handoffs("scene_agent")[2]["conversation"][-1]["result"]
    assert "only before drawing or filming" in refused
    assert "Handed back:" not in str(handoffs("produce")[-1])
