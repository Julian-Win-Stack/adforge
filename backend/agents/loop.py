"""The agent loop. An agent is a model with tools: it is given its conversation, asks for
tools, is given what they produced, and asks again, until it has nothing left to do and
replies. The same loop runs every agent, and nothing here says which tool comes when.

Nothing is kept in memory between turns. Each turn is rebuilt from the database, from what
was said and the checkpoint of every tool called, so a worker that stops halfway is
replaced by one that runs the loop again and carries on where the checkpoints say."""

import inspect
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, ValidationError

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
    take_turn,
)
from gateway.types import (
    BlockedBySafetyFilter,
    ClipFailed,
    Happened,
    Image,
    Said,
    Shown,
    StepFinished,
    ToolSpec,
    ToolUse,
    UnusableReply,
)
from jobs.models import SceneStep
from jobs.reference_photos import ReferencePhotoUnusable
from jobs.scenes import NotAColourPhoto

from .models import SystemNote, ToolCall

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
)

# Asking again sends the same thing, so the run stops and the shop owner starts afresh.
BLOCKED = (
    "the AI service's safety filter blocked something on this product's page, and this "
    "version of the app can't handle that yet. Please create a new session with a different "
    "product"
)


class RunStopped(Exception):
    """A scene step stopped the run, saying why. The producer takes no turn until the user
    speaks again."""


class Refused(Exception):
    """A tool won't do what it was asked, and has done nothing: it would break one of the
    rules, or what it works on doesn't exist yet. Says why, for the agent."""


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

    def run(self, call: ToolCall) -> str | ToolResult:
        """Do the work and say what happened. What it says is the result the agent is given,
        with any pictures it hands back."""
        raise NotImplementedError

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name, description=inspect.cleandoc(cls.__doc__ or ""), arguments=cls
        )


@dataclass(frozen=True)
class Agent:
    """What runs on the loop: the agent's name, the purpose that picks its model, what it is
    told to do, the tools it may call, and whether it is given code's system notes on the
    fix plans it runs (the producer is: fix redesign, decision 20)."""

    name: str
    purpose: str
    instructions: str
    tools: Sequence[type[Tool]]
    reads_system_notes: bool = False


def run(agent: Agent, session: Session, *, woken_by: Sequence[str] = ()) -> int:
    """Let `agent` work in `session` until it replies. Gives the number of the last message
    its last turn was given, so the caller can tell whether anything was said since.
    `woken_by` names the scene steps whose finishing started it, for its trace."""
    message = session.messages.filter(role=Message.Role.USER).last()
    with tracing.message_work(
        agent.name,
        session_id=str(session.pk),
        message=message.text if message else "",
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
        settle(agent, call)
    while True:
        conversation, read_up_to, steps, notes = _conversation(agent, session)
        if _stopped_since_the_user_spoke(session):
            stopping = [
                step
                for step in steps
                if step.status == SceneStep.Status.STOPPED and step.producer_read_at is None
            ]
            _read(steps, notes)
            if stopping:
                raise RunStopped(
                    f"{stopping[0].reason}. The pictures and why each failed are above"
                )
            # Said once already: a step finishing since doesn't start the run again.
            return read_up_to
        if _nothing_to_answer_since_its_last_turn(agent, session, conversation):
            # Its last turn was given all of them, before a worker stopped past its reply.
            _read(steps, notes)
            return read_up_to
        # The turn is traced with the tools it asked for inside it.
        with tracing.agent_turn(agent.name):
            turn = take_turn(
                session=session,
                purpose=agent.purpose,
                instructions=agent.instructions,
                conversation=conversation,
                tools=[tool.spec() for tool in agent.tools],
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
                        arguments=asked.arguments,
                    )
                    for asked in turn.calls
                ]
            if not calls:
                return read_up_to
            for call in calls:
                if _stopped_since_the_user_spoke(session):
                    _refuse_as_stopped(call)
                else:
                    settle(agent, call)


def _refuse_as_stopped(call: ToolCall) -> None:
    """Keep a call the agent asked for before a scene step stopped the run as refused,
    without running it, so nothing is paid for once the run has stopped."""
    call.result = "Refused: a scene step stopped the run. Nothing was done."
    call.finished_at = timezone.now()
    call.save(update_fields=["result", "finished_at"])


