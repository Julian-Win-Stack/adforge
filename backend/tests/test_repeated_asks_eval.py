"""The repeated-ask eval (evals/repeated_asks): one tiny session of each kind played through
the chat with the producer scripted, and what the eval counts from its record."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from pytest_httpserver import HTTPServer

from evals.repeated_asks.cases import Case, OwnerMessage, load_cases
from evals.repeated_asks.counts import counts
from gateway.fake import FakeModel, turn

from .conftest import HeldSteps
from .repeated_asks_play import play

pytestmark = pytest.mark.django_db(transaction=True)

CASES = load_cases()


def tiny(case_id: str, *messages: OwnerMessage) -> Case:
    return replace(CASES[case_id], messages=messages)


def note(scene: int, said: str, *, owner_asked: bool = True) -> tuple[str, dict[str, Any]]:
    return (
        "pass_note",
        {
            "scenes": [
                {
                    "scene": scene,
                    "instruction": f"Change scene {scene}: {said}",
                    "quotes": [said],
                    "owner_asked": owner_asked,
                    "new_asked_change": said if owner_asked else None,
                }
            ]
        },
    )


@pytest.fixture
def playing(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
) -> Callable[[Case, list[list[Any]]], dict[str, Any]]:
    """Play a case, the producer taking each list of turns in order once the ad is made."""

    def playing(case: Case, turns: list[list[Any]]) -> dict[str, Any]:
        def scripted() -> None:
            for each in turns:
                fake_model.respond("produce", *each)

        record = play(
            case,
            repeat=1,
            fake_model=fake_model,
            httpserver=httpserver,
            say=say,
            steps=steps,
            after_the_ad=scripted,
        )
        assert fake_model.left("produce") == 0, "scripted producer turns were never taken"
        return record

    return playing


type Playing = Callable[[Case, list[list[Any]]], dict[str, Any]]

GIVES_UP = "The video model can't do it."


def test_impossible_check_fails_counts_the_owner_note_and_the_producers_extra_idea(
    playing: Playing,
) -> None:
    first = OwnerMessage(
        "Scene 2: make the brush go through the hair.", "gives_up", "On top.", GIVES_UP
    )
    second = OwnerMessage("Scene 2: still on top of the hair!", "gives_up", "On top.", GIVES_UP)
    record = playing(
        tiny("impossible-fails-01-brush", first, second),
        [
            [turn(calls=[note(2, first.say)]), turn(says="On it.")],
            [turn(calls=[note(2, first.say, owner_asked=False)]), turn(says="Trying once more.")],
            [turn(says="Scene 2 is said to camera now.")],
            [turn(says="I tried twice and couldn't get it; this is the closest I can do.")],
        ],
    )

    found = counts(record)
    assert record["error"] == ""
    assert found["per_ask"] == [2, 0]
    assert (found["owner"], found["producer"], found["code"]) == (1, 1, 0)
    assert found["refused_first"] is False
    assert "problems" not in found


def test_impossible_check_passes_keeps_looks_answer_and_counts_no_remake_after_the_refusal(
    playing: Playing,
) -> None:
    missing = "The bristles stay on top of the hair."
    first = OwnerMessage(
        "Scene 2: the brush should go through the hair.", "passes_missing", missing
    )
    second = OwnerMessage("Scene 2: it still skims the hair.", "passes_missing", missing)
    record = playing(
        tiny("impossible-passes-01-brush", first, second),
        [
            [turn(calls=[note(2, first.say)]), turn(says="On it.")],
            [turn(says="Scene 2 is remade.")],
            [
                turn(
                    calls=[
                        (
                            "look",
                            {
                                "scene": 2,
                                "what": "clip",
                                "number": None,
                                "question": "Does the brush go through the hair?",
                            },
                        )
                    ]
                ),
                turn(says="I tried and couldn't get the brush through the hair."),
            ],
        ],
    )

    found = counts(record)
    assert found["per_ask"] == [1, 0]
    assert record["asks"][0]["runs"][0]["ended"] == "made a clip that passed its check"
    looked = record["asks"][1]["tool_calls"][0]
    assert looked["tool"] == "look" and missing in looked["result"]
    assert found["remade_from_ask_3"] == []


def test_hard_but_possible_counts_a_remake_on_the_third_ask(playing: Playing) -> None:
    asks = [
        OwnerMessage("Scene 2: stay zoomed in.", "passes_missing", "It pulls back out."),
        OwnerMessage("Scene 2: it still zooms out.", "passes_missing", "It pulls back out."),
        OwnerMessage("Scene 2: please stay close.", "passes", "It stays close."),
    ]
    record = playing(
        tiny("hard-01-lens", *asks),
        [
            step
            for ask in asks
            for step in (
                [turn(calls=[note(2, ask.say)]), turn(says="On it.")],
                [turn(says="Remade.")],
            )
        ],
    )

    found = counts(record)
    assert found["per_ask"] == [1, 1, 1]
    assert found["remade_from_ask_3"] == [True]
    assert [run["instruction"] for ask in record["asks"] for run in ask["runs"]] == [
        f"Change scene 2: {ask.say}" for ask in asks
    ]


def test_across_scenes_records_a_remake_on_each_scene(playing: Playing) -> None:
    asks = [
        OwnerMessage(f"Scene {n}: the lighting is too dark.", "passes", "It is bright.")
        for n in (1, 2, 3)
    ]
    record = playing(
        tiny("across-01-brush", *asks),
        [
            step
            for n, ask in enumerate(asks, 1)
            for step in (
                [turn(calls=[note(n, ask.say)]), turn(says="On it.")],
                [turn(says="Remade.")],
            )
        ],
    )

    assert [run["scene"] for ask in record["asks"] for run in ask["runs"]] == [1, 2, 3]
    assert counts(record)["owner"] == 3


def test_healthy_counts_a_remake_on_every_ask_and_keeps_the_chat(playing: Playing) -> None:
    asks = [
        OwnerMessage(text, "passes", "It is there.")
        for text in ("Scene 2: warmer light.", "Scene 2: longer hair.", "Scene 2: marble counter.")
    ]
    record = playing(
        tiny("healthy-01-brush", *asks),
        [
            step
            for ask in asks
            for step in (
                [turn(calls=[note(2, ask.say)]), turn(says="On it.")],
                [turn(says="Remade.")],
            )
        ],
    )

    assert counts(record)["per_ask"] == [1, 1, 1]
    assert [
        message["text"]
        for message in record["asks"][2]["chat"]
        if message["role"] in ("user", "agent")
    ] == ["Scene 2: marble counter.", "On it.", "Remade."]


def test_solved_then_different_counts_no_remake_on_thanks_and_one_on_the_new_ask(
    playing: Playing,
) -> None:
    asks = [
        OwnerMessage("Scene 2: show it closer.", "passes_missing", "It is far."),
        OwnerMessage("Scene 2: still too far.", "passes", "It is close."),
        OwnerMessage("Great, scene 2 is good now.", "passes", "It is close."),
        OwnerMessage("Scene 2: make the hair blonde.", "passes", "The hair is blonde."),
    ]
    remade = [[turn(calls=[note(2, asks[n].say)]), turn(says="On it.")] for n in (0, 1, 3)]
    record = playing(
        tiny("solved-01-brush", *asks),
        [
            remade[0],
            [turn(says="Remade.")],
            remade[1],
            [turn(says="Remade.")],
            [turn(says="Glad you like it.")],
            remade[2],
            [turn(says="Remade.")],
        ],
    )

    found = counts(record)
    assert found["per_ask"] == [1, 1, 0, 1]
    assert found["refused_first"] is False


@pytest.mark.parametrize("shop", sorted({case.shop.key for case in CASES.values()}))
def test_every_shop_in_the_case_file_gets_a_finished_ad(shop: str, playing: Playing) -> None:
    case = next(case for case in CASES.values() if case.shop.key == shop)
    said = OwnerMessage("Looks fine.", "passes", "Nothing.")

    record = playing(tiny(case.id, said), [[turn(says="Thanks!")]])

    assert record["error"] == ""
    assert counts(record)["per_ask"] == [0]


def test_the_player_records_the_developer_reason_when_the_producer_is_cut_off(
    playing: Playing,
) -> None:
    message = OwnerMessage("Can you make scene 2 brighter?", "passes", "It looks fine.")
    record = playing(
        tiny("impossible-fails-01-brush", message, message),
        [[RuntimeError("The scripted producer service stopped responding")]],
    )

    [ask] = record["asks"]
    assert not record["error"]
    [reason] = ask["cut_offs"]
    assert "The scripted producer service stopped responding" in reason


def test_the_player_can_script_a_scene_run_that_breaks_without_a_clip(playing: Playing) -> None:
    message = OwnerMessage(
        "Can you make scene 2 brighter?",
        "breaks",
        "It's still dark.",
        "The scripted service broke.",
    )
    record = playing(
        tiny("impossible-fails-01-brush", message),
        [[turn(calls=[note(2, message.say)]), turn(says="On it.")], [turn(says="The step broke.")]],
    )

    assert not record["error"]
    [run] = record["asks"][0]["runs"]
    assert run["clips"] == 0
    assert "The scripted service broke" in run["ended"]
