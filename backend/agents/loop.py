"""The agent loop. An agent is a model with tools: it is given its conversation, asks for
tools, is given what they produced, and asks again, until it has nothing left to do and
replies. The same loop runs every agent, and nothing here says which tool comes when.

Nothing is kept in memory between turns. Each turn is rebuilt from the database, from what
was said and the checkpoint of every tool called, so a worker that stops halfway is
replaced by one that runs the loop again and carries on where the checkpoints say."""

import inspect
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, ClassVar, Literal

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from adforge import tracing
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message, Session
from gateway.gateway import (
    IMAGE_TYPE_NAMES,
    UnreadableImage,
    charged_to,
    check_readable,
    last_turn_given,
    past_the_safety_filter,
    take_turn,
)
from gateway.models import ModelCall
from gateway.types import (
    BlockedBySafetyFilter,
    ClipFailed,
    Happened,
    Image,
    NotSetUp,
    RefusedForMoney,
    Said,
    Shown,
    StepFinished,
    ToolSpec,
    ToolUse,
    Turn,
    UnusableReply,
)
from jobs.models import Job, SceneStep
from jobs.notices import left_out_by_the_filter, post_notice
from jobs.reference_photos import ReferencePhotoUnusable

from . import fold
from .models import RefusedPicture, SystemNote, ToolCall

logger = logging.getLogger(__name__)


# What outside work is known to fail with, and says why well enough for the agent. Anything
# else is a bug, whose details go to the server log.
EXPECTED_FAILURES = (
    UnusableReply,
    OutsideServiceDown,
    UnreadableImage,
    ClipFailed,
    BlockedBySafetyFilter,
    ReferencePhotoUnusable,
    NotSetUp,
    RefusedForMoney,
)

# Why a picture refused for money failed: the account it was asked of is out of money, and
# nothing else could draw it: Azure's copy of the picture model isn't set up, or refused for
# money too (Julian 2026-10-29).
OUT_OF_MONEY = (
    "the AI service refused to draw it because our account there is out of money (its spend "
    "limit or quota is reached), and nothing else could draw it instead"
)

# Asking again sends the same thing, so the run stops and the shop owner starts afresh.
BLOCKED = (
    "the AI service's safety filter blocked something on this product's page, and this "
    "version of the app can't handle that yet. Please create a new session with a different "
    "product"
)


class Refused(Exception):
    """A tool won't do what it was asked, and has done nothing: it would break one of the
    rules, or what it works on doesn't exist yet. Says why, for the agent."""


class Failed(Exception):
    """A tool tried what it was asked, and it failed: says why, for the agent. What the tool
    did before it failed, as a notice shown to the shop owner, stays."""


class Asks(Exception):
    """A tool can't go on without one answer, and has done nothing: says the ONE clarifying
    question, for the agent to answer from what it knows and call the tool again (spec #247
    point 6)."""


# How a tool's result begins when it couldn't do what it was asked: refused, having done
# nothing, or failed. Code and tests read them, so they stay.
REFUSED = "Refused:"
FAILED = "Failed:"
# How a tool's result begins when it asks the agent one question back.
QUESTION = "Question:"

type AnswerKind = Literal["made", "cant", "question"]


@dataclass(frozen=True)
class ToolAnswer:
    """What a tool hands back to the agent (spec #247 point 6): the thing it made ("made"),
    "can't, because ..." with the reason ("cant"), or one clarifying question ("question").
    `text` is what the agent reads, as the tool call's row keeps it in its result, and the
    row's `answer` keeps the kind. Every tool's return, refusal and error is made one of
    these by the loop, so nothing a tool does stops the run or is skipped unsaid."""

    kind: AnswerKind
    text: str
    pictures: Sequence[Image] = ()

    @classmethod
    def handed_back(cls, result: str | ToolResult) -> ToolAnswer:
        """What a tool's run handed back when it raised nothing: the thing it made. A tool
        says it can't by raising Refused or Failed, and asks by raising Asks, so the kind
        comes from what the tool did, never from how its words begin."""
        if isinstance(result, ToolResult):
            return cls("made", result.text, result.pictures)
        return cls("made", result)

    @classmethod
    def cant(cls, text: str) -> ToolAnswer:
        """A can't, `text` beginning REFUSED or FAILED and saying why."""
        return cls("cant", text)

    @classmethod
    def question(cls, question: str) -> ToolAnswer:
        return cls(
            "question",
            f"{QUESTION} {question} Nothing was done. Answer it from what you already know and "
            "call the tool again with the answer; ask the shop owner only when nothing you "
            "know answers it.",
        )

    @classmethod
    def of(cls, call: ToolCall) -> ToolAnswer:
        """The answer a finished call handed back, from its row. Raises ValueError for a call
        not finished yet, which has answered nothing."""
        if call.finished_at is None:
            raise ValueError(f"the {call.tool} call hasn't finished, so it has no answer yet")
        # A row finished before answers were kept has no kind: its words say it.
        kind: AnswerKind = call.answer or cls.kind_of(call.result)  # type: ignore[assignment]
        return cls(
            kind,
            call.result,
            [Image(label=each["label"], key=each["key"]) for each in call.pictures],
        )

    @staticmethod
    def kind_of(text: str) -> AnswerKind:
        """The kind of answer a result's words give, by how they begin: only for a row kept
        before its kind was."""
        if text.startswith((REFUSED, FAILED)):
            return "cant"
        if text.startswith(QUESTION):
            return "question"
        return "made"

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "pictures": [{"label": each.label, "key": each.key} for each in self.pictures],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ToolAnswer:
        return cls(
            data["kind"],
            data["text"],
            [Image(label=each["label"], key=each["key"]) for each in data.get("pictures", [])],
        )


