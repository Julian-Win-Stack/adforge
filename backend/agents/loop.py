"""The agent loop. An agent is a model with tools: it is given its conversation, asks for
tools, is given what they produced, and asks again, until it has nothing left to do and
replies. The same loop runs every agent, and nothing here says which tool comes when.

Nothing is kept in memory between turns. Each turn is rebuilt from the database, from what
was said and the checkpoint of every tool called, so a worker that stops halfway is
replaced by one that runs the loop again and carries on where the checkpoints say."""

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from django.db import transaction
from pydantic import BaseModel, ConfigDict

from chat import messages
from chat.models import Message, Session
from gateway.gateway import charged_to, take_turn
from gateway.types import Said, ToolSpec, ToolUse

from .models import ToolCall


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


def run(agent: Agent, session: Session) -> None:
    """Let `agent` work in `session` until it replies."""
    # A tool that was running when the last worker stopped runs again first: the agent
    # can't take another turn until every tool it asked for has handed back a result.
    for call in session.tool_calls.filter(agent=agent.name, finished=False):
        _finish(agent, call)
    while True:
        turn = take_turn(
            session=session,
            purpose=agent.purpose,
            instructions=agent.instructions,
            conversation=_conversation(agent, session),
            tools=[tool.spec() for tool in agent.tools],
        )
        # What the agent said and the tools it asked for are written down together, before
        # any tool runs, so a restart finds both or neither.
        with transaction.atomic():
            if turn.says:
                messages.add(session, role=Message.Role.AGENT, text=turn.says)
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
            return
        for call in calls:
            _finish(agent, call)


def _finish(agent: Agent, call: ToolCall) -> None:
    """Run the tool a checkpoint asked for, and keep what it produced."""
    tool = {tool.name: tool for tool in agent.tools}[call.tool]
    with charged_to(call):
        call.result = tool.model_validate(call.arguments).run(call)
    call.finished = True
    call.save(update_fields=["result", "finished"])


def _conversation(agent: Agent, session: Session) -> list[Said | ToolUse]:
    """Everything said in the session, and every tool the agent called, in the order they
    happened."""
    happened: list[Message | ToolCall] = [
        *session.messages.prefetch_related("attachments"),
        *session.tool_calls.filter(agent=agent.name),
    ]
    return [_as_given(each) for each in sorted(happened, key=lambda each: each.created_at)]


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
