"""PROTOTYPE (#738 version 2), never merged: the owner's things and their tries.

A thing is one change the owner asked for on one scene. The producer files every owner-asked
scene note with pass_note as the same thing as number N, or a new thing. A try is one scene
agent run started for an owner-asked note on a thing; a run that broke for a technical reason
with no clip is not counted. Code decides from the count, never the producer:

- a repeat with tries left is refused once per owner message, before anything films, with
  what each earlier try did and how it ended; the note sent again is filmed;
- one owner message gives a thing at most one try, and nothing films again by itself after
  an owner-asked try on that scene;
- after THING_TRIES tries the thing is closed for the whole session: every filing under it is
  refused, with why.

Nothing is shown with normal owner messages. The things are kept in OwnerProblem rows; their
tries are read from the scene runs whose quotes carry the thing's number."""

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Sum

from agents.loop import Refused
from agents.models import OwnerProblem, ToolCall
from chat.models import Message, Session
from jobs.models import Scene, SceneAgentRun, SceneStep

THING = "thing"
# The owner message the note answers: one ask per message.
ASK = "ask_message"
SUPERSEDED = "Superseded by a new owner note."


class Briefing(Refused):
    """A repeat refused once, with the history, before its try: kept as briefed afterwards,
    when the refused note's own changes are rolled back."""

    def __init__(self, text: str, thing: OwnerProblem, message: Message) -> None:
        super().__init__(text)
        self.thing = thing
        self.message = message

    def keep(self) -> None:
        thing = OwnerProblem.objects.get(pk=self.thing.pk)
        thing.briefed = sorted({*thing.briefed, self.message.pk})
        thing.save(update_fields=["briefed"])


def limit() -> int:
    return int(getattr(settings, "THING_TRIES", 3))


def source_message(call: ToolCall) -> Message:
    """The owner message the producer is answering with this call."""
    source = (
        call.session.messages.filter(
            role=Message.Role.USER, pasted_page=False, created_at__lte=call.created_at
        )
        .order_by("seq")
        .last()
    )
    assert source is not None, "a note follows an owner message"
    return source


def _runs(session: Session) -> Any:
    from agents.scene_agent import PENDING_NOTE_DELIVERED

    return (
        SceneAgentRun.objects.filter(agent_fix__step__tool_call__session=session)
        .exclude(reason=PENDING_NOTE_DELIVERED)
        .select_related("agent_fix", "agent_fix__step", "agent_fix__step__scene")
        .order_by("created_at", "pk")
    )


def _thing_of(run: SceneAgentRun) -> int | None:
    return next(
        (quote[THING] for quote in run.quotes or [] if isinstance(quote.get(THING), int)), None
    )


def _ask_of(run: SceneAgentRun) -> int | None:
    return next((quote[ASK] for quote in run.quotes or [] if isinstance(quote.get(ASK), int)), None)


def _owner_asked(run: SceneAgentRun) -> bool:
    return any(quote.get("owner_asked") for quote in run.quotes or [])


def broke(run: SceneAgentRun) -> bool:
    """The run ended for a technical reason with no clip, or was replaced before it filmed:
    not a try."""
    if run.reason == SUPERSEDED and run.clips == 0:
        return True
    fixing = run.agent_fix
    last = fixing.scene_runs.order_by("-created_at", "-pk").first()
    return (
        last is not None
        and last.pk == run.pk
        and fixing.step.status == SceneStep.Status.FAILED
        and fixing.passed_id is None
        and run.clips == 0
    )


def tries(session: Session, number: int) -> list[SceneAgentRun]:
    """The thing's counted tries, oldest first."""
    return [
        run
        for run in _runs(session)
        if _thing_of(run) == number and _owner_asked(run) and not broke(run)
    ]


def ended(run: SceneAgentRun) -> str:
    fixing = run.agent_fix
    last = fixing.scene_runs.order_by("-created_at", "-pk").first()
    current = last is not None and last.pk == run.pk
    if broke(run):
        return "broke for a technical reason with no clip (not counted as a try)"
    if current and fixing.passed_id is not None:
        return "made a clip that passed its check"
    if current and fixing.gave_up:
        return f"the scene agent gave up: {fixing.gave_up[:300]}"
    if run.reason and run.reason != SUPERSEDED:
        return run.reason[:300]
    if current and fixing.finished_at is None and run.finished_at is None:
        return "still running"
    return "ended without a passing clip"


