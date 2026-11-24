"""The producer reads earlier tries before resending an owner's repeated change."""

import re

import pytest

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import turn

from .conftest import given_to_the_producer, producer_turns
from .test_asked_changes import filed_note
from .test_repeated_asks_eval import Playing, playing, tiny  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_repeat_gets_history_before_the_next_try_can_film(playing: Playing) -> None:  # noqa: F811
    first = OwnerMessage(
        "Get the product closer to the camera.", "gives_up", "It's still wide.", "Too wide."
    )
    repeat = OwnerMessage(
        "I need a much closer view.", "gives_up", "It's still wide.", "The hand covers it."
    )
    final = OwnerMessage("Let me see it fill the frame.", "passes", "It fills the frame.")
    first_direction = "Move the camera to a close-up during the demonstration."
    next_direction = "Hold the product up to the camera in a static shot."
    final_direction = "Frame the product in the foreground on a steady surface."
    retry = filed_note(2, repeat.say, instruction=next_direction, asked_change=1)

    record = playing(
        tiny("healthy-01-brush", first, repeat, final),
        [
            [
                turn(
                    calls=[
                        filed_note(
                            2,
                            first.say,
                            instruction=first_direction,
                            new_asked_change="Fill the frame with the product",
                        )
                    ]
                ),
                turn(says="I'll try a close-up."),
            ],
            [turn(says="The view is still too wide.")],
            [
                turn(calls=[retry]),
                turn(calls=[retry]),
                turn(says="I'm trying a static close-up."),
            ],
            [turn(says="The product is still covered.")],
            [
                turn(calls=[filed_note(2, final.say, instruction=next_direction, asked_change=1)]),
                turn(calls=[filed_note(2, final.say, instruction=final_direction, asked_change=1)]),
                turn(says="I'm trying a shot on the counter."),
            ],
            [turn(says="Here is the close-up.")],
        ],
    )

    assert record["error"] == ""
    repeated = record["asks"][1]
    history, filmed = (call["result"] for call in repeated["tool_calls"])
    assert "refused" in history.lower()
    assert first_direction in history and first.reason in history
    assert re.search(r"\$\d", history)
    assert first_direction not in filmed and "try 2" in filmed.lower()
    assert [run["instruction"] for run in repeated["runs"]] == [next_direction]

    third = record["asks"][2]
    history, filmed = (call["result"] for call in third["tool_calls"])
    assert "refused" in history.lower()
    assert first_direction in history and first.reason in history
    assert next_direction in history and repeat.reason in history
    assert len(re.findall(r"\$\d", history)) == 2
    assert "try 3" in filmed.lower()
    assert [run["instruction"] for run in third["runs"]] == [final_direction]

    (change,) = asked_changes.summary(Session.objects.get())
    (covered,) = change["scenes"]
    assert covered["tries"] == 3
    assert [len(ask["refusals"]) for ask in covered["asks"]] == [0, 1, 1]


def test_a_failed_try_tells_the_producer_to_report_what_is_still_wrong(
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage(
        "Keep the blue bottle upright.", "gives_up", "It still tips.", "It tips at the end."
    )
    note = filed_note(
        2,
        first.say,
        instruction="Set the bottle straight down and hold the shot.",
        new_asked_change=first.say,
    )

    record = playing(
        tiny("healthy-01-brush", first),
        [
            [turn(calls=[note]), turn(says="I'll try a different shot.")],
            [turn(says="The bottle still tips when it comes to rest.")],
        ],
    )

    assert record["error"] == ""
    finished = next(
        item["text"].lower()
        for item in given_to_the_producer(producer_turns())
        if item["kind"] == "step_finished" and "asked change #1" in item["text"].lower()
    )
    assert first.reason.lower() in finished
    assert re.search(r"\btries?\b[^.]{0,35}\b(remain|left)\b", finished)
    assert re.search(r"\$\d", finished)
