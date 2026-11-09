"""A clip the judge failed is fixed with a new picture, not new words (#408), through the chat,
faking only outside services."""

from collections.abc import Callable

import pytest

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
FIX_ORDER = "Fix it by where the clip first goes wrong."
DIFFERENT_ACTION = "Consider a different action."


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
    from agents.scene_agent.instructions import INSTRUCTIONS
    from jobs.models import AgentFixTry, Job

    assert (
        "A clip the judge failed is never filmed again from the same starting picture and end "
        "picture" in " ".join(INSTRUCTIONS.split())
    )
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
    assert "It passes." in passed
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


def test_the_fail_notes_count_across_a_note_run_of_the_same_scene(
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

    filmed = results_of("film")
    assert len(filmed) == 3
    assert [FIX_ORDER in result for result in filmed] == [True, False, False]
    assert [DIFFERENT_ACTION in result for result in filmed] == [False, True, False]


def test_the_fix_order_comes_at_the_first_fail_and_a_different_action_at_the_second(
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
        *(turn(calls=[call]) for _ in range(4) for call in (DRAW, ("film", FILM))),
        turn(calls=[("give_up", {"reason": "Enough tries."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    filmed = results_of("film")
    assert len(filmed) == 4
    assert [FIX_ORDER in result for result in filmed] == [True, False, False, False]
    assert [DIFFERENT_ACTION in result for result in filmed] == [False, True, False, False]
    first = filmed[0]
    for case in (
        "result where the product touched",
        "crop of the real label",
        "drifts or ends in the wrong place",
        "stop at a set point",
    ):
        assert case in first
    # Every failed try hands back its own starting picture.
    for labels, each in zip(film_pictures(), AgentFixTry.objects.order_by("number"), strict=True):
        assert sum(f"starting picture {each.picture_id}" in label for label in labels) == 1


def test_a_word_only_fail_gets_no_fix_note_and_the_first_judge_fail_does(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import CLIP_PRODUCT_OK

    fake_model.respond("check_clip_product", CLIP_PRODUCT_OK, failing(FLOATS), failing(FLOATS))
    fake_model.respond("transcribe_line", {"text": "This is a completely different claim."})
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The claim changed."})
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        # A word-only fail leaves the same picture allowed.
        turn(calls=[("film", FILM)]),
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Enough tries."})]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    words, judged, again = results_of("film")
    assert "Word check: fail" in words and "It fails." in words
    assert [FIX_ORDER in result for result in (words, judged, again)] == [False, True, False]
    assert [DIFFERENT_ACTION in result for result in (words, judged, again)] == [
        False,
        False,
        True,
    ]