def cost(run: SceneAgentRun) -> Decimal:
    from gateway.models import ModelCall

    later = (
        run.agent_fix.scene_runs.filter(created_at__gt=run.created_at)
        .order_by("created_at")
        .first()
    )
    calls = ModelCall.objects.filter(tool_call__agent_fix=run.agent_fix)
    calls = calls.filter(created_at__gte=run.created_at)
    if later is not None:
        calls = calls.filter(created_at__lt=later.created_at)
    spent: Decimal | None = calls.aggregate(cost=Sum("cost_usd"))["cost"]
    return spent or Decimal(0)


def _history(runs: list[SceneAgentRun]) -> str:
    return " ".join(
        f"Try {number}: {run.instruction.strip()} Ended: {ended(run)}; cost ${cost(run):.2f}."
        for number, run in enumerate(runs, start=1)
    )


def own_note_allowed(call: ToolCall, scene: Scene) -> None:
    """Refuses filming the producer starts by itself on a scene that already had an
    owner-asked run for the owner's latest message: one ask is one try."""
    source = source_message(call)
    if any(
        _owner_asked(run) and _ask_of(run) == source.pk
        for run in _runs(call.session).filter(agent_fix__step__scene=scene)
    ):
        raise Refused(
            f"scene {scene.number} already had its one try for the owner's latest message. "
            "Nothing is filmed again without the owner asking: the scene keeps the clip it "
            "has. Tell the owner what was tried, how it went and what it cost. If that try "
            "broke for a technical reason with no clip, send it again as the owner's note "
            "under its thing."
        )


def file(
    call: ToolCall,
    scene: Scene,
    quotes: list[dict[str, Any]],
    *,
    owner_asked: bool,
    same_thing: int | None,
    new_thing: str,
) -> tuple[list[dict[str, Any]], str]:
    """The note's quotes marked with its thing and ask, and a line for the tool's result.
    Raises Refused for a note the count doesn't allow, and Briefing for a repeat's first
    filing in an owner message."""
    new_thing = new_thing.strip()
    if not owner_asked:
        own_note_allowed(call, scene)
        return quotes, ""
    if same_thing is None and not new_thing:
        raise Refused(
            "an owner-asked note must be filed: same_thing with the number of the thing the "
            "owner is asking about again, or new_thing with one sentence for a new request."
        )
    if same_thing is not None and new_thing:
        raise Refused("give either same_thing or new_thing, not both.")
    source = source_message(call)
    session = call.session
    if any(
        _owner_asked(run) and _ask_of(run) == source.pk and not broke(run)
        for run in _runs(session).filter(agent_fix__step__scene=scene)
    ):
        raise Refused(
            f"scene {scene.number} already had its one try for the owner's latest message. "
            "Nothing is filmed again until the owner asks again: the scene keeps the clip it "
            "has. Tell the owner what was tried, how it went and what it cost."
        )
    if new_thing:
        # Several notes of one call naming the same new thing on one scene share it.
        thing = session.owner_problems.filter(
            description__iexact=new_thing, scene=scene.number, created_at__gte=call.created_at
        ).first()
        if thing is None:
            last = session.owner_problems.order_by("-number").first()
            thing = OwnerProblem.objects.create(
                session=session,
                number=(last.number + 1) if last else 1,
                description=new_thing,
                scene=scene.number,
            )
        said = (
            f" Filed as new thing {thing.number} (scene {scene.number}): {thing.description} "
            f"This is try 1 of {limit()}."
        )
        return [{**quote, THING: thing.number, ASK: source.pk} for quote in quotes], said
    assert same_thing is not None
    found = session.owner_problems.filter(number=same_thing).first()
    if found is None:
        listed = [
            f"{each.number} (scene {each.scene}): {each.description}"
            for each in session.owner_problems.all()
        ]
        raise Refused(
            f"there is no thing {same_thing}. The things so far: "
            f"{'; '.join(listed) or 'none'}. Name one of them, or file a new_thing."
        )
    thing = found
    if thing.scene is not None and thing.scene != scene.number:
        raise Refused(
            f"thing {thing.number} is about scene {thing.scene}. The same request on scene "
            f"{scene.number} is a thing of its own: file it as a new_thing."
        )
    used = tries(session, thing.number)
    history = _history(used)
    if len(used) >= limit():
        raise Refused(
            f"thing {thing.number} ({thing.description}) is closed: it had its {limit()} tries "
            f"and the owner is still not satisfied, so nothing more is filmed for it in this "
            f"session, however it is asked. {history} Tell the owner plainly that you couldn't "
            "get it, name what you tried, and offer the closest thing you can do. A clearly "
            "different result the owner accepts is a new thing; the same result in other "
            "words, or a new way to reach it, is still this closed thing."
        )
    if any(_ask_of(run) == source.pk for run in used):
        raise Refused(
            f"thing {thing.number} already had its one try for the owner's latest message. "
            "Nothing is filmed again for it until the owner asks again: tell the owner what "
            "was tried, how it went and what it cost."
        )
    if used and source.pk not in thing.briefed:
        raise Briefing(
            f"not filmed yet. The owner is asking for thing {thing.number} again "
            f"({thing.description}): {len(used)} of its {limit()} tries are used, so it gets "
            f"another. What the earlier tries did: {history} Your next try must be a clearly "
            "different kind of action from all of these, not the same one reworded. Write "
            f"that new instruction and send the note again with same_thing {thing.number}; it "
            "will be filmed. Don't tell the owner you couldn't do it while tries remain.",
            thing,
            source,
        )
    position = len(used) + 1
    final = " It is the last try for this thing." if position == limit() else ""
    said = f" Filed under thing {thing.number}: try {position} of {limit()}.{final}"
    return [{**quote, THING: thing.number, ASK: source.pk} for quote in quotes], said


