"""Fixing a scene the way the app does once the ad has a clip (fix redesign): a plan the
decider would have made, checked and recorded by code, then the shop owner's yes, which has
code run it. For tests about the worker's part of a fix (its checks, judge, photos and
versions) rather than about the decider or the chat, which tests/test_fix_wiring.py covers.
Nothing here takes a producer turn."""

from typing import Any

from agents import run_plan
from chat import messages
from chat.models import Message, Session
from jobs import fix_plans
from jobs.models import FixPlan, FixPlanScene, Job, Scene


def owner_says(text: str) -> Message:
    """The shop owner's message, as the chat keeps it, with nothing woken by it."""
    return messages.add(Session.objects.get(), role=Message.Role.USER, text=text)


def plan_row(number: int, action: str, **fields: Any) -> FixPlanScene:
    """One scene's row of a plan, unsaved, as the decider hands it to code."""
    return FixPlanScene(scene=Scene.objects.get(number=number), action=action, **fields)


def planned(*rows: FixPlanScene, note: str = "Some notes on the ad.") -> FixPlan:
    """The decider's plan for the shop owner's `note`, checked and recorded by code, shown
    to them and waiting for their yes."""
    said = owner_says(note)
    plan = run_plan.check_and_propose(
        Job.objects.get(), [{"seq": said.seq, "text": said.text}], list(rows)
    )
    fix_plans.show(plan)
    return plan


def say_yes(plan: FixPlan, text: str = "yes") -> FixPlan:
    """The shop owner's yes to `plan`, settled as the chat settles it, then run by code."""
    fix_plans.settle(plan, FixPlan.State.CONFIRMED, by=owner_says(text))
    run_plan.run_after_yes(plan)
    return plan


def fixed(number: int, action: str, note: str = "Some notes on the ad.", **fields: Any) -> FixPlan:
    """Scene `number` fixed by a plan of one row, said yes to: its first steps are queued."""
    return say_yes(planned(plan_row(number, action, **fields), note=note))


def owner_photo(number: int, thing: str, file: str, **answers: bool) -> dict[str, Any]:
    """An owner photo on a plan row, routed as the decider answers: in the starting picture
    now unless said otherwise, and not more during the action."""
    return {
        "photo": number,
        "file": file,
        "thing": thing,
        "in_picture": answers.get("in_picture", True),
        "during_action": answers.get("during_action", False),
    }