@dataclass(frozen=True)
class ToolResult:
    """What a tool hands back when it has pictures to show: its words, and the pictures,
    each with the label code gave it, such as "Scene 2, starting picture, v1 first version".
    The agent is shown them, labelled, in this order, on its next turn."""

    text: str
    pictures: Sequence[Image]


class Tool(BaseModel):
    """One thing an agent can do. A subclass is one tool: its `name`, a docstring telling
    the model what it does, and fields for the arguments the model fills in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ClassVar[str]
    # Whether the agent's turn ends once the tool has done what it was asked, as asking the
    # shop owner a question does: the turn's other calls still run, and then the agent takes
    # no further turn until something new comes (spec #247 point 5).
    ends_the_turn: ClassVar[bool] = False
    # Whether it may run while the shop owner's pause holds (spec #247 point 14): only a tool
    # that spends nothing, such as one that reads what is there or asks the shop owner. Every
    # other tool of an agent that can be paused answers paused, so a new tool spends nothing
    # while paused unless it says it is free.
    runs_while_paused: ClassVar[bool] = False

    def run(self, call: ToolCall) -> str | ToolResult:
        """Do the work and say what happened. What it says is the result the agent is given,
        with any pictures it hands back."""
        raise NotImplementedError

    @classmethod
    def spec(cls, *, asks_why: bool = False) -> ToolSpec:
        """The tool as the agent's model is offered it: with a required `why` among its
        arguments when `asks_why`."""
        return ToolSpec(
            name=cls.name,
            description=inspect.cleandoc(cls.__doc__ or ""),
            arguments=_with_why(cls) if asks_why else cls,
        )


# What the agent is asked to write in a call's `why` (spec #247 point 7).
WHY = (
    "One sentence: why you are making this call now, from what you know so far. It is kept "
    "with the call for the people who review your work; the tool doesn't read it."
)


@cache
def _with_why(tool: type[Tool]) -> type[BaseModel]:
    """`tool`'s arguments with a required `why`, the shape its model fills in. A tool of an
    agent that asks why can't have an argument of its own named so."""
    if "why" in tool.model_fields:
        raise TypeError(f"the {tool.name} tool has its own `why`, which the agent's would hide")
    return create_model(tool.__name__, __base__=tool, why=(str, Field(description=WHY)))


@dataclass(frozen=True)
class Agent:
    """What runs on the loop: the agent's name, the purpose that picks its model, what it is
    told to do, the tools it may call, and whether it is given code's system notes (the
    producer is: a pasted page, the shop owner's pause)."""

    name: str
    purpose: str
    instructions: str
    tools: Sequence[type[Tool]]
    reads_system_notes: bool = False
    # Whether each of its tools takes a required `why`, kept on the call's row (spec #247
    # point 7): the producer's do.
    asks_why: bool = False
    # Whether the shop owner's pause holds it: its tools that aren't free answer paused while
    # it does (`Tool.runs_while_paused`). The producer's are.
    pausable: bool = False
    # Whether its oldest tool calls fold to one line each once its last turn passes the fold
    # point (spec #247 point 15, agents/fold.py): the producer's do.
    folds: bool = False
    # What it is given last on every turn, rebuilt from the database each time: the
    # producer's is the ad as it stands (agents/snapshot.py). None for no snapshot.
    snapshot: Callable[[Session], str] | None = None
    # Whether code holds its turn open as it goes to end it, having left it a system note
    # saying why: the producer's holds it while a photo sent with a scene's note is unused
    # (agents/note_photos.py). None never holds it.
    holds_the_turn: Callable[[Session], bool] | None = None

    @property
    def turn_enders(self) -> set[str]:
        """The names of its tools that end its turn (`Tool.ends_the_turn`)."""
        return {tool.name for tool in self.tools if tool.ends_the_turn}

    def tool_specs(self) -> list[ToolSpec]:
        return [tool.spec(asks_why=self.asks_why) for tool in self.tools]


