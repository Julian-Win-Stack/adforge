"""File owner asks, count their scene runs, and read back what happened."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict

from django.conf import settings
from django.db.models import Max, Q, QuerySet, Sum

from agents.loop import Refused
from agents.models import (
    AskedChange,
    AskedChangeBrief,
    AskedChangeFiling,
    AskedChangeScene,
    ToolCall,
)
from agents.money import dollars
from chat.models import Message, Session
from gateway.models import ModelCall
from jobs import stale
from jobs.models import AgentFixTry, ProducedItem, Scene, SceneAgentRun, SceneStep

DID_NOT_COUNT = "This run did not count as a try."
_KEEP_THE_CLIP = (
    "Keep the scene's clip. Tell the owner what was tried, how it went and what it cost."
)


class BriefBeforeRepeat(Refused):
    def __init__(self, filing: AskedChangeFiling, reason: str) -> None:
        self.filing = filing
        super().__init__(reason)


def try_limit() -> int:
    return settings.ASKED_CHANGE_TRY_LIMIT


def is_closed(total_tries: int) -> bool:
    return total_tries >= try_limit()


def owner_message_for(call: ToolCall) -> Message | None:
    """The last owner message the agent read, even if it quotes earlier words."""
    messages = call.session.messages.filter(role=Message.Role.USER, pasted_page=False)
    if call.read_up_to is None:
        messages = messages.filter(created_at__lte=call.created_at)
    else:
        messages = messages.filter(seq__lte=call.read_up_to)
    return messages.order_by("seq").last()


def _owner_runs(scene: Scene, message: Message) -> QuerySet[SceneAgentRun]:
    return SceneAgentRun.objects.filter(
        asked_filing__coverage__scene=scene,
        asked_filing__owner_message=message,
    ).select_related("agent_fix__step")


def require_producer_start(call: ToolCall, scene: Scene) -> None:
    """After an owner-asked run, only a filed owner note may start another run for its ask."""
    message = owner_message_for(call)
    if message is not None and _owner_runs(scene, message).exists():
        raise Refused(
            f"another run after an owner-asked run needs an owner-asked note. {_KEEP_THE_CLIP}"
        )


def _require_owner_try(scene: Scene, message: Message) -> None:
    if any(counted(run) for run in _owner_runs(scene, message)):
        raise Refused(
            f"this owner message already had its one try for this scene. {_KEEP_THE_CLIP}"
        )


def file_note(
    call: ToolCall,
    scene: Scene,
    *,
    asked_change: int | None,
    new_asked_change: str | None,
    instruction: str,
) -> AskedChangeFiling:
    """File an owner-asked note while its scene and job are locked."""
    description = new_asked_change.strip() if new_asked_change is not None else ""
    if (asked_change is None) == (new_asked_change is None) or (
        new_asked_change is not None and not description
    ):
        raise Refused(
            "give either the number of an asked change already on file or a sentence saying "
            "what new result the owner asked for."
        )
    message = owner_message_for(call)
    if message is None:
        raise Refused("there is no owner message for this asked change.")
    changes = AskedChange.objects.filter(session=call.session)
    if asked_change is None:
        _require_owner_try(scene, message)
        number = (changes.aggregate(last=Max("number"))["last"] or 0) + 1
        change = AskedChange.objects.create(
            session=call.session, number=number, description=description
        )
    else:
        selected = changes.filter(number=asked_change).first()
        if selected is None:
            filed = "; ".join(f"#{each.number}: {each.description}" for each in changes)
            raise Refused(
                f"asked change #{asked_change} isn't on file. "
                + (f"Asked changes on file: {filed}." if filed else "No asked changes are on file.")
            )
        change = selected
    coverage = AskedChangeScene.objects.filter(change=change, scene=scene).first()
    if coverage is None:
        coverage = AskedChangeScene.objects.create(
            change=change, scene=scene, before_clip=stale.current_clip(scene)
        )
    refusal = _closed_refusal(coverage, message)
    if asked_change is not None and not refusal:
        _require_owner_try(scene, message)
    filing, _ = AskedChangeFiling.objects.get_or_create(
        coverage=coverage,
        tool_call=call,
        defaults={"owner_message": message, "instruction": instruction, "refusal": refusal},
    )
    if asked_change is not None and not filing.refusal:
        _brief_repeat(filing)
    return filing


def _brief_repeat(filing: AskedChangeFiling) -> None:
    coverage = filing.coverage
    message = filing.owner_message
    if AskedChangeBrief.objects.filter(scene_id=coverage.scene_id, owner_message=message).exists():
        return
    previous = [
        run
        for run in tries_before_note(coverage, message)
        if run.asked_filing is not None and run.asked_filing.owner_message.seq < message.seq
    ]
    if previous:
        raise BriefBeforeRepeat(
            filing,
            f"Try history for asked change #{coverage.change.number}\n"
            f"{try_history(previous)}\n"
            "Send the note again for the next try. Use a clearly different kind of action "
            "from every earlier try. If the note you just sent already does that, resend it "
            "as is. Tries remain, so tell the owner what is still wrong without saying the "
            "change can't be made.",
        )


def record_repeat_brief(brief: BriefBeforeRepeat, answer: str) -> None:
    """Store what was told after the refused scene handoff has rolled back."""
    filing = brief.filing
    AskedChangeBrief.objects.get_or_create(
        scene_id=filing.coverage.scene_id, owner_message_id=filing.owner_message_id
    )
    AskedChangeFiling.objects.update_or_create(
        coverage_id=filing.coverage_id,
        tool_call_id=filing.tool_call_id,
        defaults={
            "owner_message_id": filing.owner_message_id,
            "instruction": filing.instruction,
            "refusal": answer,
        },
    )


def filed_already(call: ToolCall, scene: Scene) -> str:
    """Replay the earlier filing if the producer's tool checkpoint is run again."""
    filing = AskedChangeFiling.objects.filter(tool_call=call, coverage__scene=scene).first()
    if filing is None:
        return ""
    if filing.refusal:
        return filing.refusal
    window = filing.runs.exclude(reason=SceneAgentRun.PENDING_DELIVERED).order_by("pk").last()
    return f"Scene {scene.number}'s note was handed on already." + filed_line(filing, window)


