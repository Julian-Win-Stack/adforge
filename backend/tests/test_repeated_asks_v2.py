"""PROTOTYPE #738 v2, free scripted checks: each path of the thing count, played through the
chat with the producer scripted and everything else faked."""

from typing import Any

import pytest

from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import turn

from .test_repeated_asks_eval import GIVES_UP, Playing, tiny
from .test_repeated_asks_eval import playing as playing  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def filed(
    scene: int,
    said: str,
    *,
    same: int | None = None,
    new: str = "",
    instruction: str = "",
    owner_asked: bool = True,
) -> tuple[str, dict[str, Any]]:
    return (
        "pass_note",
        {
            "scenes": [
                {
                    "scene": scene,
                    "instruction": instruction or f"Change scene {scene}: {said}",
                    "quotes": [said],
                    "owner_asked": owner_asked,
                    **({"same_thing": same} if same is not None else {}),
                    **({"new_thing": new} if new else {}),
                }
            ]
        },
    )


def can_not(say: str) -> OwnerMessage:
    return OwnerMessage(say, "gives_up", "On top.", GIVES_UP)


ASKS = [can_not(f"Scene 2: the brush must go through the hair ({n}).") for n in range(1, 6)]


def results(record: dict[str, Any], ask: int) -> list[str]:
    return [call["result"] for call in record["asks"][ask - 1]["tool_calls"]]


def runs(record: dict[str, Any]) -> list[int]:
    return [len(ask["runs"]) for ask in record["asks"]]


def first_then_repeats(asks: list[OwnerMessage]) -> list[list[Any]]:
    """Ask 1 filed new; every later ask filed as thing 1, sent again after its briefing."""
    turns: list[list[Any]] = [
        [turn(calls=[filed(2, asks[0].say, new="brush through hair")]), turn(says="On it.")],
        [turn(says="Still not right; it cost $1.")],
    ]
    for n, ask in enumerate(asks[1:], start=2):
        turns.append(
            [
                turn(calls=[filed(2, ask.say, same=1)]),
                turn(calls=[filed(2, ask.say, same=1, instruction=f"Different action {n}.")]),
                turn(says="On it."),
            ]
        )
        if n <= 3:
            turns.append([turn(says="Still not right.")])
    return turns


def test_a_repeat_gets_the_history_before_filming_and_closes_after_three_tries(
    playing: Playing,
) -> None:
    turns = first_then_repeats(ASKS[:4])
    # Ask 4: the briefing never comes, the closed refusal does; the resend is refused too.
    turns[-1] = [
        turn(calls=[filed(2, ASKS[3].say, same=1)]),
        turn(says="I couldn't get it; the closest I can do is a side view."),
    ]
    record = playing(tiny("impossible-fails-01-brush", *ASKS[:4]), turns)

    assert record["error"] == ""
    assert runs(record) == [1, 1, 1, 0]
    briefing = results(record, 2)[0]
    assert "not filmed yet" in briefing and "Try 1: Change scene 2" in briefing
    assert "gave up" in briefing
    assert "try 2 of 3" in results(record, 2)[1]
    assert "Try 2: Different action 2." in results(record, 3)[0]
    assert "try 3 of 3" in results(record, 3)[1] and "last try" in results(record, 3)[1]
    assert "is closed" in results(record, 4)[0]
    [thing] = record["problems"]
    assert (thing["tries"], thing["closed"], thing["scene"]) == (3, True, 2)
    assert [run["instruction"] for ask in record["asks"] for run in ask["runs"]] == [
        f"Change scene 2: {ASKS[0].say}",
        "Different action 2.",
        "Different action 3.",
    ]


def test_nothing_films_by_itself_after_an_owner_asked_try_fails(playing: Playing) -> None:
    record = playing(
        tiny("impossible-fails-01-brush", ASKS[0]),
        [
            [turn(calls=[filed(2, ASKS[0].say, new="brush through hair")]), turn(says="On it.")],
            [
                turn(calls=[filed(2, ASKS[0].say, owner_asked=False)]),
                turn(calls=[filed(2, ASKS[0].say, new="brush through hair again")]),
                turn(
                    calls=[
                        (
                            "start_scene_agent",
                            {"scene": 2, "instruction": "Again.", "quotes": [ASKS[0].say]},
                        )
                    ]
                ),
                turn(says="That didn't work; the scene keeps its clip."),
            ],
        ],
    )

    assert runs(record) == [1]
    told = results(record, 1)
    assert "try 1 of 3" in told[0]
    assert all("already had its one try" in each for each in told[1:3]), told
    assert "Refused" in told[3] or "already had its one try" in told[3], told
    assert record["asks"][0]["runs"][0]["origin"] == "owner"