# What a trace shows for the page panel's message, rather than the whole page pasted.
PASTED_PAGE_IN_TRACE = "[The page panel's pasted page]"


def run(agent: Agent, session: Session, *, woken_by: Sequence[str] = ()) -> int:
    """Let `agent` work in `session` until it replies. Gives the number of the last message
    its last turn was given, so the caller can tell whether anything was said since.
    `woken_by` names the scene steps whose finishing started it, for its trace."""
    message = session.messages.filter(role=Message.Role.USER).last()
    with tracing.message_work(
        agent.name,
        session_id=str(session.pk),
        # A whole pasted page is the page now, not something said: its trace names it.
        message=(PASTED_PAGE_IN_TRACE if message.pasted_page else message.text) if message else "",
        trace_id=message.trace_id if message else "",
        woken_by=woken_by,
    ) as traced:
        # Each later time it works on the message adds to the trace started now.
        if message is not None and not message.trace_id:
            message.trace_id = traced.ids()[0]
            message.save(update_fields=["trace_id"])
        before = session.messages.order_by("seq").values_list("seq", flat=True).last() or 0
        read_up_to = _work(agent, session)
        if traced.recording():
            said = session.messages.filter(role=Message.Role.AGENT, seq__gt=before)
            tracing.said(traced, [text for text in said.values_list("text", flat=True) if text])
        return read_up_to


def _work(agent: Agent, session: Session) -> int:
    # A tool that was running when the last worker stopped runs again first: the agent
    # can't take another turn until every tool it asked for has handed back a result.
    for call in session.tool_calls.filter(agent=agent.name, finished_at__isnull=True):
        settle(agent, call, restarted=True)
    while True:
        conversation, read_up_to, steps, notes = _conversation(agent, session)
        # Built with it, so what it says has the conversation's steps.
        snapshot = agent.snapshot(session) if agent.snapshot else ""
        conversation = without_refused_pictures(session, agent.purpose, conversation)
        if _nothing_to_answer_since_its_last_turn(agent, session, conversation):
            # Its last turn was given all of them, before a worker stopped past its reply.
            _read(steps, notes)
            return read_up_to
        # The turn is traced with the tools it asked for inside it.
        with tracing.agent_turn(agent.name):
            turn = take_a_turn(
                session=session,
                job=session.jobs.first(),
                purpose=agent.purpose,
                instructions=agent.instructions,
                conversation=conversation,
                tools=agent.tool_specs(),
                done="the work on your ad carried on",
                snapshot=snapshot,
            )
            # A tool past the limit is refused, and the agent gets one turn to tell the user.
            # Asking for another tool instead, it is stopped.
            stopped = turn.calls and _calls_since_the_user_spoke(session) > _limit()
            # What the agent said and the tools it asked for are written down together, before
            # any tool runs, so a restart finds both or neither.
            with transaction.atomic():
                # Only the steps and notes the turn was given: one that came while the model
                # worked is given on the next turn.
                _read(steps, notes)
                # Decided from the turn just kept, and kept with it, so a restart rebuilds the
                # same conversation: folded or not, never folded twice for one turn.
                if agent.folds:
                    fold.fold_if_past(session, agent.name, agent.purpose)
                if turn.says:
                    messages.add(session, role=Message.Role.AGENT, text=turn.says)
                if stopped:
                    messages.add(
                        session,
                        role=Message.Role.AGENT,
                        text=f"I hit my limit of {_limit()} steps for one message. Send a message "
                        "and I'll carry on.",
                    )
                    return read_up_to
                calls = [
                    ToolCall.objects.create(
                        session=session,
                        agent=agent.name,
                        tool=asked.tool,
                        call_id=asked.call_id,
                        **_why_apart(agent, asked.arguments),
                    )
                    for asked in turn.calls
                ]
            if not calls:
                if agent.holds_the_turn is not None and agent.holds_the_turn(session):
                    continue
                return read_up_to
            for call in calls:
                # Read before each call, so a pause pressed meanwhile reaches the agent
                # between its calls rather than after the whole turn.
                held = _held_since_the_user_spoke(session)
                if held is not None:
                    _refuse_unrun(call, held)
                else:
                    settle(agent, call, new=True)


