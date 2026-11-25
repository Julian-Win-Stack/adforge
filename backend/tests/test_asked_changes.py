"""Owner notes filed through chat and read back for scoring."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings as SettingsWrapper
from pytest_httpserver import HTTPServer

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps, handoffs, results_of
from .repeated_asks_play import Script, fake_the_rest, make_the_ad, serve_shop
from .test_repeated_asks_eval import Playing, playing, tiny  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db(transaction=True)


def filed_note(scene: int, said: str, **filing: object) -> tuple[str, dict[str, Any]]:
    return "pass_note", {
        "scenes": [
            {"scene": scene, "instruction": said, "quotes": [said], "owner_asked": True, **filing}
        ]
    }


def test_an_owner_note_files_a_change_and_the_next_ask_uses_its_number(
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Move the camera closer.", "gives_up", "It is too wide.", "Too wide.")
    again = OwnerMessage("Fill the frame with the product.", "passes", "It fills the frame.")
    description = "Show the product close up"
    record = playing(
        tiny("healthy-01-brush", first, again),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=description)]),
                turn(says="On it."),
            ],
            [turn(says="That view is still too wide.")],
            [turn(calls=[filed_note(2, again.say, asked_change=1)]), turn(says="On it.")],
            [turn(says="Here is the closer shot.")],
        ],
    )

    assert record["error"] == ""
    assert [len(ask["runs"]) for ask in record["asks"]] == [1, 1]
    results = [ask["tool_calls"][0]["result"] for ask in record["asks"]]
    assert description in results[0] and "try 1" in results[0].lower()
    assert description in results[1] and "try 2" in results[1].lower()
    producer_handoffs = [str(handoff).lower() for handoff in handoffs("produce")]
    assert any(
        "background step finished" in handoff and "try 1" in handoff
        for handoff in producer_handoffs
    )
    assert "background step finished" in producer_handoffs[-1]
    assert "try 2" in producer_handoffs[-1]

    first_handoff, second_handoff = (
        next(handoff for handoff in producer_handoffs if ask.say.lower() in handoff)
        for ask in (first, again)
    )
    assert "asked change #1" not in first_handoff
    assert "background step finished" in second_handoff

    (change,) = asked_changes.summary(Session.objects.get())
    assert change["number"] == 1 and change["description"] == description
    (scene,) = change["scenes"]
    assert scene["tries"] == 2
    assert [ask["message"] for ask in scene["asks"]] == [first.say, again.say]
    assert all(ask["tried"] for ask in scene["asks"])
    first_run, second_run = [ask["runs"][0] for ask in scene["asks"]]
    assert first_run["outcome"] == "gave_up" and first_run["try_number"] == 1
    assert second_run["outcome"] == "passed" and second_run["try_number"] == 2
    assert 0 < first_run["cost_usd"] < second_run["cost_usd"]


def test_bad_filings_open_no_runs_and_unknown_number_lists_filed_changes(
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Show the brush at an angle.", "passes", "The angle is visible.")
    again = OwnerMessage("Put it on a table.", "passes", "A table is visible.")
    description = "Show the brush at an angle"
    record = playing(
        tiny("healthy-01-brush", first, again),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=description)]),
                turn(says="On it."),
            ],
            [turn(says="Here it is.")],
            [
                turn(calls=[filed_note(2, again.say)]),
                turn(calls=[filed_note(2, again.say, asked_change=1, new_asked_change=again.say)]),
                turn(calls=[filed_note(2, again.say, asked_change=999)]),
                turn(says="I haven't made that change yet."),
            ],
        ],
    )

    assert [len(ask["runs"]) for ask in record["asks"]] == [1, 0]
    missing, both, unknown = [call["result"] for call in record["asks"][1]["tool_calls"]]
    assert "refused" in missing and "refused" in both and "refused" in unknown
    assert "999" in unknown and "#1" in unknown and description in unknown


def test_a_technical_step_failure_without_a_clip_does_not_use_a_try(
    fake_model: FakeModel,
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Show the product lying flat.", "passes", "It is lying flat.")
    again = OwnerMessage("Please try it again.", "passes", "It is lying flat.")
    description = "Show the product lying flat"

    def fail_the_scene_agent() -> Turn:
        raise RuntimeError("the worker lost its scene model connection")

    def start_the_failed_attempt() -> Turn:
        fake_model.respond("scene_agent", fail_the_scene_agent)
        return turn(calls=[filed_note(2, first.say, new_asked_change=description)])

    record = playing(
        tiny("healthy-01-brush", first, again),
        [
            [start_the_failed_attempt, turn(says="On it.")],
            [turn(says="The step broke.")],
            [turn(calls=[filed_note(2, again.say, asked_change=1)]), turn(says="On it.")],
            [turn(says="Here is the flat shot.")],
        ],
    )

    assert record["error"] == ""
    assert len(record["asks"][0]["runs"]) == 1
    assert "try 1" in record["asks"][1]["tool_calls"][0]["result"]
    assert any(
        "background step failed" in handoff and asked_changes.DID_NOT_COUNT.lower() in handoff
        for handoff in (str(handoff).lower() for handoff in handoffs("produce"))
    )
    scene = asked_changes.summary(Session.objects.get())[0]["scenes"][0]
    assert scene["tries"] == 1
    failed, passed = scene["asks"]
    assert not failed["tried"]
    assert failed["runs"][0]["outcome"] == "failed"
    assert failed["runs"][0]["try_number"] is None
    assert passed["tried"]
    assert passed["runs"][0]["outcome"] == "passed"
    assert passed["runs"][0]["try_number"] == 1


def test_a_note_replaced_before_filming_does_not_use_a_try(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
) -> None:
    first = OwnerMessage("Show the brush closer.", "passes", "It is closer.")
    again = OwnerMessage("Move much closer than that.", "passes", "It fills the frame.")
    case = tiny("healthy-01-brush", first, again)
    fake_the_rest(fake_model, Script(case, now=-1))
    make_the_ad(fake_model, say, steps, serve_shop(httpserver, case.shop), case.shop)

    fake_model.respond(
        "produce",
        turn(calls=[filed_note(2, first.say, new_asked_change=first.say)]),
        turn(says="On it."),
    )
    say(first.say)
    assert len(steps.held) == 1

    fake_model.respond(
        "produce",
        turn(calls=[filed_note(2, again.say, asked_change=1)]),
        turn(says="Moving closer."),
        turn(says="Here is the new shot."),
    )
    say(again.say)
    steps.run_held()

    assert asked_changes.DID_NOT_COUNT in results_of("pass_note")[-1]
    scene = asked_changes.summary(Session.objects.get())[0]["scenes"][0]
    assert scene["tries"] == 1
    assert [ask["tried"] for ask in scene["asks"]] == [False, True]
    assert scene["asks"][0]["runs"][0]["outcome"] == "superseded"
    assert scene["asks"][1]["runs"][0]["try_number"] == 1


def test_a_reordered_scene_keeps_its_tries_and_a_scene_number_does_not(
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Show the brush closer.", "passes", "It is closer.")
    move = OwnerMessage("Switch the first and second scenes.", "passes", "")
    again = OwnerMessage("Please get closer in the first scene.", "passes", "It fills the frame.")
    other = OwnerMessage("Also show it closer in the second scene.", "passes", "It is closer.")
    record = playing(
        tiny("healthy-01-brush", first, move, again, other),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=first.say)]),
                turn(says="On it."),
            ],
            [turn(says="Here it is.")],
            [
                turn(calls=[("reorder_scenes", {"order": [2, 1, 3], "owner_asked": True})]),
                turn(says="Reordered."),
            ],
            [turn(calls=[filed_note(1, again.say, asked_change=1)]), turn(says="On it.")],
            [turn(says="Here it is.")],
            [turn(calls=[filed_note(2, other.say, asked_change=1)]), turn(says="On it.")],
            [turn(says="Here it is.")],
        ],
    )

    assert record["error"] == ""
    assert [len(ask["runs"]) for ask in record["asks"]] == [1, 0, 1, 1]
    assert "try 2" in record["asks"][2]["tool_calls"][0]["result"]
    assert "try 1" in record["asks"][3]["tool_calls"][0]["result"]
    scenes = asked_changes.summary(Session.objects.get())[0]["scenes"]
    assert [(scene["scene"], scene["tries"]) for scene in scenes] == [(1, 2), (2, 1)]


def test_a_superseded_render_that_finishes_late_does_not_take_the_last_try(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
    settings: SettingsWrapper,
) -> None:
    settings.ASKED_CHANGE_TRY_LIMIT = 1
    first = OwnerMessage("Show the brush closer.", "passes", "It is closer.")
    again = OwnerMessage("Move much closer than that.", "passes", "It fills the frame.")
    case = tiny("healthy-01-brush", first, again)
    fake_the_rest(fake_model, Script(case, now=-1))
    make_the_ad(fake_model, say, steps, serve_shop(httpserver, case.shop), case.shop)
    existing_films = len(results_of("film"))

    def replace_the_note_while_the_camera_is_working() -> dict[str, str]:
        fake_model.respond(
            "produce",
            turn(calls=[filed_note(2, again.say, asked_change=1)]),
            turn(says="Moving closer."),
        )
        say(again.say)
        return {"state": "working"}

    fake_model.respond("collect_clip", replace_the_note_while_the_camera_is_working)
    fake_model.respond(
        "produce",
        turn(calls=[filed_note(2, first.say, new_asked_change=first.say)]),
        turn(says="On it."),
    )
    say(first.say)
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    assert len(results_of("film")) == existing_films + 2
    scene = asked_changes.summary(Session.objects.get())[0]["scenes"][0]
    replaced, kept = scene["asks"]
    assert scene["tries"] == 1
    assert (replaced["runs"][0]["outcome"], replaced["runs"][0]["try_number"]) == (
        "superseded",
        None,
    )
    assert kept["runs"][0]["try_number"] == 1


def test_a_note_answers_the_message_the_producer_had_read_before_its_turn(
    say: Callable[..., None],
    playing: Playing,  # noqa: F811
) -> None:
    first = OwnerMessage("Move closer to the product.", "passes", "The product is closer.")
    later = "Keep the original music."

    def note_from_the_earlier_turn() -> Turn:
        say(later)
        return turn(calls=[filed_note(2, first.say, new_asked_change=first.say)])

    record = playing(
        tiny("healthy-01-brush", first),
        [
            [note_from_the_earlier_turn, turn(says="I'll keep the music.")],
            [turn(says="Here is the new shot.")],
        ],
    )

    assert record["error"] == ""
    assert len(record["asks"][0]["runs"]) == 1
    captured = asked_changes.summary(Session.objects.get())[0]["scenes"][0]["asks"]
    assert [ask["message"] for ask in captured] == [first.say]
