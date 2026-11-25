"""Play the nine repeated-ask groups through chat and score what the app recorded."""

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pytest_django import Settings as SettingsWrapper

from evals.repeated_asks.cases import ASKED_CHANGES_CASES_FILE, load_cases
from evals.repeated_asks.per_ask import summarize
from evals.repeated_asks.runner import Session, save_attempt
from gateway.fake import turn
from gateway.types import Turn

from .test_asked_changes import filed_note
from .test_repeated_asks_eval import Playing, note, playing  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)
CASES = load_cases(ASKED_CHANGES_CASES_FILE)

# The number is the ask that introduced the change. Zero asks the app to start a scene itself.
# Each tuple contains the scene and the number, with more than one tuple on a mixed ask.
type Filing = tuple[int, int]


@pytest.mark.parametrize(
    ("case_id", "numbers", "filings", "tries", "closed", "mix_ups"),
    [
        ("impossible-fails-01-brush", (1, 2, 3, 4), (((2, 1),),) * 4, [3], 1, (0, 0, 0)),
        ("hard-01-lens", (1, 2, 3), (((2, 1),),) * 3, [3], 1, (0, 0, 0)),
        (
            "alternative-works-01-brush",
            (1, 2, 3, 4, 5),
            (((2, 1),),) * 4 + (((2, 5),),),
            [3, 1],
            1,
            (0, 0, 0),
        ),
        ("reworded-01-brush", (1, 2, 3, 4, 5), (((2, 1),),) * 5, [3], 1, (0, 0, 0)),
        ("other-scene-01-brush", (1, 5), (((2, 1),), ((3, 1),)), [2], 0, (0, 0, 0)),
        (
            "closed-and-new-07-speaker",
            (1, 2, 3, 4, 5),
            (((2, 1),),) * 4 + (((2, 1), (1, 5)),),
            [3, 1],
            1,
            (0, 0, 0),
        ),
        ("breaks-08-lens", (1, 2), (((2, 1),),) * 2, [1], 0, (0, 0, 0)),
        (
            "much-later-01-brush",
            (1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
            (((2, 1),),) * 4 + (((1, 5),), ((3, 6),), ((1, 7),), ((3, 8),), ((2, 9),), ((2, 1),)),
            [3, 1, 1, 1, 1, 1],
            1,
            (0, 0, 0),
        ),
        (
            "solved-01-brush",
            (1, 2, 3, 4),
            (((2, 1),), ((2, 2),), ((3, 0),), ((2, 1),)),
            [2, 1],
            0,
            (1, 1, 1),
        ),
    ],
    ids=[
        "impossible",
        "third-try",
        "alternative",
        "reworded",
        "other-scene",
        "mixed",
        "technical-break",
        "much-later",
        "normal-with-mistakes",
    ],
)
def test_the_eval_reads_the_app_record_in_each_scripted_group(
    playing: Playing,  # noqa: F811
    settings: SettingsWrapper,
    case_id: str,
    numbers: tuple[int, ...],
    filings: tuple[tuple[Filing, ...], ...],
    tries: list[int],
    closed: int,
    mix_ups: tuple[int, int, int],
) -> None:
    full = CASES[case_id]
    case = replace(full, messages=tuple(full.messages[n - 1] for n in numbers))
    settings.ASKED_CHANGE_TRY_LIMIT = 3
    registered: dict[int, int] = {}
    used: dict[Filing, int] = {}
    turns: list[list[Turn]] = []
    did_start = []
    for message, actions in zip(case.messages, filings, strict=True):
        batch: list[Turn] = []
        opens = False
        for scene, result in actions:
            if result == 0:
                batch.append(turn(calls=[note(scene, message.say, owner_asked=False)]))
                opens = True
                continue
            if result in registered:
                call = filed_note(scene, message.say, asked_change=registered[result])
            else:
                call = filed_note(scene, message.say, new_asked_change=message.say)
                registered[result] = len(registered) + 1
            batch.append(turn(calls=[call]))
            count = used.get((scene, result), 0)
            if count == 3:
                continue
            if count:
                batch.append(turn(calls=[call]))
            opens = True
            if message.ending != "breaks":
                used[scene, result] = count + 1
        did_start.append(opens)
        if opens:
            batch.append(turn(says="I'll see how that shot turns out."))
            turns.append(batch)
            batch = []
        batch.append(turn(says="Here is what happened with your request."))
        turns.append(batch)

    record = playing(case, turns)
    assert not record["error"], record
    assert [bool(ask["runs"]) for ask in record["asks"]] == did_start
    assert len(record["asked_changes"]) == len(tries)
    for change in record["asked_changes"]:
        assert all(
            {"message_id", "tried", "runs"} <= ask.keys()
            for scene in change["scenes"]
            for ask in scene["asks"]
        )
    report = summarize([record], {case.id: case})
    metrics: dict[str, Any] = report["groups"][case.group]["scoring"]
    assert metrics["closed_changes"] == closed
    assert metrics["tries_per_change"] == {n: tries.count(n) for n in set(tries)}
    assert (
        metrics["self_started"],
        metrics["repeat_filed_as_new"],
        metrics["different_filed_as_old"],
    ) == mix_ups
    if case.kind == "cant_be_done":
        assert metrics["replies"]["honest_close"] == {"pending": 2}
        assert metrics["replies"]["gave_up_early"] == {"pending": 2}
    if case.kind == "hard_but_possible":
        assert "honest_close" not in metrics["replies"]
    if case.kind == "closed_and_new":
        assert metrics["replies"]["covered_both"] == {"pending": 1}
    if case.kind == "breaks":
        broken = record["asked_changes"][0]["scenes"][0]["asks"][0]
        assert not broken["tried"]
        assert broken["runs"][0]["outcome"] == "failed"

    if output := os.environ.get("ADFORGE_REPEATED_ASKS_SCRIPTED_OUT"):
        # The saved report uses the original ask numbers for the short other-scene fixture.
        for ask, number in zip(record["asks"], numbers, strict=True):
            ask["number"] = number
        save_attempt(Path(output), Session(case.id, 1), record)
