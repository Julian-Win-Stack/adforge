"""The plan's critic (round 5 causes 1 and 2): once the planner plans, each B-roll scene is
checked before anything is drawn or filmed: is it how the product is really used, and does
its one action last the whole clip. A failing scene goes back to the planner once with the
critic's notes; one that fails again is said to camera. Driven through the chat, with the
producer's model, the planner and the critic faked at the gateway. Julian 2026-10-19 03:37:
"yes" to the critic's two questions."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import UnusableReply
from jobs.models import Job

from .conftest import PLAN, a_plan_with, broll, chat, handoffs, paid_for

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

POUR = "A hand pours tea from the mug into a cup on a workbench."
STACK = "A hand sets a second mug onto the first."
SIP = "The presenter sips tea from the mug."


def with_broll(*shows: str | None) -> dict[str, Any]:
    """The mug plan with its scenes between the first and the last showing `shows`, or
    said to camera for None."""
    first, middle, last = PLAN["plan"]["scenes"]
    scenes = [first, *(broll({**middle, "shows": s}) for s in shows), last]
    return a_plan_with(scenes=scenes)


def passes(**changes: Any) -> dict[str, Any]:
    return {
        "decision": "checked",
        "reason": "The page shows it poured.",
        "shown_how": "a photo shows it",
        "fills_clip": True,
        **changes,
    }


NOT_SHOWN = passes(shown_how="not shown how", reason="Nothing shows it stacked.")
OVER_AT_ONCE = passes(fills_clip=False, reason="Setting it down is over in a moment.")


def planning(fake_model: FakeModel, *plans: dict[str, Any], says: str = "Here's the plan.") -> None:
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says=says))
    fake_model.respond("plan_ad", plans[0])
    if len(plans) > 1:
        fake_model.respond("replan_ad", *plans[1:])


def shown() -> list[str]:
    return [scene.shows for scene in Job.objects.get().scenes.order_by("number")]


def test_each_broll_scene_is_checked_and_a_plan_that_passes_is_kept_as_planned(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    planning(fake_model, with_broll(POUR, None))
    fake_model.respond("check_plan_scene", passes())

    say("Plan it")

    (checked,) = handoffs("check_plan_scene")
    # Only the B-roll scene is checked, with the whole plan and how long its clip runs: its
    # 7 words take under 5 seconds, and a clip is never shorter.
    assert (checked["scene_to_check"], checked["clip_seconds"]) == (2, 5)
    assert checked["plan"]["scenes"][1]["shows"] == POUR
    assert "page_text" in checked
    assert "replan_ad" not in paid_for()
    assert shown() == ["", POUR, "", ""]


def test_a_scene_the_page_doesnt_show_how_to_do_goes_back_to_the_planner_once(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    planning(fake_model, with_broll(POUR, STACK), with_broll(POUR, SIP))
    fake_model.respond("check_plan_scene", passes(), NOT_SHOWN, passes())

    say("Plan it")

    (replanning,) = handoffs("replan_ad")
    assert replanning["earlier_plan"]["scenes"][2]["shows"] == STACK
    assert replanning["checker_notes"] == [
        "Scene 3: the page doesn't show how this is done. Nothing shows it stacked."
    ]
    # Only the scene the critic failed is checked again.
    assert [h["scene_to_check"] for h in handoffs("check_plan_scene")] == [2, 3, 3]
    assert shown() == ["", POUR, SIP, ""]


def test_a_one_moment_action_goes_back_to_the_planner_as_not_lasting_the_clip(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    planning(fake_model, with_broll(STACK), with_broll(POUR))
    fake_model.respond("check_plan_scene", OVER_AT_ONCE, passes())

    say("Plan it")

    (replanning,) = handoffs("replan_ad")
    assert replanning["checker_notes"] == [
        "Scene 2: its one action is over before the clip ends. Setting it down is over in a moment."
    ]
    assert shown() == ["", POUR, ""]


def test_a_scene_failing_again_after_the_planner_tried_again_is_said_to_camera(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    planning(fake_model, with_broll(POUR, STACK), with_broll(POUR, STACK))
    fake_model.respond("check_plan_scene", passes(), NOT_SHOWN, NOT_SHOWN)

    say("Plan it")

    assert paid_for().count("replan_ad") == 1
    assert shown() == ["", POUR, "", ""]
    said = Job.objects.get().scenes.get(number=3)
    assert (said.broll_kind, said.person_shown, said.usage) == ("", "", "")


QUESTION = (
    "Your page says the mug stacks, but nothing shows how. Could you attach a photo of it "
    "being done, or tell me in one sentence how?"
)


def test_the_planner_trying_again_may_ask_the_shop_owner_instead(
    fake_model: FakeModel, page_read: str, say: Callable[..., None], api: Any, session_id: str
) -> None:
    asks = {"decision": "ask", "reason": "Nothing shows the mug stacked.", "question": QUESTION}
    planning(fake_model, with_broll(STACK), {**asks, "plan": None}, says=QUESTION)
    fake_model.respond("check_plan_scene", NOT_SHOWN)

    say("Plan it")

    assert not Job.objects.get().scenes.exists()
    assert chat(api, session_id)[-1] == ("agent", QUESTION)


def test_a_scene_the_critic_couldnt_answer_for_is_kept_unchecked(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    planning(fake_model, with_broll(POUR))
    fake_model.respond(
        "check_plan_scene", UnusableReply("unreadable", input_tokens=10, output_tokens=10)
    )

    say("Plan it")

    assert "replan_ad" not in paid_for()
    assert shown() == ["", POUR, ""]