def _why_apart(agent: Agent, arguments: dict[str, Any]) -> dict[str, Any]:
    """A call's row fields from the arguments it was asked with: for an agent that asks why,
    the `why` apart from the tool's own arguments. A `why` that isn't words is none."""
    if not agent.asks_why:
        return {"arguments": arguments}
    why = arguments.get("why")
    return {
        "arguments": {key: value for key, value in arguments.items() if key != "why"},
        "why": why.strip() if isinstance(why, str) else "",
    }


def take_a_turn(
    *,
    session: Session,
    job: Job | None,
    purpose: str,
    instructions: str,
    conversation: Sequence[Happened],
    tools: Sequence[ToolSpec],
    done: str,
    since: datetime | None = None,
    snapshot: str = "",
) -> Turn:
    """An agent's turn (`take_turn`), past the safety filter (requirement 22). A model's
    filter refuses a whole turn over one picture in the conversation, so when it refuses, the
    turn is taken again without the pictures it hadn't already looked at in an earlier turn,
    one at a time, newest first, then all together. The agent is told in its tool's result
    which picture is left out and why, every later turn leaves it out too, and the chat says
    so in red, with a warning line: `done` says what went on without it, as "the work on
    your ad carried on". When that is refused too, or no picture is new, one more turn is
    taken without every picture. Raises the filter's BlockedBySafetyFilter when every try is
    refused. A `snapshot` is given after the conversation, as take_turn says."""
    conversation = without_refused_pictures(session, purpose, conversation)
    # A picture shown twice is one picture.
    shown = list(
        dict.fromkeys(
            Image(label=picture.label, key=picture.key)
            for each in conversation
            if isinstance(each, ToolUse)
            for picture in each.pictures
        )
    )

    @cache
    def looked_at() -> set[str]:
        # Only needed once a turn is refused.
        return {
            picture["key"]
            for images in ModelCall.objects.filter(
                session=session, purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED
            ).values_list("images", flat=True)
            for picture in images
        }

    def ask(kept: list[Image]) -> Turn:
        return take_turn(
            session=session,
            purpose=purpose,
            instructions=instructions,
            conversation=_leaving_out(
                conversation, [image for image in shown if image not in kept]
            ),
            tools=tools,
            since=since,
            snapshot=snapshot,
        )

    try:
        turn, left_out = past_the_safety_filter(
            ask, shown, may_leave_out=lambda image: image.key not in looked_at()
        )
    except BlockedBySafetyFilter:
        # A refusal shouldn't end the session over pictures the model looked at before
        # either: one more turn, without every picture, unless that was already tried.
        if not any(image.key in looked_at() for image in shown):
            raise
        turn, left_out = ask([]), shown
    if left_out:
        with transaction.atomic():
            RefusedPicture.objects.bulk_create(
                RefusedPicture(session=session, purpose=purpose, label=image.label, key=image.key)
                for image in left_out
            )
            notice = left_out_by_the_filter([image.label for image in left_out], done)
            if job is not None:
                post_notice(job, notice, Message.Level.PROBLEM)
            else:
                messages.add(
                    session, role=Message.Role.NOTICE, text=notice, level=Message.Level.PROBLEM
                )
    return turn


def without_refused_pictures(
    session: Session, purpose: str, conversation: Sequence[Happened]
) -> list[Happened]:
    """The conversation without the pictures the agent's model refused to look at in an
    earlier turn, each tool's result saying which of its pictures is left out."""
    refused = [
        Image(label=label, key=key)
        for label, key in RefusedPicture.objects.filter(
            session=session, purpose=purpose
        ).values_list("label", "key")
    ]
    return _leaving_out(conversation, refused)


def _leaving_out(conversation: Sequence[Happened], left_out: Sequence[Image]) -> list[Happened]:
    """The conversation with the pictures `left_out` taken out of each tool's result, which
    says so in one line. A result with none of them is left as it is."""
    gone = {(image.label, image.key) for image in left_out}
    kept: list[Happened] = []
    for each in conversation:
        if isinstance(each, ToolUse) and any(
            (picture.label, picture.key) in gone for picture in each.pictures
        ):
            dropped = [
                picture.label for picture in each.pictures if (picture.label, picture.key) in gone
            ]
            each = each.model_copy(
                update={
                    "pictures": [
                        picture
                        for picture in each.pictures
                        if (picture.label, picture.key) not in gone
                    ],
                    "result": f"{each.result}\n{not_shown(dropped)}",
                }
            )
        kept.append(each)
    return kept


