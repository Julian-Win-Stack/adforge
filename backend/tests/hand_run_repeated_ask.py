"""PROTOTYPE #738 hand run, not a test: CI never collects it (no test_ prefix).

One owner session where the producer is the REAL model (Azure/OpenAI text, paid) and every
other step is faked: scene agents draw and film, every clip passes its check. The owner asks
three times for the same change on scene 2. Run it by path:

    REAL_PRODUCER=1 uv run pytest -s tests/hand_run_repeated_ask.py

It prints the producer's conversation and the stored problem list. Without REAL_PRODUCER it
is skipped, so it never spends by accident."""

import json
import os
from collections.abc import Callable
from typing import Any

import pytest
from django.db.models import Sum
from pytest_django import Settings as SettingsWrapper

from adforge.settings import openai_account
from chat.models import Message, Session
from gateway import gateway
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ToolUse, TurnReply, TurnRequest

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not os.environ.get("REAL_PRODUCER"), reason="spends on the real model"),
]

REAL = {"produce", "look"}


def _real_text(settings: SettingsWrapper) -> None:
    key, address = openai_account(os.environ)
    settings.OPENAI_API_KEY = key
    settings.OPENAI_BASE_URL = address
    settings.OPENAI_TEXT_ON_AZURE = bool(os.environ.get("AZUREE_OPENAI_KEY"))
    gateway._openai.cache_clear()


def _scene_agent(request: TurnRequest) -> Any:
    """Draw, then film; every clip passes."""
    used = [each.tool for each in request.handoff.conversation if isinstance(each, ToolUse)]
    if used and used[-1] == "draw_starting_picture":
        return turn(calls=[("film", FILM)])
    return turn(calls=[("draw_starting_picture", PICTURE_FORM)])


def _passthrough(fake: FakeModel) -> None:
    faked = fake.take_turn

    def take_turn(request: TurnRequest, *, on_openai: bool = False) -> TurnReply:
        if request.purpose in REAL and not fake._script(request.purpose, None):
            return gateway._openai().take_turn(request, on_openai=on_openai)
        return faked(request, on_openai=on_openai)

    fake.take_turn = take_turn  # type: ignore[method-assign]


def _print_session() -> None:
    session = Session.objects.get()
    print("\n===== CHAT (owner and producer) =====")
    for message in session.messages.exclude(role=Message.Role.NOTICE).order_by("seq"):
        if message.text:
            print(f"[{message.role}] {message.text}")
    print("\n===== PASS_NOTE CALLS =====")
    for call in session.tool_calls.filter(agent="producer", tool="pass_note"):
        print(json.dumps(call.arguments, indent=1))
        print("->", call.result)
    try:
        # Only on the prototype branch: the same file runs on main for a "before" run.
        from agents import owner_problems
    except ImportError:
        owner_problems = None  # type: ignore[assignment]
    if owner_problems is not None:
        print("\n===== PROBLEM-LIST SYSTEM NOTES =====")
        for note in session.system_notes.filter(kind="problem_list"):
            print(f"--- note {note.pk} (read {note.read_at})\n{note.text}")
        print("\n===== STORED PROBLEMS =====")
        print(json.dumps(owner_problems.summary(session), indent=1))
    spent = (
        ModelCall.objects.filter(purpose__in=REAL)
        .exclude(input_tokens=FakeModel.INPUT_TOKENS)
        .aggregate(cost=Sum("cost_usd"))["cost"]
    )
    print(f"\nReal model spend: ${spent}")


def test_hand_run(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: SettingsWrapper,
) -> None:
    fake_model.answer_unscripted("scene_agent", _scene_agent)
    # Scene 2's first clip, all faked.
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the mug.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)

    _real_text(settings)
    settings.PRODUCER_MOST_NUDGES = 0
    _passthrough(fake_model)

    first = "Scene 2: the mug looks tiny. Make it much bigger in the frame."
    # Ask 1: the first pass_note names no problem and is refused; the real producer goes on.
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": "Make the mug fill more of the frame.",
                                "quotes": [first],
                                "owner_asked": True,
                            }
                        ]
                    },
                )
            ]
        ),
    )
    say(first)
    steps.run_held()
    # Ask 2 and ask 3, in other words.
    say("The mug is still way too small in scene 2.")
    steps.run_held()
    say("Come on, scene 2's mug is STILL tiny. I need it big.")
    steps.run_held()
    _print_session()
