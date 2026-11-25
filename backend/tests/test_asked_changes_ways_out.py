"""The shop owner chooses how to finish a change that spans several scenes."""

import re
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import turn

from .conftest import handoffs
from .test_asked_changes import filed_note
from .test_card_versions import cards
from .test_repeated_asks_eval import Playing, playing, tiny  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)

SCENES = (1, 2, 3)
CHANGE = "Use warmer light"


def note_for(
    scenes: tuple[int, ...], ask: OwnerMessage, **filing: object
) -> tuple[str, dict[str, Any]]:
    return (
        "pass_note",
        {"scenes": [filed_note(scene, ask.say, **filing)[1]["scenes"][0] for scene in scenes]},
    )


def _ways_out(text: str) -> None:
    assert re.search(r"\bshot\b[^\n]*\bscene\s+\d+\b", text, re.I)
    assert re.search(r"\bapproach\b[^\n]*\bscenes\b", text, re.I)
    assert "use_version" in text
    assert re.search(r"\b(wait|choose)\b", text, re.I)


def _covered(text: str, tries: int) -> list[str]:
    rows = re.findall(
        r"^scene (\d+)\b[^\n]*?\b(\d+)[^\d\n]+(\d+)\b[^\n]*\b(open|closed)\b", text, re.I | re.M
    )
    return [scene for scene, used, _limit, _state in rows if int(used) == tries]


def test_refused_linked_notes_report_each_covered_scene_once(playing: Playing) -> None:  # noqa: F811
    first = OwnerMessage("Use warmer light in all three scenes.", "passes", "The lights are warm.")
    record = playing(
        tiny("healthy-01-brush", first),
        [
            [
                turn(calls=[note_for(SCENES, first, new_asked_change=CHANGE)]),
                turn(calls=[note_for((1, 2), first, asked_change=1)]),
                turn(says="The scenes are being filmed."),
            ],
            [turn(says="The warmer clips are ready.")],
        ],
    )

    assert record["error"] == ""
    ask = record["asks"][0]
    assert [run["scene"] for run in ask["runs"]] == list(SCENES)
    refused = ask["tool_calls"][1]["result"]
    assert "refused" in refused.lower()
    assert _covered(refused, 1) == ["1", "2", "3"]


def test_all_three_scenes_can_fail_independently_and_stay_closed(
    playing: Playing,  # noqa: F811
) -> None:
    asks = tuple(
        OwnerMessage(words, "gives_up", "The light is still cold.", "The light stayed cold.")
        for words in (
            "Use warm light in all three scenes.",
            "Please get all those scenes warmer.",
            "All three scenes still need warmer light.",
            "Try the warmer light on all three again.",
        )
    )
    first, second, third, fourth = asks
    record = playing(
        tiny("healthy-01-brush", *asks),
        [
            [
                turn(calls=[note_for(SCENES, first, new_asked_change=CHANGE)]),
                turn(says="I'll try it."),
            ],
            [turn(says="The light is still cold across the ad.")],
            *[
                step
                for ask in (second, third)
                for step in (
                    [
                        turn(calls=[note_for(SCENES, ask, asked_change=1)]),
                        turn(calls=[note_for(SCENES, ask, asked_change=1)]),
                        turn(says="I'll try a different setup."),
                    ],
                    [turn(says="The light is still cold.")],
                )
            ],
            [
                turn(calls=[note_for(SCENES, fourth, asked_change=1)]),
                turn(says="I couldn't make that lighting work. Here are the options."),
            ],
        ],
    )

    assert record["error"] == ""
    assert [tuple(run["scene"] for run in ask["runs"]) for ask in record["asks"]] == [
        SCENES,
        SCENES,
        SCENES,
        (),
    ]
    history = record["asks"][1]["tool_calls"][0]["result"]
    assert _covered(history, 1) == ["1", "2", "3"]
    assert "use_version" not in history

    finished = next(
        event["text"]
        for event in handoffs("produce")[-1]["conversation"]
        if event["kind"] == "step_finished" and re.search(r"\btry\s+3\b", event["text"], re.I)
    )
    assert _covered(finished, 3) == ["1", "2", "3"]
    _ways_out(finished)
    refused = record["asks"][-1]["tool_calls"][0]["result"]
    assert _covered(refused, 3) == ["1", "2", "3"]
    assert all(
        scene["closed"] for scene in asked_changes.summary(Session.objects.get())[0]["scenes"]
    )
    _ways_out(refused)


