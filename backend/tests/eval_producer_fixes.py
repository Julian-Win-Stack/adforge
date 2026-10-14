"""Producer eval for fixing a scene (#137): does a real model, given the producer's
instructions, pick the step that fixes an owner's note, and leave out what it can't do?

Not part of the test suite (pytest collects only test_*.py). Each case builds a finished ad
with fake providers, as the tests do, then hands the producer's turns after the owner's note
to a real model, up to MOST_TURNS. Its tools run for real against the fakes, so nothing is
paid but the model's turns (pennies on luna). A case passes when restate_fix records exactly
the expected step per scene, nothing is made before the owner's yes, and the producer
replies. Each run's calls and reply are written to EVAL_OUT (a temp file by default), one
JSON line each, for reading the replies.

    EVAL_PRODUCER_MODEL=gpt-6-luna EVAL_RUNS=3 EVAL_OUT=/tmp/luna.jsonl \\
        uv run pytest -q tests/eval_producer_fixes.py
"""

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel
from gateway.openai_adapter import OpenAIProvider
from gateway.types import Turn, TurnReply, TurnRequest
from jobs.models import Fix, ProducedItem

from .conftest import HeldSteps
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_fixes import (
    FIX_2,
    NOTE_1,
    NOTE_2,
    Say,
    clip_with,
    owner_says,
    restating,
    shown,  # noqa: F401 (a fixture)
)

MODEL = os.environ.get("EVAL_PRODUCER_MODEL", "")
RUNS = int(os.environ.get("EVAL_RUNS", "1"))
OUT = Path(os.environ.get("EVAL_OUT") or Path(tempfile.gettempdir()) / "eval-producer-fixes.jsonl")
MOST_TURNS = 4
REDOS = {"make_clip", "make_starting_picture"}

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not MODEL, reason="set EVAL_PRODUCER_MODEL to run the producer eval"),
]


@dataclass(frozen=True)
class Case:
    """An owner's note on the shown ad (scenes 1 and 3: the person talking about the mug;
    scene 2: the mug on its own), and the step restate_fix should record per scene. An empty
    `expect` is a can't: nothing is restated, and the reply says why."""

    id: str
    note: str
    expect: dict[int, str] = field(default_factory=dict)
    # Scene 2's earlier fix is confirmed and its clip still being made.
    running: bool = False


CASES = [
    Case("movement", NOTE_2, {2: "clip"}),
    Case(
        "wrong side",
        "In scene 2 we see the back of the mug. The side with the glaze drip should face us.",
        {2: "picture"},
    ),
    Case(
        "impossible object",
        "The mug in scene 2 has two handles. Ours only has one.",
        {2: "picture"},
    ),
    Case("zoom in", "Can you zoom in on the mug in scene 2?", {2: "picture"}),
    Case("words", "In the last scene she should say it's 20% off this week.", {}),
    Case("talking-scene action", "In the first scene she should wave at the camera.", {}),
    Case("talking-scene hold", NOTE_1, {1: "picture"}),
    Case("two scenes at once", f"{NOTE_1} {NOTE_2}", {1: "picture", 2: "clip"}),
    Case(
        "note while a redo runs",
        "Actually, in scene 2 the tea should pour in a thin stream.",
        {2: "clip"},
        running=True,
    ),
    Case("slower", "Scene 2 is too fast, make it slower.", {}),
    Case("music", "Can the music be more upbeat?", {}),
]


@pytest.mark.parametrize("run", range(RUNS))
@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_the_producer_restates_the_step_that_fixes_the_note(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
    case: Case,
    run: int,
) -> None:
    if case.running:
        owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))
        owner_says(fake_model, say, "Yes, do that.", clip_with(2, FIX_2))
        assert len(steps.held) == 1
    since_call = ToolCall.objects.order_by("pk").last()
    since_message = Message.objects.order_by("seq").last()
    fixes_before = Fix.objects.count()
    real = RealTurns(fake_model)
    monkeypatch.setattr(fake_model, "take_turn", real.take_turn)

    say(case.note)

    calls = ToolCall.objects.filter(pk__gt=since_call.pk if since_call else 0).order_by("pk")
    made = [(call.tool, call.arguments) for call in calls]
    restated = {fix.scene.number: fix.redo for fix in Fix.objects.order_by("pk")[fixes_before:]}
    replies = Message.objects.filter(
        role=Message.Role.AGENT, seq__gt=since_message.seq if since_message else 0
    ).exclude(text="")
    redone = [tool for tool, _ in made if tool in REDOS]
    reply = "\n".join(replies.values_list("text", flat=True))
    # A real reply, not one cut off at the turn limit: a can't passes only once it is said.
    answered = bool(reply) and real.turns <= MOST_TURNS
    passed = restated == case.expect and not redone and answered
    with OUT.open("a") as out:
        record = {
            "model": MODEL,
            "case": case.id,
            "run": run,
            "note": case.note,
            "expect": case.expect,
            "restated": restated,
            "fixes": [fix.fix for fix in Fix.objects.order_by("pk")[fixes_before:]],
            "calls": made,
            "results": [call.result for call in calls],
            "reply": reply,
            "turns": real.turns,
            "passed": passed,
        }
        out.write(json.dumps(record) + "\n")
    assert restated == case.expect
    assert not redone, f"made {redone} before the owner's yes"
    assert answered, "the producer didn't reply to the shop owner"


class RealTurns:
    """The producer's turns from the real model under eval, at most MOST_TURNS of them."""

    def __init__(self, fake: FakeModel) -> None:
        self.provider = OpenAIProvider()
        self.fake = fake
        self.turns = 0

    def take_turn(self, request: TurnRequest) -> TurnReply:
        if request.purpose != "produce":
            return FakeModel.take_turn(self.fake, request)
        self.turns += 1
        if self.turns > MOST_TURNS:
            return TurnReply(
                turn=Turn(says="(eval: turn limit)", calls=()), input_tokens=0, output_tokens=0
            )
        return self.provider.take_turn(replace(request, model=MODEL))
