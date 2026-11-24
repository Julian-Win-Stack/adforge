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

    def __init__(self, text: str, thing: OwnerProblem, message: Message, key: int | str) -> None:
        super().__init__(text)
        self.thing = thing
        self.message = message
        self.key = key

    def keep(self) -> None:
        thing = OwnerProblem.objects.get(pk=self.thing.pk)
        thing.briefed = [*thing.briefed, self.key]
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


def tries(session: Session, number: int, scene: int | None = None) -> list[SceneAgentRun]:
    """The thing's counted tries, oldest first: on one scene, or on all its scenes."""
    return [
        run
        for run in _runs(session)
        if _thing_of(run) == number
        and _owner_asked(run)
        and not broke(run)
        and (scene is None or run.agent_fix.step.scene.number == scene)
    ]


def _scenes(thing: OwnerProblem) -> list[int]:
    listed = list(thing.scenes or [])
    if not listed and thing.scene is not None:
        listed = [thing.scene]
    return sorted(set(listed))


def record(session: Session, thing: OwnerProblem) -> str:
    """The whole record of a thing that covers several scenes; blank for one scene."""
    scenes = _scenes(thing)
    if len(scenes) < 2:
        return ""
    parts = []
    for number in scenes:
        used = tries(session, thing.number, number)
        state = "closed" if len(used) >= limit() else f"{len(used)} of {limit()} tries used"
        last = f"; last try ended: {ended(used[-1])}" if used else ""
        parts.append(f"scene {number} ({state}{last})")
    return (
        f" Thing {thing.number} covers scenes {', '.join(map(str, scenes))}, and they must "
        f"match: {'; '.join(parts)}."
    )


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


def ways_out_of(scene: int) -> str:
    """What the producer tells the owner when one scene of a multi-scene thing closes."""
    return (
        " The scenes of this thing must match, and they don't all have the change. Tell the "
        "owner plainly which scenes have the change and which don't, and offer the three ways "
        f"out: a different shot for scene {scene} only; a different approach for all the "
        "linked scenes; or putting the changed scenes back to their earlier versions with "
        "use_version so they match again, with nothing filmed. Film nothing for it until the "
        "owner picks one. The way out the owner picks is not the closed thing: a different "
        "shot or a different approach is filed as a new_thing (on that scene, or on all the "
        "linked scenes) and filmed, with its own tries."
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
        # The notes of one call naming the same new thing share it: one change, its scenes.
        thing = session.owner_problems.filter(
            description__iexact=new_thing, created_at__gte=call.created_at
        ).first()
        if thing is None:
            last = session.owner_problems.order_by("-number").first()
            thing = OwnerProblem.objects.create(
                session=session,
                number=(last.number + 1) if last else 1,
                description=new_thing,
                scene=scene.number,
                scenes=[scene.number],
            )
        elif scene.number not in _scenes(thing):
            thing.scenes = sorted({*_scenes(thing), scene.number})
            thing.save(update_fields=["scenes"])
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
    if scene.number not in _scenes(thing):
        # A scene joins the thing later: its own first try, filmed at once.
        thing.scenes = sorted({*_scenes(thing), scene.number})
        thing.save(update_fields=["scenes"])
        said = (
            f" Scene {scene.number} joins thing {thing.number}: try 1 of {limit()} on scene "
            f"{scene.number}.{record(session, thing)}"
        )
        return [{**quote, THING: thing.number, ASK: source.pk} for quote in quotes], said
    used = tries(session, thing.number, scene.number)
    history = _history(used)
    linked = record(session, thing)
    if len(used) >= limit():
        ways_out = (
            ways_out_of(scene.number)
            if linked
            else " Tell the owner plainly that you couldn't get it, name what you tried, and "
            "offer the closest thing you can do."
        )
        raise Refused(
            f"thing {thing.number} ({thing.description}) is closed on scene {scene.number}: it "
            f"had its {limit()} tries there and the owner is still not satisfied, so nothing "
            f"more is filmed for it on that scene in this session, however it is asked. "
            f"{history}{linked}{ways_out} A clearly different result the owner accepts is a new "
            "thing; the same result in other words, or a new way to reach it, is still this "
            "closed thing."
        )
    if any(_ask_of(run) == source.pk for run in used):
        raise Refused(
            f"thing {thing.number} already had its one try on scene {scene.number} for the "
            "owner's latest message. Nothing is filmed again for it until the owner asks "
            "again: tell the owner what was tried, how it went and what it cost."
        )
    key = source.pk if len(_scenes(thing)) < 2 else f"{source.pk}:{scene.number}"
    if used and key not in thing.briefed:
        raise Briefing(
            f"not filmed yet. The owner is asking for thing {thing.number} again "
            f"({thing.description}) on scene {scene.number}: {len(used)} of its {limit()} tries "
            f"there are used, so it gets another. What the earlier tries did: {history}{linked} "
            "Your next try must be a clearly different kind of action from all of these, not "
            "the same one reworded. If the note you just sent already is clearly different from "
            f"all of them, send it again as it is with same_thing {thing.number}; otherwise "
            "write a new instruction that is, and send that. It will be filmed. Don't tell the "
            "owner you couldn't do it while tries remain.",
            thing,
            source,
            key,
        )
    position = len(used) + 1
    final = " It is the last try for this thing on this scene." if position == limit() else ""
    said = (
        f" Filed under thing {thing.number}: try {position} of {limit()} on scene "
        f"{scene.number}.{final}{linked}"
    )
    return [{**quote, THING: thing.number, ASK: source.pk} for quote in quotes], said


def try_line(run: SceneAgentRun) -> str:
    """For the producer when an owner-asked run ends: which try of which thing it was."""
    number = _thing_of(run)
    if number is None or not _owner_asked(run):
        return ""
    if broke(run):
        return f" This run of thing {number} broke with no clip, so it isn't counted as a try."
    session = run.agent_fix.step.tool_call.session
    scene = run.agent_fix.step.scene.number
    used = tries(session, number, scene)
    position = next((n for n, each in enumerate(used, start=1) if each.pk == run.pk), len(used))
    thing = session.owner_problems.filter(number=number).first()
    linked = record(session, thing) if thing is not None else ""
    line = (
        f" It was try {position} of {limit()} on thing {number}, scene {scene}.{linked} This "
        "was the owner's ask's one try: don't film this scene again by yourself; the scene "
        "keeps the clip it has. Tell "
        "the owner what you tried, how it went and what it cost."
    )
    if position >= limit():
        line += (
            f" Thing {number} has no tries left on scene {scene}: if the owner says it is still "
            "not right there, tell them plainly you couldn't get it"
            + (f".{ways_out_of(scene)}" if linked else " and offer the closest thing you can do.")
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
                "scenes": _scenes(thing),
                "tries_per_scene": {
                    str(n): len(tries(session, thing.number, n)) for n in _scenes(thing)
                },
                "closed_scenes": [
                    n for n in _scenes(thing) if len(tries(session, thing.number, n)) >= limit()
                ],
                "asks": len({_ask_of(run) for run in mine if _ask_of(run) is not None}),
                "briefed": list(thing.briefed),
                "tries": len(counted),
                "closed": any(
                    len(tries(session, thing.number, n)) >= limit() for n in _scenes(thing)
                ),
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