def not_shown(labels: Sequence[str]) -> str:
    """What an agent is told of the pictures a tool gave that its model's safety filter
    refused to look at, in one line."""
    one = len(labels) == 1
    named = labels[0] if one else f"{', '.join(labels[:-1])} and {labels[-1]}"
    it, isnt = ("it", "isn't") if one else ("them", "aren't")
    return (
        f"{named} {isnt} shown: the AI model's safety filter refused to look at {it}. Carry "
        f"on without {it}."
    )


def _refuse_unrun(call: ToolCall, why: str) -> None:
    """Keep a call the agent asked for before its run was held as refused for `why`, without
    running it, so nothing is paid for once the run has stopped or the shop owner paused."""
    call.result = f"{REFUSED} {why} Nothing was done."
    call.answer = "cant"
    call.finished_at = timezone.now()
    call.save(update_fields=["result", "answer", "finished_at"])


def settle(
    agent: Agent,
    call: ToolCall,
    *,
    counted: bool = True,
    new: bool = False,
    restarted: bool = False,
) -> None:
    """Run the tool a checkpoint asked for, unless it is past the limit, and keep what it
    handed back, as a ToolAnswer: whatever the tool does, refuses or raises, the agent is
    answered and the run goes on. A call not `counted` against the limit is code's work, as
    the fixing agent's is, not an answer to the shop owner's message. A `new` call, just
    asked for, of an agent that asks why is refused when it has no `why`, once per tool in
    the session; one `restarted`, left unfinished by a worker that stopped, is run as it was
    asked. Either one run with no `why` is said in red."""
    with tracing.tool(call.tool, call.arguments, why=call.why) as traced:
        # Kept before the tool runs, for the scene steps it starts to find. A tool run again
        # after a restart is traced again, and the steps it starts then go inside that run.
        call.trace_id, call.observation_id = traced.ids()
        call.save(update_fields=["trace_id", "observation_id"])
        number = _calls_since_the_user_spoke(call.session, up_to=call)
        if counted and number > _limit():
            answer = ToolAnswer.cant(
                f"{REFUSED} this would be tool call {number} since the shop owner's last "
                f"message, and the limit is {_limit()}. Nothing was done. Tell the shop owner "
                "plainly that you hit the limit of work for one message, what is done so far, "
                "and that sending a message lets you carry on."
            )
        elif new and agent.asks_why and not call.why and not _asked_why_before(agent, call):
            answer = ToolAnswer.cant(f"{NO_WHY} Nothing was done.")
        elif _held_by_the_pause(agent, call):
            answer = ToolAnswer.cant(f"{REFUSED} {PAUSED} Nothing was done.")
        else:
            answer = _run(agent, call)
            # Said once it ran, so a tool that starts the ad's job keeps the line with it.
            if (new or restarted) and agent.asks_why and not call.why:
                _say_it_ran_without_a_why(call)
        call.result = answer.text
        call.answer = answer.kind
        call.pictures = [
            {"label": picture.label, "key": picture.key} for picture in answer.pictures
        ]
        tracing.handed_back(traced, call.result, **_level(call.result))
    call.finished_at = timezone.now()
    call.save(update_fields=["result", "answer", "pictures", "asked_about", "finished_at"])


# What an agent that asks why is told of a call made without one. Said once per tool: the
# same call made again without one runs, so a missing reason never stops the work.
NO_WHY = (
    f"{REFUSED} the call has no `why`. Every call takes one sentence saying why you are "
    "making it now: call the tool again with it."
)


def _asked_why_before(agent: Agent, call: ToolCall) -> bool:
    """Whether a call of the same tool before `call` in the session was refused for having
    no `why`: the agent was told once, and isn't refused for it again."""
    return call.session.tool_calls.filter(
        agent=agent.name, tool=call.tool, id__lt=call.pk, result__startswith=NO_WHY
    ).exists()


