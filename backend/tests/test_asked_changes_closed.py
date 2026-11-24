"""Closing an asked change in the shop owner's chat."""

import pytest
from pytest_django import Settings as SettingsWrapper

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import turn

from .conftest import handoffs
from .test_asked_changes import filed_note
from .test_repeated_asks_eval import Playing, playing, tiny  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)

DESCRIPTION = "Show the brush going through the hair"
FAILED = "The brush stayed above the hair."
REPEATS = [
    "Please make the brush go through the hair.",
    "It should actually pass through the hair.",
    "The bristles still need to go through it.",
]


def refused_ask(said: str) -> OwnerMessage:
    return OwnerMessage(said, "gives_up", FAILED, FAILED)


def test_the_third_try_closes_the_change_and_the_fourth_ask_is_answered(
    playing: Playing,  # noqa: F811
) -> None:
    asks = [refused_ask(said) for said in [*REPEATS, "Please try again; I'll pay."]]
    record = playing(
        tiny("healthy-01-brush", *asks),
        [
            [
                turn(calls=[filed_note(2, asks[0].say, new_asked_change=DESCRIPTION)]),
                turn(says="I’ll try it."),
            ],
            [turn(says="The brush still stays above the hair.")],
            [
                turn(calls=[filed_note(2, asks[1].say, asked_change=1)]),
                turn(says="I’ll try another shot."),
            ],
            [turn(says="That shot still isn't right.")],
            [
                turn(calls=[filed_note(2, asks[2].say, asked_change=1)]),
                turn(says="I’ll try a different angle."),
            ],
            [turn(says="I couldn't get it through. I can show the brush held near the hair.")],
            [
                turn(calls=[filed_note(2, asks[3].say, asked_change=1)]),
                turn(says="I couldn't get it through. I can show the brush held near the hair."),
            ],
        ],
    )

    assert [len(ask["runs"]) for ask in record["asks"]] == [1, 1, 1, 0]
    assert record["error"] == ""
    refusal = record["asks"][3]["tool_calls"][0]["result"]
    assert "closed" in refusal.lower() and DESCRIPTION in refusal
    assert all(text in refusal for text in REPEATS)
    assert FAILED in refusal and refusal.count("$") == 3
    assert "alternative" in refusal.lower()

    producer_handoffs = handoffs("produce")
    step_lines = [
        event["text"].lower()
        for handoff in producer_handoffs
        for event in handoff["conversation"]
        if event["kind"] == "step_finished"
    ]
    assert any("try 3" in line and "no tries" in line for line in step_lines), step_lines
    assert refusal in [event.get("result") for event in producer_handoffs[-1]["conversation"]]
    assert [entry["role"] for entry in record["asks"][3]["chat"]] == ["user", "agent"]
    (scene,) = asked_changes.summary(Session.objects.get())[0]["scenes"]
    assert scene["tries"] == 3 and scene["closed"]
    assert [ask["tried"] for ask in scene["asks"]] == [True, True, True, False]
    assert refusal in scene["asks"][3]["refusals"][0]["reason"]


@pytest.mark.parametrize("other_scene", [2, 3], ids=["same-scene", "another-scene"])
def test_a_closed_change_leaves_a_new_change_in_the_same_message_free_to_film(
    playing: Playing,  # noqa: F811
    settings: SettingsWrapper,
    other_scene: int,
) -> None:
    settings.ASKED_CHANGE_TRY_LIMIT = 1
    first = refused_ask(REPEATS[0])
    new_result = f"Show the brush resting beside the mirror in scene {other_scene}."
    mixed = OwnerMessage(
        f"Try to get it through the hair again. {new_result}",
        "passes",
        "The brush is beside the mirror.",
    )
    closed = filed_note(2, mixed.say, asked_change=1, instruction=REPEATS[0])
    new = filed_note(other_scene, mixed.say, new_asked_change=new_result, instruction=new_result)
    if other_scene == 2:
        filings = [turn(calls=[closed]), turn(calls=[new])]
    else:
        filings = [
            turn(calls=[("pass_note", {"scenes": [*closed[1]["scenes"], *new[1]["scenes"]]})])
        ]
    record = playing(
        tiny("healthy-01-brush", first, mixed),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=DESCRIPTION)]),
                turn(says="I’ll try it."),
            ],
            [turn(says="I can show it resting beside the mirror instead.")],
            [*filings, turn(says="I’ll show the brush beside the mirror.")],
            [turn(says="I couldn't get it through the hair. The new mirror shot is ready.")],
        ],
    )

    assert record["error"] == ""
    assert [[run["scene"] for run in ask["runs"]] for ask in record["asks"]] == [[2], [other_scene]]
    assert record["asks"][1]["runs"][0]["instruction"] == new_result
    response = "\n".join(call["result"] for call in record["asks"][1]["tool_calls"])
    assert "closed" in response and "#1" in response and "#2" in response
    original, alternative = asked_changes.summary(Session.objects.get())
    assert original["scenes"][0]["tries"] == 1
    assert not original["scenes"][0]["asks"][-1]["tried"]
    assert alternative["description"] == new_result
    assert alternative["scenes"][0]["asks"][0]["runs"][0]["try_number"] == 1


def test_the_closed_change_stays_closed_after_five_other_changes(
    playing: Playing,  # noqa: F811
    settings: SettingsWrapper,
) -> None:
    settings.ASKED_CHANGE_TRY_LIMIT = 1
    first = refused_ask(REPEATS[0])
    later = refused_ask("One more thing: can you make it go through the hair now?")
    others = [
        OwnerMessage(text, "passes", "The requested change is visible.")
        for text in [
            "Place the brush on the counter.",
            "Make the light warmer.",
            "Show a blue towel.",
            "Move to the other side of the mirror.",
            "Give the presenter a green shirt.",
        ]
    ]
    record = playing(
        tiny("healthy-01-brush", first, *others, later),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=DESCRIPTION)]),
                turn(says="I’ll try it."),
            ],
            [
                turn(
                    says="I couldn't make it go through the hair. I can show it held near the hair."
                )
            ],
            *[
                producer_step
                for ask in others
                for producer_step in (
                    [
                        turn(calls=[filed_note(2, ask.say, new_asked_change=ask.say)]),
                        turn(says="On it."),
                    ],
                    [turn(says="Here is the updated shot.")],
                )
            ],
            [
                turn(calls=[filed_note(2, later.say, asked_change=1)]),
                turn(says="I couldn't make it go through. I can show it held near the hair."),
            ],
        ],
    )

    assert record["error"] == ""
    assert [len(ask["runs"]) for ask in record["asks"]] == [1, 1, 1, 1, 1, 1, 0]
    assert "closed" in record["asks"][-1]["tool_calls"][0]["result"]
