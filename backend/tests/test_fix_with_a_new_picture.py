"""A clip the judge failed is fixed with a new picture, not new words (#408), through the chat,
faking only outside services."""

import re
from collections.abc import Callable

import pytest

from agents.loop import REFUSED
from agents.scene_agent.lifecycle import CLIP_STEPS, LADDER_STEPS
from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, handoffs, results_of
from .test_clip_judge import product
from .test_producer_auto_fix import FLOATS, failing
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_end_pictures import END_PICTURE
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

START = ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]})
DRAW = ("draw_starting_picture", PICTURE_FORM)


def assert_clip_reminder(result: str, *, count: int, step: int) -> None:
    _, verdict, reminder, _ = result.rsplit("\n", 3)
    assert verdict == "It fails."
    step_words = LADDER_STEPS[step - 1].split(". ", 1)[1]
    assert reminder.endswith(step_words)
    assert re.findall(r"\b\d+\b", reminder.removesuffix(step_words)) == [str(count), str(step)]


def film_pictures() -> list[list[str]]:
    """The labels of the pictures each film call handed back, oldest first."""
    from agents.models import ToolCall

    return [
        [picture["label"] for picture in pictures]
        for pictures in ToolCall.objects.filter(tool="film")
        .order_by("created_at", "id")
        .values_list("pictures", flat=True)
    ]


def test_a_judge_failed_picture_can_be_filmed_again_as_the_agent_is_only_told_not_to(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # The same-picture refusal (#408) never fired in round 12: the scene agent is told not to
    # film a judge-failed pair again, and code no longer refuses it (Julian, Nov 9 2026, rules
    # review).
    from jobs.models import AgentFixTry, Job

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "camera": "The phone tracks the hand"}})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    failed, passed = results_of("film")
    assert "It fails." in failed
    assert passed.splitlines()[-2] == "It passes."
    first, second = AgentFixTry.objects.order_by("number")
    assert first.picture_id == second.picture_id
    # The failed try's own starting picture comes back with its frames.
    assert any(f"starting picture {first.picture_id}" in label for label in film_pictures()[0])
    [again] = [
        warning
        for warning in Job.objects.get().warnings
        if warning.get("kind") == "same_picture_filmed_again"
    ]
    assert f"Scene 2: starting picture {first.picture_id} is filmed again" in again["text"]
    assert "(try 1)" in again["text"]


def test_an_end_picture_on_the_same_start_is_filmed_after_a_judge_fail(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import Job

    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    failed, passed = results_of("film")
    assert "It fails." in failed
    assert "It passes." in passed
    clips = handoffs("make_broll_clip")
    assert len(clips) == 2
    assert not any(
        warning.get("kind") == "same_picture_filmed_again" for warning in Job.objects.get().warnings
    )


def test_the_clip_failure_count_restarts_when_a_note_opens_a_new_run(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Try later."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)
    fake_model.respond("check_clip_product", failing(FLOATS), failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Enough tries."})]),
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Try the pour again.", "quotes": ["go on"]}]},
        ),
    )
    run(fake_model, steps)

    # The count is per run: the note's run is told the fix order again at its first fail.
    filmed = results_of("film")
    assert len(filmed) == 3
    assert [CLIP_STEPS in result for result in filmed] == [True, True, False]
    assert_clip_reminder(filmed[0], count=1, step=3)
    assert_clip_reminder(filmed[1], count=1, step=3)
    assert_clip_reminder(filmed[2], count=2, step=4)


def test_each_judge_failed_clip_names_the_next_step_and_the_first_gets_the_full_fix_order(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import AgentFixTry

    fake_model.respond(
        "check_clip_product", failing(FLOATS), failing(FLOATS), failing(FLOATS), failing(FLOATS)
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("film", FILM)]),  # Refused before a starting picture exists.
        *(turn(calls=[call]) for _ in range(4) for call in (DRAW, ("film", FILM))),
        turn(calls=[("give_up", {"reason": "Enough tries."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    refused, *filmed = results_of("film")
    assert refused.startswith(REFUSED)
    assert len(refused.splitlines()) == 1
    assert len(filmed) == 4
    assert [CLIP_STEPS in result for result in filmed] == [True, False, False, False]
    for count, (result, step) in enumerate(zip(filmed, (3, 4, 5, 5), strict=True), start=1):
        assert_clip_reminder(result, count=count, step=step)
    first = filmed[0]
    for case in (
        "result where the product touched",
        "crop of the real label",
        "drifts or ends in the wrong place",
        "stop at a set point",
    ):
        assert case in first
    # Every failed try hands back its own starting picture.
    failed_tries = AgentFixTry.objects.order_by("number")
    for labels, each in zip(film_pictures()[1:], failed_tries, strict=True):
        assert sum(f"starting picture {each.picture_id}" in label for label in labels) == 1