def _say_it_ran_without_a_why(call: ToolCall) -> None:
    """Tell the chat in red, with a warning line, that a call ran with no reason kept for it,
    after the agent was told once."""
    text = (
        f"The {call.agent} made its {call.tool} call again without saying why, so it ran with "
        "no reason kept for it."
    )
    logger.warning("Session %s: %s", call.session_id, text)
    job = call.session.jobs.first()
    if job is not None:
        post_notice(job, text, Message.Level.PROBLEM)
    else:
        messages.add(call.session, role=Message.Role.NOTICE, text=text, level=Message.Level.PROBLEM)


def _level(result: str) -> dict[str, str]:
    """How a tool's result is marked in its trace, so a refusal or failure stands out."""
    if result.startswith(FAILED):
        return {"level": "ERROR"}
    if result.startswith(REFUSED):
        return {"level": "WARNING"}
    return {}


def _run(agent: Agent, call: ToolCall) -> ToolAnswer:
    """What the tool answers: what it made, why it refused or failed, or its one question.
    A tool that fails hands the reason back, so the agent can tell the user, or try something
    else: nothing it raises stops the run. A picture no model can read fails the tool here,
    rather than the agent's next turn."""
    try:
        tool = {tool.name: tool for tool in agent.tools}[call.tool]
        try:
            given = tool.model_validate(call.arguments)
        except ValidationError as invalid:
            raise Refused(
                f"it was called with arguments it can't use ({_what_is_wrong(invalid)})."
            ) from invalid
        with charged_to(call):
            result = given.run(call)
        if isinstance(result, ToolResult):
            for picture in result.pictures:
                if not (picture.label and picture.key):
                    raise ValueError(f"the {call.tool} tool gave a picture with no label or key")
                check_readable(picture)
        return ToolAnswer.handed_back(result)
    except Refused as refused:
        return ToolAnswer.cant(f"{REFUSED} {refused} Nothing was done.")
    except Failed as failed:
        return ToolAnswer.cant(f"{FAILED} {failed}")
    except Asks as asks:
        return ToolAnswer.question(str(asks))
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("The %s tool failed for checkpoint %s", call.tool, call.pk)
        return ToolAnswer.cant(f"{FAILED} {why_it_failed(error, 'the tool')}")


def why_it_failed(error: Exception, stopped: str) -> str:
    """Why work failed, for the agent, from what it raised. `stopped` names the work, such
    as "the tool", for an error nobody expected, whose details are only in the server log."""
    if isinstance(error, UnusableReply):
        return f"a model's answer couldn't be used ({error})."
    if isinstance(error, OutsideServiceDown):
        return f"an outside service stayed down after several tries ({error})."
    if isinstance(error, UnreadableImage):
        return f"{error}. Only {IMAGE_TYPE_NAMES} pictures can be shown to a model."
    if isinstance(error, BlockedBySafetyFilter):
        return (
            f"the AI service's safety filter refused what it was sent ({error}). The same "
            "request is refused again, so change what is asked, or go on without it."
        )
    if isinstance(error, ClipFailed):
        return f"the video model couldn't make the clip ({error})."
    if isinstance(error, ReferencePhotoUnusable):
        return f"{error}"
    if isinstance(error, NotSetUp):
        return f"the app isn't set up for it ({error}). Trying again won't help."
    if isinstance(error, RefusedForMoney):
        return f"{OUT_OF_MONEY} ({error}). Trying again won't help."
    return f"an unexpected error stopped {stopped}. The details are in the server log."


def why_it_failed_plainly(error: Exception) -> str:
    """Why work failed, in plain words for the shop owner, from what it raised."""
    if isinstance(error, UnusableReply):
        return "a model gave an answer we couldn't use."
    if isinstance(error, OutsideServiceDown):
        return "an outside service stayed down."
    if isinstance(error, ClipFailed):
        return "the video model couldn't make it."
    if isinstance(error, UnreadableImage):
        return f"a picture was in a kind models can't read: only {IMAGE_TYPE_NAMES}."
    if isinstance(error, BlockedBySafetyFilter):
        return "the AI service's safety filter blocked it."
    if isinstance(error, ReferencePhotoUnusable):
        return (
            f"your {error.thing} couldn't be drawn from your photo. Please send another photo "
            "of it."
        )
    if isinstance(error, NotSetUp):
        return f"the app isn't set up for it: {error}."
    if isinstance(error, RefusedForMoney):
        return f"{OUT_OF_MONEY}."
    return "something went wrong on our side."


def _what_is_wrong(invalid: ValidationError) -> str:
    """Each argument that can't be used, and why, in one line."""
    wrong = []
    for error in invalid.errors():
        # A tool's own check says why in its own words, without pydantic's "Value error, ".
        why = str(error["ctx"]["error"]) if error["type"] == "value_error" else error["msg"]
        wrong.append(f"{'.'.join(str(part) for part in error['loc'])}: {why}")
    return "; ".join(wrong)


