"""The record of an agent's tool calls and model calls, as the evals keep them: one shape for
the evals' case folders and the round export's per-ad tool_calls.json (spec #247
point 17), so the per-ad "what the agent did" page reads either.

A tool call's record: the agent and the tool, its `why`, the instruction it was given (its own
arguments), the words the agent said in the turn that made it, its answer (made / cant /
question, agents.loop.ToolAnswer) and the result in words, refused or not, its fold line, the
pictures it handed back (label and file), what its model calls cost, when it started and
finished, and its model calls in short, the look's answer in words among them.
"""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from adforge import file_store
from agents import look
from agents.loop import FAILED, REFUSED, ToolAnswer
from agents.models import ToolCall
from chat.models import Session
from gateway.models import ModelCall

# The checkpoint an agent's own turns are charged to (agents.fixing_agent.TURNS): no tool the
# agent called, so no tool call of the record.
TURNS = "take_turn"
# The gateway purpose of the producer's look (spec #247 point 2): its answer is words.
LOOK = look.PURPOSE


class Pictures:
    """Copies of the pictures an agent was shown, named in the order it saw them, in `folder`
    (whose name starts each file's path in the record)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.kept: dict[tuple[str, str], str] = {}

    def keep(self, label: str, key: str) -> str:
        if (label, key) not in self.kept:
            self.folder.mkdir(parents=True, exist_ok=True)
            name = f"{len(self.kept) + 1:03d}-{slug(label)}{Path(key).suffix or '.png'}"
            (self.folder / name).write_bytes(file_store.read(key))
            self.kept[(label, key)] = f"{self.folder.name}/{name}"
        return self.kept[(label, key)]


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:80]


def session_calls(session: Session) -> list[ToolCall]:
    """Every tool call any agent made in the session, in call order; the rows agents' own
    turns are charged to are left out."""
    return list(session.tool_calls.exclude(tool=TURNS).order_by("created_at", "id"))


def said_before(session: Session) -> dict[str, str]:
    """The words an agent said in the turn that made each tool call, by the call's id: each
    turn's model call records what it said and the calls it made."""
    said: dict[str, str] = {}
    outputs = (
        ModelCall.objects.filter(
            session=session, outcome=ModelCall.Outcome.SUCCEEDED, output__has_key="calls"
        )
        .order_by("created_at", "id")
        .values_list("output", flat=True)
    )
    for output in outputs:
        assert output is not None  # it has the key "calls"
        for call in output.get("calls") or []:
            said[call["call_id"]] = output.get("says") or ""
    return said


def tool_call(call: ToolCall, pictures: Pictures, said: str = "") -> dict[str, Any]:
    """One tool call's record; `said` is what the agent said in the turn that made it."""
    model_calls = list(call.model_calls.order_by("created_at", "id"))
    return {
        "agent": call.agent,
        "tool": call.tool,
        "why": call.why,
        "arguments": call.arguments,
        "said": said,
        "answer": (call.answer or ToolAnswer.kind_of(call.result)) if call.finished else "",
        "result": call.result,
        "refused": call.result.startswith((REFUSED, FAILED)),
        "fold_line": call.fold_line,
        "pictures": [
            {"label": shown["label"], "file": pictures.keep(shown["label"], shown["key"])}
            for shown in call.pictures
        ],
        # ToolCall.cost_usd(), summed from the model calls already loaded (no second query).
        "cost_usd": str(sum((c.cost_usd or Decimal(0) for c in model_calls), Decimal(0))),
        "created_at": call.created_at.isoformat(),
        "finished_at": call.finished_at.isoformat() if call.finished_at else None,
        "model_calls": [
            {
                "purpose": each.purpose,
                "model": each.model,
                "outcome": each.outcome,
                "cost_usd": str(each.cost_usd) if each.cost_usd is not None else None,
                "words": words(each.output) if each.purpose == LOOK else "",
            }
            for each in model_calls
        ],
    }


def words(output: Any) -> str:
    """A model's answer in words: its text, or each of its fields as "field: text"."""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        return "\n".join(
            f"{key.replace('_', ' ').capitalize()}: {value}"
            if isinstance(value, str)
            else f"{key.replace('_', ' ').capitalize()}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in output.items()
            if value not in ("", None, [], {})
        )
    return "" if output is None else json.dumps(output, ensure_ascii=False)


def model_call(call: ModelCall) -> dict[str, Any]:
    """One model call's record: what it was given and answered, tokens, cost and time."""
    return {
        "model": call.model,
        "attempt": call.attempt,
        "handoff": call.handoff,
        "pictures_shown": [image["label"] for image in call.images],
        "output": call.output,
        "outcome": call.outcome,
        "error": call.error,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cost_usd": str(call.cost_usd) if call.cost_usd is not None else None,
        "created_at": call.created_at.isoformat(),
    }
