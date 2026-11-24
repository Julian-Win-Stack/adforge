"""PROTOTYPE (#738), never merged: the session's problem list.

The producer files each owner-asked scene note under a problem with pass_note. Code keeps
the list, records each scene run under its problem (in the run's quotes, as "problem"), and
counts the owner's asks: the distinct owner messages that led to an owner-asked note on it.
With each owner message, code gives the producer the list as a system note, and for a
problem asked about REPEATED_ASK_LIMIT times or more, a plain suggestion not to repeat it."""

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Sum

from agents.loop import Refused
from agents.models import OwnerProblem, SystemNote, ToolCall
from chat.models import Message, Session
from jobs.models import Scene, SceneAgentRun

PROBLEM = "problem"
# The owner message that led to an owner-asked note: one ask per message, however many
# scenes it reached.
ASK = "ask_message"


def _limit() -> int:
    return int(getattr(settings, "REPEATED_ASK_LIMIT", 2))


def _source(call: ToolCall) -> Message:
    source = (
        call.session.messages.filter(
            role=Message.Role.USER, pasted_page=False, created_at__lte=call.created_at
        )
        .order_by("seq")
        .last()
    )
    assert source is not None, "a note follows an owner message"
    return source


def file(
    call: ToolCall,
    scene: Scene,
    quotes: list[dict[str, Any]],
    *,
    owner_asked: bool,
    problem: int | None,
    new_problem: str,
) -> tuple[list[dict[str, Any]], str]:
    """The note's quotes marked with its problem, and a line for the tool's result.
    Raises Refused for an owner-asked note that names no problem, both, or an unknown one."""
    new_problem = new_problem.strip()
    if not owner_asked:
        # The producer's own re-plan joins the problem of the note it follows on the scene.
        earlier = (
            _runs(call.session)
            .filter(agent_fix__step__scene=scene)
            .order_by("-created_at", "-pk")
            .first()
        )
        number = _problem_of(earlier) if earlier is not None else None
        if number is None:
            return quotes, ""
        return [{**quote, PROBLEM: number} for quote in quotes], ""
    if problem is None and not new_problem:
        raise Refused(
            "an owner-asked note must name its problem: the number of one on the problem "
            "list, or new_problem with one sentence for a new request."
        )
    if problem is not None and new_problem:
        raise Refused("name either an existing problem's number or a new_problem, not both.")
    if problem is not None:
        if not call.session.owner_problems.filter(number=problem).exists():
            listed = list(call.session.owner_problems.values_list("number", flat=True))
            raise Refused(
                f"there is no problem {problem} on the list (it has {listed or 'none'}). "
                "Name one on it, or describe a new_problem."
            )
        number = problem
        said = f" Filed under problem {number}."
    else:
        # Several scenes of one call naming the same new problem share it.
        same = call.session.owner_problems.filter(
            description__iexact=new_problem, created_at__gte=call.created_at
        ).first()
        if same is None:
            last = call.session.owner_problems.order_by("-number").first()
            same = OwnerProblem.objects.create(
                session=call.session,
                number=(last.number + 1) if last else 1,
                description=new_problem,
            )
        number = same.number
        said = f" Filed under new problem {number}: {same.description}"
    source = _source(call)
    return [{**quote, PROBLEM: number, ASK: source.pk} for quote in quotes], said


def _problem_of(run: SceneAgentRun) -> int | None:
    return next(
        (quote[PROBLEM] for quote in run.quotes if isinstance(quote.get(PROBLEM), int)), None
    )


def _runs(session: Session) -> Any:
    from agents.scene_agent import PENDING_NOTE_DELIVERED

    return (
        SceneAgentRun.objects.filter(agent_fix__step__tool_call__session=session)
        .exclude(reason=PENDING_NOTE_DELIVERED)
        .select_related("agent_fix", "agent_fix__step__scene")
    )


def _ended(run: SceneAgentRun) -> str:
    fixing = run.agent_fix
    last = fixing.scene_runs.order_by("-created_at", "-pk").first()
    current = last is not None and last.pk == run.pk
    if run.reason:
        return run.reason[:300]
    if current and fixing.passed_id is not None:
        return "made a clip that passed its check"
    if current and fixing.gave_up:
        return f"gave up: {fixing.gave_up[:300]}"
    if current and fixing.finished_at is None and run.finished_at is None:
        return "still running"
    return "ended without a passing clip"


def _cost(run: SceneAgentRun) -> Decimal:
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
    cost: Decimal | None = calls.aggregate(cost=Sum("cost_usd"))["cost"]
    return cost or Decimal(0)


def summary(session: Session) -> list[dict[str, Any]]:
    """Every problem with its asks and tries, for the note and for scoring scripts."""
    runs = list(_runs(session).order_by("created_at", "pk"))
    listed = []
    for problem in session.owner_problems.all():
        mine = [run for run in runs if _problem_of(run) == problem.number]
        asks = sorted(
            {
                quote[ASK]
                for run in mine
                for quote in run.quotes
                if quote.get(PROBLEM) == problem.number and isinstance(quote.get(ASK), int)
            }
        )
        listed.append(
            {
                "number": problem.number,
                "description": problem.description,
                "scenes": sorted({run.agent_fix.step.scene.number for run in mine}),
                "asks": len(asks),
                "ask_messages": asks,
                "tries": [
                    {
                        "scene": run.agent_fix.step.scene.number,
                        "instruction": run.instruction,
                        "owner_asked": any(q.get("owner_asked") for q in run.quotes),
                        "clips": run.clips,
                        "ended": _ended(run),
                        "cost_usd": str(_cost(run)),
                    }
                    for run in mine
                ],
            }
        )
    return listed


def note_the_list(session: Session) -> SystemNote | None:
    """With an owner message: the problem list as a system note, if there is one."""
    problems = summary(session)
    if not problems:
        return None
    limit = _limit()
    lines = ["The shop owner's problems so far in this session, as you filed them:"]
    for problem in problems:
        scenes = ", ".join(str(number) for number in problem["scenes"]) or "none yet"
        lines.append(
            f"Problem {problem['number']}: {problem['description']} (scenes {scenes}; "
            f"owner asked {problem['asks']} time{'s' if problem['asks'] != 1 else ''})."
        )
        for number, attempt in enumerate(problem["tries"], start=1):
            who = "owner's note" if attempt["owner_asked"] else "your own idea"
            lines.append(
                f"  Try {number}, scene {attempt['scene']} ({who}): {attempt['instruction']} "
                f"Ended: {attempt['ended']}. Cost ${Decimal(attempt['cost_usd']):.2f}."
            )
        if problem["asks"] >= limit:
            lines.append(
                f"  If the owner's newest message is about problem {problem['number']} again, "
                f"it is ask {problem['asks'] + 1}. Do not repeat what was tried. Either try a "
                "clearly different kind of action, or tell the owner plainly that you tried "
                "and couldn't get it, saying what you tried, and offer the closest thing you "
                "can do. If the message is about something else, carry on normally."
            )
    return SystemNote.objects.create(
        session=session, kind=SystemNote.Kind.PROBLEM_LIST, text="\n".join(lines)
    )