def test_a_closed_thing_stays_refused_after_other_changes_and_a_new_thing_films(
    playing: Playing,
) -> None:
    other = OwnerMessage("Scene 1: warmer light.", "passes", "It is there.")
    both = OwnerMessage(
        "Scene 3: plain wall. Also scene 2: the brush must go through the hair.",
        "passes",
        "It is there.",
    )
    turns = first_then_repeats(ASKS[:3])
    turns += [
        [turn(calls=[filed(2, ASKS[3].say, same=1)]), turn(says="I couldn't get it.")],
        [turn(calls=[filed(1, other.say, new="warmer light")]), turn(says="On it.")],
        [turn(says="Done.")],
        [
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 3,
                                    "instruction": "Plain wall.",
                                    "quotes": ["Scene 3: plain wall."],
                                    "owner_asked": True,
                                    "new_thing": "plain wall",
                                },
                                {
                                    "scene": 2,
                                    "instruction": "Brush through hair.",
                                    "quotes": ["scene 2: the brush must go through the hair."],
                                    "owner_asked": True,
                                    "same_thing": 1,
                                },
                            ]
                        },
                    )
                ]
            ),
            turn(says="Scene 3 is on its way; scene 2 I couldn't get."),
        ],
        [turn(says="Scene 3 is done.")],
    ]
    record = playing(tiny("impossible-fails-01-brush", *ASKS[:4], other, both), turns)

    assert record["error"] == ""
    assert runs(record) == [1, 1, 1, 0, 1, 1]
    assert [run["scene"] for run in record["asks"][5]["runs"]] == [3]
    assert "is closed" in results(record, 6)[0]
    assert "new thing" in results(record, 6)[0]
    things = {thing["number"]: thing for thing in record["problems"]}
    assert things[1]["tries"] == 3 and things[1]["closed"]
    assert things[2]["tries"] == 1 and things[3]["tries"] == 1


def test_the_same_complaint_on_another_scene_is_its_own_thing(playing: Playing) -> None:
    on_three = OwnerMessage("Scene 3 has it too.", "passes", "It is there.")
    turns = first_then_repeats(ASKS[:1])
    turns += [
        [
            turn(calls=[filed(3, on_three.say, same=1)]),
            turn(calls=[filed(3, on_three.say, new="scene 3 too")]),
            turn(says="On it."),
        ],
        [turn(says="Done.")],
    ]
    record = playing(tiny("impossible-fails-01-brush", ASKS[0], on_three), turns)

    assert runs(record) == [1, 1]
    assert "thing of its own" in results(record, 2)[0]
    assert [thing["scene"] for thing in record["problems"]] == [2, 3]


def test_a_technical_failure_with_no_clip_is_not_a_try(playing: Playing) -> None:
    broken = OwnerMessage(ASKS[0].say, "breaks", "On top.", GIVES_UP)
    record = playing(
        tiny("impossible-fails-01-brush", broken, ASKS[1]),
        [
            [turn(calls=[filed(2, broken.say, new="brush through hair")]), turn(says="On it.")],
            [
                turn(calls=[filed(2, broken.say, same=1)]),
                turn(says="It broke, so I started it again."),
            ],
            [turn(says="Still not right.")],
            [
                turn(calls=[filed(2, ASKS[1].say, same=1)]),
                turn(calls=[filed(2, ASKS[1].say, same=1, instruction="Side view.")]),
                turn(says="On it."),
            ],
            [turn(says="Still not right.")],
        ],
    )

    assert record["error"] == ""
    first = record["asks"][0]["runs"]
    assert [run["broke"] for run in first] == [True, False]
    assert "try 1 of 3" in results(record, 1)[1]
    assert "try 2 of 3" in results(record, 2)[1]
    [thing] = record["problems"]
    assert thing["tries"] == 2


def test_two_changes_on_one_scene_in_one_message_film_once_as_one_note(playing: Playing) -> None:
    both = OwnerMessage("Scene 2: warmer light, and make the hair blonde.", "passes", "Done.")
    record = playing(
        tiny("healthy-01-brush", both),
        [
            [
                turn(calls=[filed(2, both.say, new="warmer light and blonde hair")]),
                turn(says="On it."),
            ],
            [turn(says="Done.")],
        ],
    )

    assert runs(record) == [1]
    assert "try 1 of 3" in results(record, 1)[0]


def test_a_closed_thing_and_a_new_thing_on_the_same_scene_film_the_new_one(
    playing: Playing,
) -> None:
    both = OwnerMessage(
        "Scene 2: make the hair blonde. Also the brush must go through the hair.",
        "passes",
        "Done.",
    )
    turns = first_then_repeats(ASKS[:3])
    turns += [
        [turn(calls=[filed(2, ASKS[3].say, same=1)]), turn(says="I couldn't get it.")],
        [
            turn(calls=[filed(2, both.say, same=1)]),
            turn(calls=[filed(2, both.say, new="blonde hair", instruction="Blonde hair.")]),
            turn(says="Blonde hair is on its way; the brush I couldn't get."),
        ],
        [turn(says="Done.")],
    ]
    record = playing(tiny("impossible-fails-01-brush", *ASKS[:4], both), turns)

    assert runs(record) == [1, 1, 1, 0, 1]
    assert "is closed" in results(record, 5)[0]
    assert "new thing 2" in results(record, 5)[1]
