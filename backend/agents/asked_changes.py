"""File owner asks, count their scene runs, and read back what happened."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, TypedDict

from django.db.models import Max, Q, QuerySet, Sum

from agents.loop import Refused
from agents.models import AskedChange, AskedChangeFiling, AskedChangeScene, ToolCall
from agents.money import dollars
from chat.models import Message, Session
from gateway.models import ModelCall
from jobs import stale
from jobs.models import AgentFixTry, ProducedItem, Scene, SceneAgentRun, SceneStep

DID_NOT_COUNT = "This run did not count as a try."


def owner_message_for(call: ToolCall) -> Message | None:
    """The last owner message the agent read, even if it quotes earlier words."""
    messages = call.session.messages.filter(role=Message.Role.USER, pasted_page=False)
    if call.read_up_to is None:
        messages = messages.filter(created_at__lte=call.created_at)
    else:
        messages = messages.filter(seq__lte=call.read_up_to)
    return messages.order_by("seq").last()


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
    filing, _ = AskedChangeFiling.objects.get_or_create(
        coverage=coverage,
        tool_call=call,
        defaults={"owner_message": message, "instruction": instruction},
    )
    return filing


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
    if not counted(run, step):
        status = DID_NOT_COUNT
    else:
        number = _try_number(run)
        status = f"This was try {number}."
    return (
        f" Asked change #{change.number}, {change.description.rstrip('.')}, "
        f"scene {step.scene.number}. {status} It cost {dollars(run_cost(run))}."
    )


def run_failed(step: SceneStep) -> str:
    """Record a technical step failure on the active owner-asked run, and report it."""
    run = SceneAgentRun.objects.filter(agent_fix__step=step).order_by("number").last()
    if run is None or run.asked_filing_id is None:
        return ""
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
    asks: list[AskRecord]


class ChangeRecord(TypedDict):
    number: int
    description: str
    scenes: list[SceneRecord]


def _run_record(run: SceneAgentRun, number: int | None) -> RunRecord:
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