@pytest.mark.parametrize(
    ("choice", "films"),
    [("different shot", (2,)), ("different approach", SCENES), ("put the scenes back", ())],
)
def test_the_application_only_carries_out_the_owners_chosen_option(
    playing: Playing,  # noqa: F811
    api: APIClient,
    session_id: str,
    choice: str,
    films: tuple[int, ...],
) -> None:
    first = OwnerMessage(
        "Use warmer light in all three scenes.",
        "passes",
        "Only scenes 1 and 3 have warmer light.",
        "Scene 2 stayed cold.",
        by_scene={2: "gives_up"},
    )
    second, third, fourth = (
        OwnerMessage(words, "gives_up", "Scene 2 stayed cold.", "Scene 2 stayed cold.")
        for words in (
            "Scene 2 alone still needs warmer light.",
            "Please make scene 2 warmer like the other two.",
            "One last go for the warmer light in scene 2?",
        )
    )
    pick = OwnerMessage(f"Please {choice}.", "passes", "The chosen option is done.")
    tools = (
        [note_for(films, pick, new_asked_change="Use softer indirect lighting")]
        if films
        else [("use_version", {"scene": n, "version": 1}) for n in (1, 3)]
    )
    record = playing(
        tiny("healthy-01-brush", first, second, third, fourth, pick),
        [
            [
                turn(calls=[note_for(SCENES, first, new_asked_change=CHANGE)]),
                turn(says="I'll try it."),
            ],
            [turn(says="Scenes 1 and 3 changed. Scene 2 is still cold.")],
            *[
                step
                for ask, reply in (
                    (second, "Scene 2 is still cold."),
                    (third, "I couldn't make scene 2 match. Here are your choices."),
                )
                for step in (
                    [
                        turn(calls=[note_for((2,), ask, asked_change=1)]),
                        turn(calls=[note_for((2,), ask, asked_change=1)]),
                        turn(says="I'll try a different setup on scene 2."),
                    ],
                    [turn(says=reply)],
                )
            ],
            [
                turn(calls=[note_for((2,), fourth, asked_change=1)]),
                turn(says="Scene 2 still doesn't match scenes 1 and 3. Choose how to continue."),
            ],
            [turn(calls=tools), turn(says="I used your choice.")],
            *([[turn(says="The ad has your new lighting.")]] if films else []),
        ],
    )

    assert record["error"] == ""
    assert [tuple(run["scene"] for run in ask["runs"]) for ask in record["asks"]] == [
        SCENES,
        (2,),
        (2,),
        (),
        films,
    ]
    history = record["asks"][1]["tool_calls"][0]["result"]
    assert _covered(history, 1) == ["1", "2", "3"]

    before_the_pick = next(
        request
        for request in handoffs("produce")
        if any(event.get("text") == fourth.say for event in request["conversation"])
    )
    finished = next(
        event["text"]
        for event in before_the_pick["conversation"]
        if event["kind"] == "step_finished" and re.search(r"\btry\s+3\b", event["text"], re.I)
    )
    refused = record["asks"][3]["tool_calls"][0]["result"]
    for report in (finished, refused):
        _ways_out(report)
        assert _covered(report, 1) == ["1", "3"]
        assert _covered(report, 3) == ["2"]
        assert re.findall(r"use_version\(scene=(\d+), version=(\d+)\)", report) == [
            ("1", "1"),
            ("3", "1"),
        ]

    changes = asked_changes.summary(Session.objects.get())
    assert [(scene["scene"], scene["tries"]) for scene in changes[0]["scenes"]] == [
        (1, 1),
        (2, 3),
        (3, 1),
    ]
    if films:
        assert len(changes) == 2
        assert [(scene["scene"], scene["tries"]) for scene in changes[1]["scenes"]] == [
            (n, 1) for n in films
        ]
    else:
        assert len(changes) == 1
        assert [call["tool"] for call in record["asks"][-1]["tool_calls"]] == [
            "use_version",
            "use_version",
        ]
        on_the_cards = cards(api, session_id)
        assert all(on_the_cards[n][0]["in_ad"] for n in (1, 3))
        assert all(len(on_the_cards[n]) > 1 for n in (1, 3))
