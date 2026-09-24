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
from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, ValidationError

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message, Session
from gateway.gateway import charged_to, take_turn
from gateway.models import ModelCall
from gateway.types import Said, ToolSpec, ToolUse, UnusableReply

from .models import ToolCall

logger = logging.getLogger(__name__)


class Refused(Exception):
    """A tool won't do what it was asked, and has done nothing: it would break one of the
    rules, or what it works on doesn't exist yet. Says why, for the agent."""


class Tool(BaseModel):
    """One thing an agent can do. A subclass is one tool: its `name`, a docstring telling
    the model what it does, and fields for the arguments the model fills in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ClassVar[str]

    def run(self, call: ToolCall) -> str:
        """Do the work and say what happened. What it says is the result the agent is given."""
        raise NotImplementedError

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name, description=inspect.cleandoc(cls.__doc__ or ""), arguments=cls
        )


@dataclass(frozen=True)
class Agent:
    """What runs on the loop: the agent's name, the purpose that picks its model, what it is
    told to do, and the tools it may call."""

    name: str
    purpose: str
    instructions: str
    tools: Sequence[type[Tool]]


def run(agent: Agent, session: Session) -> int:
    """Let `agent` work in `session` until it replies. Gives the number of the last message
    its last turn was given, so the caller can tell whether anything was said since."""
    # A tool that was running when the last worker stopped runs again first: the agent
    # can't take another turn until every tool it asked for has handed back a result.
    for call in session.tool_calls.filter(agent=agent.name, finished_at__isnull=True):
        _settle(agent, call)
    while True:
        conversation, read_up_to = _conversation(agent, session)
        if _only_it_spoke_since_its_last_turn(agent, session, conversation):
            return read_up_to
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
            _settle(agent, call)


def _settle(agent: Agent, call: ToolCall) -> None:
    """Run the tool a checkpoint asked for, unless it is past the limit, and keep what it
    handed back."""
    number = _calls_since_the_user_spoke(call.session, up_to=call)
    if number > _limit():
        call.result = (
            f"Refused: this would be tool call {number} since the shop owner's last message, "
            f"and the limit is {_limit()}. Nothing was done. Tell the shop owner plainly that "
            "you hit the limit of work for one message, what is done so far, and that sending "
            "a message lets you carry on."
        )
    else:
        call.result = _run(agent, call)
    call.finished_at = timezone.now()
    call.save(update_fields=["result", "asked_about", "finished_at"])


def _run(agent: Agent, call: ToolCall) -> str:
    """What the tool hands back: what it did, or why it refused or failed. A tool that fails
    hands the reason back, so the agent can tell the user, or try something else."""
    try:
        tool = {tool.name: tool for tool in agent.tools}[call.tool]
        try:
            given = tool.model_validate(call.arguments)
        except ValidationError as invalid:
            raise Refused(
                f"it was called with arguments it can't use ({_what_is_wrong(invalid)})."
            ) from invalid
        with charged_to(call):
            return given.run(call)
    except Refused as refused:
        return f"Refused: {refused} Nothing was done."
    except UnusableReply as error:
        return f"Failed: a model's answer couldn't be used ({error})."
    except OutsideServiceDown as error:
        return f"Failed: an outside service stayed down after several tries ({error})."
    except Exception:
        logger.exception("The %s tool failed for checkpoint %s", call.tool, call.pk)
        return "Failed: an unexpected error stopped the tool. The details are in the server log."


def _what_is_wrong(invalid: ValidationError) -> str:
    """Each argument that can't be used, and why, in one line."""
    wrong = []
    for error in invalid.errors():
        # A tool's own check says why in its own words, without pydantic's "Value error, ".
        why = str(error["ctx"]["error"]) if error["type"] == "value_error" else error["msg"]
        wrong.append(f"{'.'.join(str(part) for part in error['loc'])}: {why}")
    return "; ".join(wrong)


def _only_it_spoke_since_its_last_turn(
    agent: Agent, session: Session, conversation: list[Said | ToolUse]
) -> bool:
    """Whether all the conversation gained since the agent's last turn is what it said: it
    replied, and nothing has come since for it to answer. An agent started again after its
    worker stopped past its reply has nothing to do."""
    last_turn = ModelCall.objects.filter(
        session=session, purpose=agent.purpose, outcome=ModelCall.Outcome.SUCCEEDED
    ).last()
    if last_turn is None:
        return False
    given = last_turn.handoff["conversation"]
    gained = conversation[len(given) :]
    return (
        [each.model_dump(mode="json") for each in conversation[: len(given)]] == given
        and bool(gained)
        and all(isinstance(each, Said) and each.by == "agent" for each in gained)
    )


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


def _conversation(agent: Agent, session: Session) -> tuple[list[Said | ToolUse], int]:
    """Everything said in the session, and every tool the agent called, in the order they
    happened. With it, the number of the last message in it."""
    said = list(session.messages.prefetch_related("attachments"))
    happened: list[Message | ToolCall] = [*said, *session.tool_calls.filter(agent=agent.name)]
    conversation = [_as_given(each) for each in sorted(happened, key=lambda each: each.created_at)]
    return conversation, max((message.seq for message in said), default=0)


def _as_given(happened: Message | ToolCall) -> Said | ToolUse:
    if isinstance(happened, ToolCall):
        return ToolUse(
            call_id=happened.call_id,
            tool=happened.tool,
            arguments=happened.arguments,
            result=happened.result,
        )
    return Said(
        by="user" if happened.role == Message.Role.USER else "agent",
        text=messages.as_read(happened),
    )
