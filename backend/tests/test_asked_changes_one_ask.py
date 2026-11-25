"""One owner's message can start one try on each scene."""

from collections.abc import Callable

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from agents import asked_changes
from chat.models import Session
from evals.repeated_asks.cases import OwnerMessage
from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps, handoffs, results_of
from .repeated_asks_play import Script, fake_the_rest, make_the_ad, serve_shop
from .test_asked_changes import filed_note
from .test_card_versions import cards
from .test_producer_auto_fix import failing
from .test_repeated_asks_eval import Playing, note, playing, tiny  # noqa: F401 (fixture)
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM

pytestmark = pytest.mark.django_db(transaction=True)


def test_only_the_owner_can_ask_for_another_try_on_the_scene(playing: Playing) -> None:  # noqa: F811
    first = OwnerMessage(
        "Film from closer up and hold the shot longer.",
        "gives_up",
        "Still too wide.",
        "The shot is too wide.",
    )
    next_ask = OwnerMessage("Try a wide shot from the side.", "passes", "A side view.")
    record = playing(
        tiny("healthy-01-brush", first, next_ask),
        [
            [
                turn(calls=[filed_note(2, first.say, new_asked_change=first.say)]),
                turn(says="I’ll make one shot with both changes."),
            ],
            [
                turn(
                    calls=[
                        (
                            "start_scene_agent",
                            {
                                "scene": 2,
                                "instruction": "Try another angle.",
                                "quotes": [first.say],
                            },
                        ),
                        note(2, first.say, owner_asked=False),
                        filed_note(2, first.say, asked_change=1),
                        filed_note(2, first.say, new_asked_change="Frame the product differently."),
                    ]
                ),
                turn(says="I kept the earlier clip. The closer shot is still too wide."),
            ],
            [
                turn(calls=[filed_note(2, next_ask.say, new_asked_change=next_ask.say)]),
                turn(says="I’ll film it from the side."),
            ],
            [turn(says="Here is the side view.")],
        ],
    )

    assert record["error"] == ""
    first_ask, second_ask = record["asks"]
    assert [run["origin"] for run in first_ask["runs"]] == ["owner"]
    assert [run["origin"] for run in second_ask["runs"]] == ["owner"]
    for call in first_ask["tool_calls"][1:]:
        assert "refused" in call["result"].lower()
        assert "clip" in call["result"].lower()
        assert "cost" in call["result"].lower()
    report = next(
        event["text"].lower()
        for handoff in handoffs("produce")
        for event in handoff["conversation"]
        if event["kind"] == "step_finished" and "Asked change #1" in event["text"]
    )
    assert "one try" in report
    assert "extra idea" not in report
    assert "talking recovery" not in report


def test_a_failed_remake_leaves_the_earlier_clip_in_the_ad(
    playing: Playing,  # noqa: F811
    fake_model: FakeModel,
    api: APIClient,
    session_id: str,
) -> None:
    ask = OwnerMessage("Film a closer view.", "gives_up", "Still too wide.", "Too wide.")
    earlier_url = ""

    def film_a_clip_that_does_not_pass() -> Turn:
        nonlocal earlier_url
        earlier_url = next(clip["url"] for clip in cards(api, session_id)[2] if clip["in_ad"])
        fake_model.answer_unscripted("check_clip_product", lambda _: failing("Too wide."))
        fake_model.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
            turn(calls=[("film", FILM)]),
            turn(calls=[("give_up", {"reason": ask.reason})]),
        )
        return turn(calls=[filed_note(2, ask.say, new_asked_change=ask.say)])

    record = playing(
        tiny("healthy-01-brush", ask),
        [
            [film_a_clip_that_does_not_pass, turn(says="On it.")],
            [turn(says="The new clip is still too wide. I kept the earlier clip.")],
        ],
    )

    assert record["error"] == ""
    (run,) = record["asks"][0]["runs"]
    assert run["clips"] == 1
    assert "gave up" in run["ended"] or run["ended"] == ask.reason
    (kept,) = [clip for clip in cards(api, session_id)[2] if clip["in_ad"]]
    assert kept["url"] == earlier_url


def test_a_technical_failure_leaves_the_same_owner_message_its_try(
    playing: Playing,  # noqa: F811
    fake_model: FakeModel,
) -> None:
    ask = OwnerMessage("Film a wider view.", "passes", "A wider view.")

    def fail_the_worker() -> Turn:
        raise RuntimeError("The camera worker lost its model connection.")

    def open_the_first_run() -> Turn:
        fake_model.respond("scene_agent", fail_the_worker)
        return turn(calls=[filed_note(2, ask.say, new_asked_change=ask.say)])

    record = playing(
        tiny("healthy-01-brush", ask),
        [
            [open_the_first_run, turn(says="On it.")],
            [turn(calls=[filed_note(2, ask.say, asked_change=1)]), turn(says="Trying again.")],
            [turn(says="Here is the wider view.")],
        ],
    )

    assert record["error"] == ""
    (change,) = asked_changes.summary(Session.objects.get())
    (scene,) = change["scenes"]
    (message,) = scene["asks"]
    assert scene["tries"] == 1 and message["tried"]
    assert [(run["outcome"], run["try_number"]) for run in message["runs"]] == [
        ("failed", None),
        ("passed", 1),
    ]


def test_a_failed_replacement_restores_the_line_and_clip_from_before_the_remake(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    say: Callable[..., None],
    steps: HeldSteps,
    api: APIClient,
    session_id: str,
) -> None:
    first = OwnerMessage("Film a closer view.", "gives_up", "Still too wide.", "Too wide.")
    again = OwnerMessage("Use a slow side view.", "gives_up", "Still too wide.", "Too wide.")
    case = tiny("healthy-01-brush", first, again)
    fake_the_rest(fake_model, Script(case, now=-1))
    make_the_ad(fake_model, say, steps, serve_shop(httpserver, case.shop), case.shop)
    (earlier,) = [clip for clip in cards(api, session_id)[2] if clip["in_ad"]]

    def replace_the_unfinished_note() -> Turn:
        result = results_of("change_scene")[-1]
        assert "line" in result and "shows" in result
        fake_model.respond(
            "produce",
            turn(calls=[filed_note(2, again.say, new_asked_change=again.say)]),
            turn(says="I'll use the side view."),
        )
        say(again.say)
        return turn(says="I was preparing the closer view.")

    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                change(
                    line="The brush turns on its side slowly while its shape stays in view.",
                    shows="The brush turns slowly on its side in the presenter's hands.",
                )
            ]
        ),
        replace_the_unfinished_note,
        turn(calls=[("give_up", {"reason": again.reason})]),
    )
    fake_model.respond(
        "produce",
        turn(calls=[filed_note(2, first.say, new_asked_change=first.say)]),
        turn(says="On it."),
    )
    say(first.say)
    fake_model.answer_unscripted("produce", lambda _: turn(says="I'll keep the earlier shot."))
    steps.run_held()

    changes = asked_changes.summary(Session.objects.get())
    assert [asked["description"] for asked in changes] == [first.say, again.say]
    assert changes[-1]["scenes"][0]["asks"][0]["runs"][0]["outcome"] == "gave_up"
    (kept,) = [clip for clip in cards(api, session_id)[2] if clip["in_ad"]]
    assert (kept["url"], kept["line"], kept["shows"]) == (
        earlier["url"],
        earlier["line"],
        earlier["shows"],
    )