def try_line(run: SceneAgentRun) -> str:
    """For the producer when an owner-asked run ends: which try of which thing it was."""
    number = _thing_of(run)
    if number is None or not _owner_asked(run):
        return ""
    if broke(run):
        return f" This run of thing {number} broke with no clip, so it isn't counted as a try."
    used = tries(run.agent_fix.step.tool_call.session, number)
    position = next((n for n, each in enumerate(used, start=1) if each.pk == run.pk), len(used))
    line = (
        f" It was try {position} of {limit()} on thing {number}. This was the owner's ask's one "
        "try: don't film this scene again by yourself; the scene keeps the clip it has. Tell "
        "the owner what you tried, how it went and what it cost."
    )
    if position >= limit():
        line += (
            f" Thing {number} has no tries left: if the owner says it is still not right, tell "
            "them plainly you couldn't get it and offer the closest thing you can do."
        )
    else:
        line += (
            " If it didn't work, say honestly that it is still not right; don't say you are "
            "giving up, as tries remain."
        )
    return line


def summary(session: Session) -> list[dict[str, Any]]:
    """Every thing with its asks and runs, for scoring scripts."""
    runs = list(_runs(session))
    listed = []
    for thing in session.owner_problems.all():
        mine = [run for run in runs if _thing_of(run) == thing.number]
        counted = [run for run in mine if _owner_asked(run) and not broke(run)]
        listed.append(
            {
                "number": thing.number,
                "description": thing.description,
                "scene": thing.scene,
                "asks": len({_ask_of(run) for run in mine if _ask_of(run) is not None}),
                "briefed": list(thing.briefed),
                "tries": len(counted),
                "closed": len(counted) >= limit(),
                "runs": [
                    {
                        "scene": run.agent_fix.step.scene.number,
                        "instruction": run.instruction,
                        "ask_message": _ask_of(run),
                        "counted": run in counted,
                        "clips": run.clips,
                        "ended": ended(run),
                        "cost_usd": str(cost(run)),
                    }
                    for run in mine
                ],
            }
        )
    return listed