def note_could_not_start(filing: AskedChangeFiling | None, reason: str) -> str:
    if filing is not None:
        filing.refusal = reason
        filing.save(update_fields=["refusal"])
    return reason


def filed_line(filing: AskedChangeFiling | None, run: SceneAgentRun | None = None) -> str:
    if filing is None:
        return ""
    change = filing.coverage.change
    number = _try_number(run) if run else 1 + len(tries_for(filing.coverage))
    return f" Asked change #{change.number}, {change.description.rstrip('.')}, try {number}."


def run_ended(step: SceneStep, run: SceneAgentRun | None) -> str:
    """The producer's extra line when an owner-asked run ends."""
    if run is None or run.asked_filing is None:
        return ""
    filing = run.asked_filing
    change = filing.coverage.change
    guidance = ""
    if not counted(run, step):
        status = DID_NOT_COUNT
        if run.step_failed:
            status += " The owner's note can be sent again in the same message."
    else:
        number = _try_number(run)
        status = f"This was try {number}, the one try for this scene in that owner message."
        if is_closed(number):
            guidance = " No tries are left on this scene. " + _closing_guidance()
        else:
            guidance = _unfinished_try_line(run) or " Tries remain."
    return (
        f" Asked change #{change.number}, {change.description.rstrip('.')}, "
        f"scene {step.scene.number}. {status} It cost {dollars(run_cost(run))}.{guidance}"
    )


def _unfinished_try_line(run: SceneAgentRun) -> str:
    from agents.scene_agent.hand_back import HANDED_BACK

    if (
        (not run.step_failed and not run.reason)
        or run.reason == SceneAgentRun.SUPERSEDED
        or run.reason.startswith(HANDED_BACK)
        or run.agent_fix.paused_at is not None
    ):
        return ""
    return (
        " Tries remain. Tell the owner what is still wrong, what this try did, and its cost. "
        "Say the change is still open."
    )


