"""A requested result can cover several scenes, each with its own tries."""

import re

import pytest

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import NO_CHOICES, handoffs, results_of
from .test_asked_changes import filed_note
from .test_repeated_asks_eval import Playing, playing, tiny  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)


def test_one_ask_on_three_scenes_files_one_change_with_three_first_tries(
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Make the light warmer in all three scenes.", "passes", "Warmer light.")
    description = "Use warmer light"
    notes = [
        filed_note(n, first.say, new_asked_change=description if n != 2 else " Use warmer  light ")[
            1
        ]["scenes"][0]
        for n in (1, 2, 3)
    ]
    record = playing(
        tiny("healthy-01-brush", first),
        [
            [turn(calls=[("pass_note", {"scenes": notes})]), turn(says="On it.")],
            [turn(says="The scenes have warmer light now.")],
        ],
    )

    assert record["error"] == ""
    assert [run["scene"] for run in record["asks"][0]["runs"]] == [1, 2, 3]
    (change,) = asked_changes.summary(Session.objects.get())
    assert change["description"] == description
    assert [(scene["scene"], scene["tries"]) for scene in change["scenes"]] == [
        (1, 1),
        (2, 1),
        (3, 1),
    ]

    filed = record["asks"][0]["tool_calls"][0]["result"]
    assert re.findall(r"^scene (\d+)\b.*\b1\b.*\bopen\b.*\brunning\b", filed, re.I | re.M) == [
        "1",
        "2",
        "3",
    ]
    finished = [
        event["text"]
        for event in handoffs("produce")[-1]["conversation"]
        if event["kind"] == "step_finished" and "asked change #1" in event["text"].lower()
    ]
    assert len(finished) == 3
    assert all(
        re.findall(r"^scene (\d+)\b.*\b1\b.*\bopen\b", reply, re.I | re.M) == ["1", "2", "3"]
        for reply in finished
    )
    assert any(
        re.findall(r"^scene (\d+)\b.*\bpassed\b", reply, re.I | re.M) == ["1", "2", "3"]
        for reply in finished
    )


def test_a_scene_can_join_later_and_keep_its_tries_when_another_is_removed(
    playing: Playing,  # noqa: F811
    fake_model: FakeModel,
) -> None:
    first = OwnerMessage("Use warmer light in scene 3.", "passes", "Warmer light.")
    join = OwnerMessage("Add a fourth scene with the same warm light.", "passes", "Warmer light.")
    remove = OwnerMessage("Take out the original scene 3.", "passes", "Removed scene 3.")
    repeat = OwnerMessage("Make the new scene warmer still.", "passes", "Warmer light.")

    def after_the_repeat_filing() -> Turn:
        if "refused" in results_of("pass_note")[-1].lower():
            fake_model.respond("produce", turn(says="The added scene has warmer light now."))
            return turn(calls=[filed_note(3, repeat.say, asked_change=1)])
        return turn(says="I’ll adjust the added scene.")

    record = playing(
        tiny("healthy-01-brush", first, join, remove, repeat),
        [
            [
                turn(calls=[filed_note(3, first.say, new_asked_change="Use warmer light")]),
                turn(says="On it."),
            ],
            [turn(says="Scene 3 has warmer light now.")],
            [
                turn(
                    calls=[
                        (
                            "add_scene",
                            {
                                "at": 4,
                                "line": "Before you head out the door, glide the brush through "
                                "your hair for easy styling.",
                                "kind": "talking",
                                "shows": "The presenter sets the brush down by the door.",
                                "talking_style": "propped",
                                "product_state": "out of the box",
                                "owner_asked": True,
                            },
                        )
                    ]
                ),
                turn(calls=[("run_planning_checks", NO_CHOICES)]),
                turn(calls=[filed_note(4, join.say, asked_change=1)]),
                turn(says="On it."),
            ],
            [turn(says="Scene 4 has the same warmer light.")],
            [
                turn(calls=[("remove_scene", {"scene": 3, "owner_asked": True})]),
                turn(says="I removed the original scene 3."),
            ],
            [
                turn(calls=[filed_note(3, repeat.say, asked_change=1)]),
                after_the_repeat_filing,
            ],
            [turn(says="The added scene has warmer light now.")],
        ],
    )

    assert record["error"] == ""
    assert [[run["scene"] for run in ask["runs"]] for ask in record["asks"]] == [[3], [4], [], [3]]
    first_result = record["asks"][0]["tool_calls"][0]["result"]
    assert not re.search(r"^scene \d+\b.*\bopen\b", first_result, re.I | re.M)
    assert all("failed" not in call["result"].lower() for call in record["asks"][1]["tool_calls"])
    joined = next(
        call["result"] for call in record["asks"][1]["tool_calls"] if call["tool"] == "pass_note"
    )
    assert "try 1" in joined.lower()
    repeated = record["asks"][3]["tool_calls"][-1]["result"].lower()
    assert "try 2" in repeated and "removed" in repeated

    (change,) = asked_changes.summary(Session.objects.get())
    assert {scene["removed"]: (scene["scene"], scene["tries"]) for scene in change["scenes"]} == {
        True: (3, 1),
        False: (3, 2),
    }
