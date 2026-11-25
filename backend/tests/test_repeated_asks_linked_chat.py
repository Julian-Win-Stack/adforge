"""The linked-scene eval reads its scores from saved, scripted chat sessions."""

import os
from pathlib import Path
from typing import Any

import pytest

from evals.repeated_asks.cases import LINKED_CASES_FILE, load_cases
from evals.repeated_asks.per_ask import summarize
from evals.repeated_asks.runner import Session, save_attempt
from gateway.fake import turn
from gateway.types import Turn

from .test_asked_changes_ways_out import note_for
from .test_repeated_asks_eval import Playing, playing  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)
CASES = load_cases(LINKED_CASES_FILE)
THREE = (1, 2, 3)


@pytest.mark.parametrize(
    ("case_id", "scenes_by_ask", "restore", "closure_replies"),
    [
        ("one-scene-fails-01-brush", (THREE, (2,), (2,), (), ()), False, 3),
        ("picks-back-03-floss", (THREE, (2,), (2,), (), ()), True, 2),
        ("all-fail-01-floss", (THREE, THREE, THREE, (), ()), False, 3),
        ("joins-later-01-roller", (THREE, (4,), ()), False, 0),
        ("works-everywhere-01-brush", (THREE, (1,), (3,), (1,)), False, 0),
    ],
)
def test_linked_reports_match_the_owner_and_scene_scripts(
    playing: Playing,  # noqa: F811
    case_id: str,
    scenes_by_ask: tuple[tuple[int, ...], ...],
    restore: bool,
    closure_replies: int,
) -> None:
    case = CASES[case_id]
    turns: list[list[Turn]] = []
    for number, (message, scenes) in enumerate(zip(case.messages, scenes_by_ask, strict=True), 1):
        batch: list[Turn] = []
        if number == 1 or (case.kind == "works_everywhere" and scenes):
            batch.append(turn(calls=[note_for(scenes, message, new_asked_change=message.say)]))
        elif scenes:
            note = note_for(scenes, message, asked_change=1)
            batch.append(turn(calls=[note]))
            if case.kind != "scene_joins_later":
                batch.append(turn(calls=[note]))
        elif restore and number == 5:
            batch.append(
                turn(calls=[("use_version", {"scene": scene, "version": 1}) for scene in (1, 3)])
            )
        elif closure_replies and number >= 4:
            earlier = THREE if case.kind == "all_scenes_fail" else (2,)
            batch.append(turn(calls=[note_for(earlier, message, asked_change=1)]))
        if scenes:
            turns.append([*batch, turn(says="I'll check how the changes turn out.")])
            batch = []
        turns.append([*batch, turn(says="Here's how your request turned out.")])

    record = playing(case, turns)
    assert not record["error"], record
    assert [tuple(run["scene"] for run in ask["runs"]) for ask in record["asks"]] == list(
        scenes_by_ask
    )
    result = summarize([record], CASES)
    assert case.group is not None
    group: dict[str, Any] = result["groups"][case.group]
    assert [line["tried"] for line in group["per_ask"]] == [
        int(bool(scenes)) for scenes in scenes_by_ask
    ]
    assert group["sessions_as_expected"] == 1
    assert group["scoring"]["repeat_filed_as_new"] == 0
    assert group["scoring"]["self_started"] == 0
    assert (
        group["scoring"]["replies"].get("linked_ways_out", {}).get("pending", 0) == closure_replies
    )

    if case.kind == "one_scene_fails":
        assert group["linked"]["refilmed_other_scenes"] == 0
    if case.kind == "scene_joins_later":
        assert group["linked"]["joined_on_first_try"] == 1
    if restore:
        assert group["linked"]["choices"] == [
            {
                "case": case.id,
                "repeat": 1,
                "picked": "Put the changed scenes back",
                "filmed": [],
                "restored": [{"scene": 1, "version": 1}, {"scene": 3, "version": 1}],
                "exact": True,
            }
        ]

    if output := os.environ.get("ADFORGE_LINKED_ASKS_SCRIPTED_OUT"):
        save_attempt(Path(output), Session(case.id, 1), record)
