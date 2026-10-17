"""Playing one case on a finished ad: put the ad in the case's state (an earlier version of a
scene, from a picture a past run drew), send the shop owner's messages one at a time, and have
the decider read each one as the chat will (agents.decider.read on this round's messages).
What it did is read back from the app's own records: its tool calls, the model calls they
took, and the plan it recorded."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.utils import timezone

from adforge import file_store
from agents import decider, run_plan
from agents.models import ToolCall
from chat import messages
from chat.models import Attachment, Message
from jobs import fix_plans, work
from jobs.models import FixCheck, FixPlan, FixPlanScene, Job, ProducedItem, SceneStep


@dataclass
class Turn:
    """What the decider answered to one message from the shop owner."""

    said: str
    photos: list[str]
    seq: int
    outcome: str  # planned, not_a_fix, yes, no, go_ahead, no_decision or error
    why: str = ""  # why it stopped without a decision, or the error it stopped on
    plan: dict[str, Any] | None = None  # the plan notice, for a planned turn
    decide: dict[str, Any] | None = None  # the decide call the plan was recorded from
    gone_ahead: list[int] = field(default_factory=list)  # the scenes a go ahead carried on


@dataclass
class Played:
    job: Job
    turns: list[Turn] = field(default_factory=list)


def play(job: Job, case: dict[str, Any]) -> Played:
    """Play `case` on `job`, a finished ad: its setup, then each of its turns. A turn that
    stops on an error ends the case there, so what it did and paid for is still kept."""
    for version in case.get("setup", {}).get("versions", []):
        add_version(job, version["scene"], Path(version["picture"]), version["change"])
    for held in case.get("setup", {}).get("held_checks", []):
        hold_on_a_check(job, held["scene"], Path(held["picture"]), held["change"], held["reason"])
    played = Played(job)
    for turn in case["turns"]:
        played.turns.append(_send(job, turn["say"], turn.get("photos", [])))
        if played.turns[-1].outcome == "error":
            break
    return played


def add_version(job: Job, number: int, picture: Path, change: str) -> ProducedItem:
    """A new version of scene `number`: the picture a past run drew for `change`, made as a
    fix of it (its clip not made yet)."""
    scene = job.scenes.get(number=number)
    assert job.session is not None
    call = ToolCall.objects.create(
        session=job.session,
        job=job,
        agent="producer",
        tool="make_starting_picture",
        call_id=f"setup-{scene.number}-{picture.stem}",
        arguments={"scene": scene.number, "note": change},
    )
    step = SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.STARTING_PICTURE,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        line=scene.line,
        shows=scene.shows,
        note=change,
    )
    made = scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE).count()
    return ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=made + 1,
        file=file_store.save(picture.name, picture.read_bytes()),
        change=change,
    )


def hold_on_a_check(job: Job, number: int, picture: Path, change: str, reason: str) -> None:
    """Scene `number` fixed by a plan the shop owner said yes to, its new starting picture the
    one a past run drew for `change`, and its fix check failed for `reason`: the chat shows
    the notice, and the scene waits for their go ahead or what to change (flow step 9)."""
    assert job.session is not None
    scene = job.scenes.get(number=number)
    old = scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE).order_by("id").last()
    note = messages.add(job.session, role=Message.Role.USER, text=f"Scene {number}: {change}")
    plan = run_plan.check_and_propose(
        job,
        [{"seq": note.seq, "text": note.text}],
        [FixPlanScene(scene=scene, action=FixPlanScene.Action.NEW_PICTURE, change=change)],
    )
    fix_plans.show(plan)
    fix_plans.settle(
        plan,
        FixPlan.State.CONFIRMED,
        by=messages.add(job.session, role=Message.Role.USER, text="yes"),
    )
    row = plan.scenes.get()
    row.started_at = timezone.now()
    row.fix = run_plan._confirmed_fix(plan, row)
    row.save(update_fields=["started_at", "fix"])
    made = add_version(job, number, picture, change)
    assert made.step is not None
    made.step.plan_scene, made.step.fix = row, row.fix
    made.step.save(update_fields=["plan_scene", "fix"])
    check = FixCheck.objects.create(
        fix=row.fix,
        step=made.step,
        old=old,
        new=made,
        verdict=FixCheck.Verdict.FAILED,
        reason=reason,
    )
    work.say_the_fix_check_failed(check)


def _send(job: Job, text: str, photos: list[str]) -> Turn:
    assert job.session is not None
    message = messages.add(
        job.session,
        role=Message.Role.USER,
        text=text,
        carrying=[
            messages.AttachedFile(
                kind=Attachment.Kind.PICTURE,
                file=file_store.save(Path(photo).name, Path(photo).read_bytes()),
            )
            for photo in photos
        ],
    )
    turn = Turn(said=text, photos=photos, seq=message.seq, outcome="")
    try:
        outcome = decider.read(job, decider.this_round(job))
    except decider.NoDecision as stopped:
        turn.outcome, turn.why = "no_decision", str(stopped)
        return turn
    except Exception as error:  # a model refused or failed: the case is kept, not lost
        turn.outcome, turn.why = "error", f"{type(error).__name__}: {error}"
        return turn
    if isinstance(outcome, decider.Planned):
        turn.outcome = "planned"
        turn.plan = dict(fix_plans.notice(outcome.plan))
        decided = (
            ToolCall.objects.filter(
                session=job.session, agent=decider.DECIDER_NAME, tool=decider.Decide.name
            )
            .exclude(result__startswith="Refused:")
            .exclude(result__startswith="Failed:")
            .last()
        )
        turn.decide = decided.arguments if decided else None
    elif isinstance(outcome, decider.GoAhead):
        turn.outcome = "go_ahead"
        turn.gone_ahead = sorted(row.scene.number for row in outcome.rows)
        # The chat carries each scene on (ticket 08); here its check only waits no more, as
        # no clip is made.
        FixCheck.objects.filter(step__plan_scene__in=outcome.rows).update(
            gone_ahead_at=timezone.now()
        )
    elif isinstance(outcome, decider.Yes | decider.No):
        turn.outcome = "yes" if isinstance(outcome, decider.Yes) else "no"
        # The chat settles the plan on the answer (ticket 08), so the next round starts afresh.
        outcome.plan.state = (
            FixPlan.State.CONFIRMED if turn.outcome == "yes" else FixPlan.State.DECLINED
        )
        outcome.plan.confirmed_by = message if turn.outcome == "yes" else None
        outcome.plan.save()
    else:
        turn.outcome = "not_a_fix"
    return turn