def _nothing_to_answer_since_its_last_turn(
    agent: Agent, session: Session, conversation: list[Happened]
) -> bool:
    """Whether all the conversation gained since the agent's last turn is what it said: it
    replied, and nothing has come since for it to answer. An agent started again after its
    worker stopped past its reply has nothing to do."""
    given = last_turn_given(session, agent.purpose)
    if given is None:
        return False
    gained = conversation[len(given) :]
    the_same = len(conversation) >= len(given) and all(
        _the_same(now, then) for now, then in zip(conversation, given, strict=False)
    )
    if not the_same or not gained:
        return False
    if all(isinstance(each, Said) and each.by == "agent" for each in gained):
        return True
    # A turn that ended with a call, as asking the shop owner, waits as a reply does.
    return all(
        (isinstance(each, Said) and each.by == "agent") or isinstance(each, ToolUse)
        for each in gained
    ) and any(
        isinstance(each, ToolUse)
        and each.tool in agent.turn_enders
        and ToolAnswer.kind_of(each.result) == "made"
        for each in gained
    )


def _the_same(now: Happened, then: Happened) -> bool:
    """Whether `now` is what `then` was in an earlier turn's conversation. A tool call is the
    same call, folded since or not: a fold changes how it is given, not what happened."""
    if isinstance(now, ToolUse) and isinstance(then, ToolUse):
        return (now.call_id, now.tool) == (then.call_id, then.tool)
    return now == then


def over_the_limit(session: Session) -> bool:
    """Whether the agents have called every tool they may for the user's last message."""
    return _calls_since_the_user_spoke(session) >= _limit()


def _limit() -> int:
    """How many tools every agent together may call for one message from the user."""
    limit: int = settings.MAX_TOOL_CALLS_PER_MESSAGE
    return limit


def _calls_since_the_user_spoke(session: Session, *, up_to: ToolCall | None = None) -> int:
    """How many tools every agent has called since the user's last message: all of them, or
    those up to and including `up_to`. The fixing agent's calls are code's work on a scene,
    not an answer to the message, so they never count."""
    calls = session.tool_calls.filter(agent_fix__isnull=True)
    if up_to is not None:
        calls = calls.filter(id__lte=up_to.pk)
    spoke = session.messages.filter(role=Message.Role.USER)
    if up_to is not None:
        spoke = spoke.filter(created_at__lt=up_to.created_at)
    last = spoke.order_by("created_at").last()
    if last is not None:
        calls = calls.filter(created_at__gt=last.created_at)
    return calls.count()


def _conversation(
    agent: Agent, session: Session
) -> tuple[list[Happened], int, list[SceneStep], list[SystemNote]]:
    """Everything said in the session, every tool the agent called, every scene step its
    tools started that has finished, and, for an agent that reads them, every system note,
    in the order they happened. With it, the number of the last message in it, and the
    scene steps and system notes in it.

    A step comes where the agent was first given it, not when it finished, and one not
    given yet comes last. A step that finished while a turn was being taken is given on the
    next, so it never lands behind a reply that hadn't seen it.

    A message with no words from the agent's side is left out: the app posts one to show
    the shop owner a file, and the tool or step that made the file already tells the agent
    so. Given as the agent's own "[Attached 1 picture]", it was copied as a reply with no
    picture on it. A notice is left out too: it is code telling the shop owner about a
    fallback, which the tool's result already told the agent, and given as the agent's own
    words it would read as something it said. So is the page panel's message: code keeps
    its pasted page as the page, and a system note tells the agent so.

    A system note comes where the agent was first given it, as a step does."""
    said = list(session.messages.prefetch_related("attachments"))
    finished = _finished_steps(agent, session).order_by("finished_at", "id")
    read = list(finished.filter(producer_read_at__isnull=False))
    unread = list(finished.filter(producer_read_at__isnull=True))
    notes = list(session.system_notes.all()) if agent.reads_system_notes else []
    happened: list[tuple[datetime, Message | ToolCall | SceneStep | SystemNote]] = [
        *(
            (message.created_at, message)
            for message in said
            if message.role != Message.Role.NOTICE
            and not message.pasted_page
            and (message.text or message.role == Message.Role.USER)
        ),
        *((call.created_at, call) for call in session.tool_calls.filter(agent=agent.name)),
        *((step.producer_read_at, step) for step in read if step.producer_read_at),
        *((note.read_at, note) for note in notes if note.read_at),
    ]
    in_order: list[Message | ToolCall | SceneStep | SystemNote] = [
        *(each for _, each in sorted(happened, key=lambda each: each[0])),
        *unread,
        *(note for note in notes if note.read_at is None),
    ]
    conversation = [_as_given(each) for each in in_order]
    return conversation, max((message.seq for message in said), default=0), read + unread, notes


