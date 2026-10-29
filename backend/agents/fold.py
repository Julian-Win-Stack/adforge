"""The fold (spec #247 point 15): an agent's conversation is rebuilt from the database before
every turn and holds everything in full, until its last turn's input tokens, as the model
reported them and the turn's record keeps them, pass the fold point, a share of its model's
window. Then the oldest share of the tool calls still given in full fold: each is given as one
line (tool, scene, outcome, cost), kept on its row, and the newest stay whole. Nothing is
deleted: every row keeps its whole answer.

A fold is decided from the stored rows, when the turn that passed the point is kept, and
written with it, so every rebuild of the conversation comes out the same."""

import json
import logging
import math
import re
from decimal import Decimal

from django.conf import settings
from django.db.models import Q

from chat.models import Session
from gateway import catalog
from gateway.models import ModelCall

from .models import ToolCall
from .money import cost_of, dollars

logger = logging.getLogger(__name__)

# How long an outcome, the first sentence of the call's answer, may run in a fold line
# before it is cut.
_OUTCOME_CHARACTERS = 120

_KINDS = {"made": "made", "cant": "can't", "question": "asked a question"}


def fold_point(purpose: str) -> int:
    """The input tokens past which a conversation for `purpose` folds."""
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    share: float = settings.FOLD_PAST_SHARE_OF_WINDOW
    return int(catalog.max_input_tokens(model) * share)


def fold_if_past(session: Session, agent: str, purpose: str) -> list[ToolCall]:
    """Fold the oldest of `agent`'s calls still given in full when its last turn in `session`
    passed the fold point, keeping each one's line on its row. Gives the calls folded."""
    last = (
        ModelCall.objects.filter(
            session=session, purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED
        )
        .order_by("id")
        .last()
    )
    point = fold_point(purpose)
    if last is None or last.input_tokens is None or last.input_tokens <= point:
        return []
    whole = list(
        session.tool_calls.filter(agent=agent, finished_at__isnull=False, fold_line="").order_by(
            "id"
        )
    )
    share: float = settings.FOLD_SHARE_OF_CALLS
    # The oldest share of the calls that are shorter folded: a call with a one-sentence answer
    # given as its line would only grow, so it stays whole, and it never holds back a longer
    # call behind it.
    lines = {each.pk: fold_line(each) for each in whole}
    shrinking = [each for each in whole if _folding_shrinks(each, lines[each.pk])]
    folding = shrinking[: math.ceil(len(shrinking) * share)]
    for each in folding:
        each.fold_line = lines[each.pk]
        each.save(update_fields=["fold_line"])
    if folding:
        logger.info(
            "Session %s: the %s's last turn took %s input tokens, past its fold point of %s, so "
            "its %s oldest tool calls given in full were folded",
            session.pk,
            agent,
            last.input_tokens,
            point,
            len(folding),
        )
    else:
        logger.warning(
            "Session %s: the %s's last turn took %s input tokens, past its fold point of %s, "
            "and none of its oldest tool calls given in full is shorter folded",
            session.pk,
            agent,
            last.input_tokens,
            point,
        )
    return folding


def fold_line(call: ToolCall) -> str:
    """The one line a call stands as once folded: its tool, its scene, its outcome, and what
    its work has cost."""
    scenes = sorted(set(call.scene_steps.values_list("scene__number", flat=True)))
    if not scenes and isinstance(call.arguments.get("scene"), int):
        scenes = [call.arguments["scene"]]
    where = f", scene {', '.join(str(number) for number in scenes)}" if scenes else ""
    kind = _KINDS.get(call.answer, "made")
    # Its first sentence.
    said = re.split(r"(?<=[.!?])\s", " ".join(call.result.split()), maxsplit=1)[0]
    if len(said) > _OUTCOME_CHARACTERS:
        said = said[: _OUTCOME_CHARACTERS - 3].rstrip() + "..."
    return f"{call.tool}{where}: {kind} ({said}); cost {dollars(_cost(call))}."


def _folding_shrinks(call: ToolCall, line: str) -> bool:
    """Whether `call` given as `line` is shorter than given in full: its arguments, its
    answer and any picture it handed back, against its why and the folded line."""
    if call.pictures:
        return True
    given = len(json.dumps(call.arguments)) + len(call.result)
    return len(as_folded(call, line)) < given


def as_folded(call: ToolCall, line: str = "") -> str:
    """What the agent is given of a folded call in place of its whole answer: its line (its
    fold line unless `line` is given), and how to read the answer again."""
    return (
        f"{line or call.fold_line} Folded to one line; read_earlier_call with call id "
        f"{call.call_id} gives its whole answer."
    )


def _cost(call: ToolCall) -> Decimal:
    """What a call's work cost: its own model calls, and the fixing agent's on any step it
    started, each once."""
    return cost_of(Q(tool_call=call) | Q(tool_call__agent_fix__step__tool_call=call))