def run_failed(step: SceneStep) -> str:
    """Record a technical step failure on the active owner-asked run, and report it."""
    from agents.scene_agent.lifecycle import keep_owner_clip

    run = SceneAgentRun.objects.filter(agent_fix__step=step).order_by("number").last()
    if run is None or run.asked_filing_id is None:
        return ""
    keep_owner_clip(run.agent_fix, run)
    run.step_failed = True
    run.reason = step.reason
    run.finished_at = run.finished_at or step.finished_at
    run.save(update_fields=["step_failed", "reason", "finished_at"])
    return run_ended(step, run)


def _clip_rows(run: SceneAgentRun) -> QuerySet[AgentFixTry]:
    return run.agent_fix.tries.filter(sent__scene_run=run.pk)


def made_clip(run: SceneAgentRun, *, by: datetime | None = None) -> bool:
    # Film reserves its allowance before the provider answers. A saved clip can also
    # predate the judge or transcript that makes the try's result final.
    clip = Q(clip__isnull=False)
    produced = Q(step__produced__kind=ProducedItem.Kind.CLIP)
    if by is not None:
        clip &= Q(clip__created_at__lte=by)
        produced &= Q(step__produced__created_at__lte=by)
    return _clip_rows(run).filter(clip | produced).exists()


def counted(run: SceneAgentRun, step: SceneStep | None = None) -> bool:
    """Whether this run uses a try, including an active run still in progress."""
    if run.reason == SceneAgentRun.PENDING_DELIVERED:
        return False
    if run.reason == SceneAgentRun.SUPERSEDED:
        # A render that arrives after the replacement remains a version, but was
        # still unfinished when the new ask was admitted.
        return made_clip(run, by=run.finished_at)
    step = step or run.agent_fix.step
    return (not run.step_failed and step.status != SceneStep.Status.CANCELLED) or made_clip(run)


def tries_for(coverage: AskedChangeScene) -> list[SceneAgentRun]:
    runs = (
        SceneAgentRun.objects.filter(asked_filing__coverage=coverage)
        .select_related("agent_fix__step")
        .order_by("created_at", "pk")
    )
    return [run for run in runs if counted(run)]


def tries_before_note(coverage: AskedChangeScene, message: Message) -> list[SceneAgentRun]:
    """Count the tries that will still count if this note opens a new window."""
    tries = tries_for(coverage)
    if tries:
        latest = tries[-1]
        filing = latest.asked_filing
        assert filing is not None
        if (
            latest.finished_at is None
            and latest.agent_fix.step.status == SceneStep.Status.RUNNING
            and filing.owner_message.seq < message.seq
            and not made_clip(latest)
        ):
            return tries[:-1]
    return tries


def _closed_refusal(coverage: AskedChangeScene, message: Message) -> str:
    tries = tries_before_note(coverage, message)
    if not is_closed(len(tries)):
        return ""
    change = coverage.change
    return (
        f"Scene {coverage.scene.number}'s note was refused: asked change #{change.number}, "
        f"{change.description.rstrip('.')}, is closed on this scene. "
        f"All {len(tries)} tries have been used. {_closing_guidance()}\n{try_history(tries)}"
    )


def _closing_guidance() -> str:
    return (
        "If the owner is still dissatisfied, say plainly that you couldn't make this change. "
        "Say what you tried and offer the closest alternative you can make. "
        "A clearly different result the owner accepts is a new asked change. "
        "The same result in different words or a new way to reach it is still the closed change."
    )


def run_cost(run: SceneAgentRun) -> Decimal:
    cost: Decimal | None = ModelCall.objects.filter(tool_call__scene_run=run).aggregate(
        cost=Sum("cost_usd")
    )["cost"]
    return cost or Decimal(0)


def _try_number(run: SceneAgentRun) -> int:
    assert run.asked_filing is not None
    return 1 + sum(
        (earlier.created_at, earlier.pk) < (run.created_at, run.pk)
        for earlier in tries_for(run.asked_filing.coverage)
    )


type Outcome = Literal["running", "passed", "gave_up", "failed", "superseded", "cancelled"]