# What a tool that spends answers while the shop owner's pause holds (spec #247 point 14),
# and every call left of a turn asked for before the agent was told of the pause: "starts
# nothing" is the tools' rule, not only the agent's instructions.
PAUSED = (
    "paused. The shop owner pressed pause, so nothing that costs money is started until they "
    "write again. Call it again after their message if it is still needed."
)


def paused(session: Session) -> bool:
    """Whether the shop owner's pause holds in `session`, as the database has it now."""
    return Session.objects.filter(pk=session.pk, paused_at__isnull=False).exists()


def _held_by_the_pause(agent: Agent, call: ToolCall) -> bool:
    """Whether the shop owner's pause holds `call`: the agent can be paused, the tool isn't
    one that runs while paused, and the session is paused."""
    if not agent.pausable:
        return False
    tool = {tool.name: tool for tool in agent.tools}.get(call.tool)
    # A tool it hasn't got is refused by the run, saying so.
    return tool is not None and not tool.runs_while_paused and paused(call.session)


def _held_since_the_user_spoke(session: Session) -> str | None:
    """Why the calls left of the agent's turn are refused unrun, or None to run them: the
    shop owner paused and the agent hasn't been given the pause's note yet, so it reads it
    before anything more runs."""
    if paused(session) and unread_pause_notes(session).exists():
        return PAUSED
    return None


def unread_pause_notes(session: Session) -> QuerySet[SystemNote]:
    """The notes of the shop owner's pause the producer hasn't been given yet."""
    return session.system_notes.filter(kind=SystemNote.Kind.PAUSED, read_at__isnull=True)


def unread_steps(agent: Agent, session: Session) -> QuerySet[SceneStep]:
    """The scene steps `agent` started in the session that have finished, and that it
    hasn't been given yet."""
    return _finished_steps(agent, session).filter(producer_read_at__isnull=True)


def _finished_steps(agent: Agent, session: Session) -> QuerySet[SceneStep]:
    """The scene steps `agent` started in the session that have ended, but those the shop
    owner's pause cancelled: the pause's note tells it of them."""
    return SceneStep.objects.filter(
        tool_call__session=session, tool_call__agent=agent.name, finished_at__isnull=False
    ).exclude(status=SceneStep.Status.CANCELLED)


def _read(steps: list[SceneStep], notes: list[SystemNote]) -> None:
    """Mark these scene steps' results as given to the agent that started them, and these
    system notes as given to the producer."""
    now = timezone.now()
    SceneStep.objects.filter(
        pk__in=[step.pk for step in steps], producer_read_at__isnull=True
    ).update(producer_read_at=now)
    SystemNote.objects.filter(pk__in=[note.pk for note in notes], read_at__isnull=True).update(
        read_at=now
    )


# How a system note begins, so the agent reads it as the app's and not the shop owner's.
SYSTEM_NOTE = "From the app, not the shop owner:"


def _as_given(happened: Message | ToolCall | SceneStep | SystemNote) -> Happened:
    if isinstance(happened, SceneStep):
        return StepFinished(text=happened.result)
    if isinstance(happened, SystemNote):
        return StepFinished(text=f"{SYSTEM_NOTE} {happened.text}")
    if isinstance(happened, ToolCall) and happened.fold_line:
        # As one line: its tool, scene, outcome and cost, and the call's why, as it was made.
        return ToolUse(
            call_id=happened.call_id,
            tool=happened.tool,
            arguments={"why": happened.why} if happened.why else {},
            result=fold.as_folded(happened),
        )
    if isinstance(happened, ToolCall):
        return ToolUse(
            call_id=happened.call_id,
            tool=happened.tool,
            # As the agent made the call: its why, kept apart on the row, too.
            arguments={**happened.arguments, "why": happened.why}
            if happened.why
            else happened.arguments,
            result=happened.result,
            pictures=[Shown(**picture) for picture in happened.pictures],
        )
    return Said(
        by="user" if happened.role == Message.Role.USER else "agent",
        text=messages.as_read(happened, numbered=True),
    )