def settle(agent: Agent, call: ToolCall) -> None:
    """Run the tool a checkpoint asked for, unless it is past the limit, and keep what it
    handed back. A call the safety filter blocked is kept as failed, then stops the run."""
    blocked: BlockedBySafetyFilter | None = None
    with tracing.tool(call.tool, call.arguments) as traced:
        # Kept before the tool runs, for the scene steps it starts to find. A tool run again
        # after a restart is traced again, and the steps it starts then go inside that run.
        call.trace_id, call.observation_id = traced.ids()
        call.save(update_fields=["trace_id", "observation_id"])
        number = _calls_since_the_user_spoke(call.session, up_to=call)
        if number > _limit():
            call.result = (
                f"Refused: this would be tool call {number} since the shop owner's last "
                f"message, and the limit is {_limit()}. Nothing was done. Tell the shop owner "
                "plainly that you hit the limit of work for one message, what is done so far, "
                "and that sending a message lets you carry on."
            )
        else:
            try:
                result = _run(agent, call)
                if isinstance(result, ToolResult):
                    call.result = result.text
                    call.pictures = [
                        {"label": picture.label, "key": picture.key} for picture in result.pictures
                    ]
                else:
                    call.result = result
            except BlockedBySafetyFilter as error:
                blocked = error
                call.result = f"Failed: {why_it_failed(error, 'the tool')}"
        tracing.handed_back(traced, call.result, **_level(call.result))
    call.finished_at = timezone.now()
    call.save(update_fields=["result", "pictures", "asked_about", "finished_at"])
    if blocked is not None:
        raise blocked


def _level(result: str) -> dict[str, str]:
    """How a tool's result is marked in its trace, so a refusal or failure stands out."""
    if result.startswith("Failed:"):
        return {"level": "ERROR"}
    if result.startswith("Refused:"):
        return {"level": "WARNING"}
    return {}


def _run(agent: Agent, call: ToolCall) -> str | ToolResult:
    """What the tool hands back: what it did, or why it refused or failed. A tool that fails
    hands the reason back, so the agent can tell the user, or try something else. A picture
    no model can read fails the tool here, rather than the agent's next turn."""
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
        return result
    except Refused as refused:
        return f"Refused: {refused} Nothing was done."
    except BlockedBySafetyFilter:
        raise  # It stops the whole run, once the call is kept as failed.
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("The %s tool failed for checkpoint %s", call.tool, call.pk)
        return f"Failed: {why_it_failed(error, 'the tool')}"


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
        return f"{BLOCKED}."
    if isinstance(error, ClipFailed):
        return f"the video model couldn't make the clip ({error})."
    if isinstance(error, ReferencePhotoUnusable):
        return f"{error}"
    return f"an unexpected error stopped {stopped}. The details are in the server log."


def why_it_failed_plainly(error: Exception) -> str:
    """Why work failed, in plain words for the shop owner, from what it raised."""
    if isinstance(error, UnusableReply):
        if _picked_a_photo_not_in_the_colour(error):
            return "the model picked a photo that doesn't show the product in the ad's colour."
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
    return "something went wrong on our side."


def _picked_a_photo_not_in_the_colour(error: UnusableReply) -> bool:
    """Whether the answer couldn't be used because the photo picked isn't in the ad's colour."""
    invalid = error.__cause__
    return isinstance(invalid, ValidationError) and any(
        isinstance(each.get("ctx", {}).get("error"), NotAColourPhoto) for each in invalid.errors()
    )


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
    return (
        conversation[: len(given)] == given
        and bool(gained)
        and all(isinstance(each, Said) and each.by == "agent" for each in gained)
    )


def over_the_limit(session: Session) -> bool:
    """Whether the agents have called every tool they may for the user's last message."""
    return _calls_since_the_user_spoke(session) >= _limit()


def _limit() -> int:
    """How many tools every agent together may call for one message from the user."""
    limit: int = settings.MAX_TOOL_CALLS_PER_MESSAGE
    return limit


def _calls_since_the_user_spoke(session: Session, *, up_to: ToolCall | None = None) -> int:
    """How many tools every agent has called since the user's last message: all of them, or
    those up to and including `up_to`."""
    calls = session.tool_calls.all()
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

    A system note comes where the agent was first given it, as a step does. The fix plan
    steps code runs are never the agent's, so it is told of them only by these notes."""
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


def _stopped_since_the_user_spoke(session: Session) -> bool:
    """Whether a scene step stopped the run since the user's last message."""
    stopped = SceneStep.objects.filter(tool_call__session=session, status=SceneStep.Status.STOPPED)
    spoke = session.messages.filter(role=Message.Role.USER).order_by("seq").last()
    if spoke is not None:
        stopped = stopped.filter(finished_at__gt=spoke.created_at)
    return stopped.exists()


def unread_steps(agent: Agent, session: Session) -> QuerySet[SceneStep]:
    """The scene steps `agent` started in the session that have finished, and that it
    hasn't been given yet."""
    return _finished_steps(agent, session).filter(producer_read_at__isnull=True)


def _finished_steps(agent: Agent, session: Session) -> QuerySet[SceneStep]:
    return SceneStep.objects.filter(
        tool_call__session=session, tool_call__agent=agent.name, finished_at__isnull=False
    )


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
    if isinstance(happened, ToolCall):
        return ToolUse(
            call_id=happened.call_id,
            tool=happened.tool,
            arguments=happened.arguments,
            result=happened.result,
            pictures=[Shown(**picture) for picture in happened.pictures],
        )
    return Said(
        by="user" if happened.role == Message.Role.USER else "agent",
        text=messages.as_read(happened, numbered=True),
    )