class RunRecord(TypedDict):
    instruction: str
    try_number: int | None
    outcome: Outcome
    detail: str
    cost_usd: float
    passed_clip: int | None


class FilingRefusal(TypedDict):
    instruction: str
    reason: str


class AskRecord(TypedDict):
    message: str
    message_id: int
    message_seq: int
    refusals: list[FilingRefusal]
    tried: bool
    runs: list[RunRecord]


class SceneRecord(TypedDict):
    scene: int
    scene_id: int
    removed: bool
    before_version: int | None
    tries: int
    closed: bool
    asks: list[AskRecord]


class ChangeRecord(TypedDict):
    number: int
    description: str
    scenes: list[SceneRecord]


def _outcome(run: SceneAgentRun) -> tuple[Outcome, int | None]:
    passed_clip = (
        _clip_rows(run)
        .filter(passed=True, clip__isnull=False)
        .values_list("clip_id", flat=True)
        .last()
    )
    if run.reason == SceneAgentRun.SUPERSEDED:
        outcome: Outcome = "superseded"
    elif run.step_failed:
        outcome = "failed"
    elif run.agent_fix.step.status == SceneStep.Status.CANCELLED:
        outcome = "cancelled"
    elif passed_clip is not None:
        outcome = "passed"
    elif run.finished_at is None:
        outcome = "running"
    else:
        outcome = "gave_up"
    return outcome, passed_clip


def try_history(runs: Sequence[SceneAgentRun]) -> str:
    """Tell the producer what was tried, how each try ended, and its cost."""
    lines = []
    for number, run in enumerate(runs, 1):
        outcome, _ = _outcome(run)
        detail = f" ({run.reason})" if run.reason else ""
        lines.append(
            f"Try {number}: {run.instruction} · {outcome.replace('_', ' ')}{detail} "
            f"· {dollars(run_cost(run))}"
        )
    return "\n".join(lines)


def _run_record(run: SceneAgentRun, number: int | None) -> RunRecord:
    outcome, passed_clip = _outcome(run)
    return RunRecord(
        instruction=run.instruction,
        try_number=number,
        outcome=outcome,
        detail=run.reason,
        cost_usd=float(run_cost(run)),
        passed_clip=passed_clip,
    )


def _scene_record(coverage: AskedChangeScene) -> SceneRecord:
    numbered = {run.pk: number for number, run in enumerate(tries_for(coverage), 1)}
    asks: dict[int, AskRecord] = {}
    for filing in coverage.filings.select_related("owner_message").order_by(
        "owner_message__seq", "tool_call__created_at", "pk"
    ):
        runs = [
            _run_record(run, numbered.get(run.pk))
            for run in filing.runs.select_related("agent_fix__step")
            .exclude(reason=SceneAgentRun.PENDING_DELIVERED)
            .order_by("created_at", "pk")
        ]
        ask = asks.setdefault(
            filing.owner_message_id,
            AskRecord(
                message=filing.owner_message.text,
                message_id=filing.owner_message_id,
                message_seq=filing.owner_message.seq,
                refusals=[],
                tried=False,
                runs=[],
            ),
        )
        if filing.refusal:
            ask["refusals"].append(
                FilingRefusal(instruction=filing.instruction, reason=filing.refusal)
            )
        ask["tried"] = ask["tried"] or any(run["try_number"] is not None for run in runs)
        ask["runs"].extend(runs)
    return SceneRecord(
        scene=coverage.scene.number,
        scene_id=coverage.scene_id,
        removed=coverage.scene.removed_at is not None,
        before_version=coverage.before_clip.version if coverage.before_clip else None,
        tries=len(numbered),
        closed=is_closed(len(numbered)),
        asks=list(asks.values()),
    )


def summary(session: Session) -> list[ChangeRecord]:
    """The asked changes, scenes and owner asks in one session, for scoring."""
    return [
        ChangeRecord(
            number=change.number,
            description=change.description,
            scenes=[
                _scene_record(coverage)
                for coverage in change.coverage.select_related("scene", "before_clip").order_by(
                    "scene__number", "scene_id"
                )
            ],
        )
        for change in AskedChange.objects.filter(session=session)
    ]
