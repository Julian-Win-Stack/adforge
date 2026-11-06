"""A clip the judge failed is fixed with a new picture, not new words (#408), through the chat,
faking only outside services."""

from collections.abc import Callable
from typing import Any

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
DIFFERENT_ACTION = (
    "Consider a different action; a changed action must still show the scene's proof."
)


def refusals() -> list[dict[str, Any]]:
    """Every same-picture refusal recorded for the developer."""
    from jobs.models import Job

    return [
        warning
        for warning in Job.objects.get().warnings
        if warning.get("kind") == "same_picture_refused"
    ]


def film_pictures() -> list[list[str]]:
    """The labels of the pictures each film call handed back, oldest first."""
    from agents.models import ToolCall

    return [
        [picture["label"] for picture in pictures]
        for pictures in ToolCall.objects.filter(tool="film")
        .order_by("created_at", "id")
        .values_list("pictures", flat=True)
    ]


def test_a_judge_failed_picture_is_refused_and_a_new_starting_picture_is_filmed(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import AgentFixTry

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "camera": "The phone tracks the hand"}})]),
        turn(calls=[DRAW]),
        turn(calls=[("film", FILM)]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    failed, refused, passed = results_of("film")
    assert "It fails." in failed
    assert "so it is not filmed again" in refused
    assert "make_end_picture" in refused
    assert "It passes." in passed
    assert len(handoffs("make_broll_clip")) == 2
    first, second = AgentFixTry.objects.order_by("number")
    assert first.picture_id != second.picture_id
    # The failed try's own starting picture comes back with its frames.
    assert any(f"starting picture {first.picture_id}" in label for label in film_pictures()[0])
    assert not any("starting picture" in label for label in film_pictures()[2])
    [notice] = refusals()
    assert "Scene 2" in notice["text"]
    assert f"starting picture {first.picture_id}" in notice["text"]
    assert "The teapot rises" in notice["text"]


def test_an_end_picture_on_the_same_start_is_filmed_after_a_judge_fail(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
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
    assert not refusals()


@pytest.mark.parametrize("not_a_judge_fail", ["unsure", "not checked", "words", "owner change"])
def test_a_clip_the_judge_did_not_fail_lets_the_same_picture_be_filmed_again(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    not_a_judge_fail: str,
) -> None:
    from gateway.types import UnusableReply
    from jobs.models import AgentFixTry

    from .conftest import CLIP_MOTION_OK

    note = not_a_judge_fail == "owner change"
    if note:
        # The fix check only runs on a note's run: the scene is made once first.
        fake_model.respond("scene_agent", turn(calls=[DRAW]), turn(calls=[("film", FILM)]))
        calling(fake_model, say, START)
        run(fake_model, steps)
        fake_model.respond(
            "check_clip_motion",
            {
                **CLIP_MOTION_OK,
                "owner_change": {
                    "old_shows": "The handle is hidden.",
                    "new_shows": "The handle is still hidden.",
                    "verdict": "not fixed",
                    "still_wrong": "The handle is still hidden.",
                },
            },
        )
    elif not_a_judge_fail == "words":
        fake_model.respond("transcribe_line", {"text": "This is a completely different claim."})
        fake_model.respond("check_words", {"verdict": "fail", "reason": "The claim changed."})
    elif not_a_judge_fail == "unsure":
        fake_model.respond("check_clip_product", product("unsure"))
    else:
        fake_model.respond(
            "check_clip_product", UnusableReply("no answer", input_tokens=1, output_tokens=1)
        )
    fake_model.respond(
        "scene_agent",
        *([] if note else [turn(calls=[DRAW])]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Enough tries."})]),
    )
    if note:
        calling(
            fake_model,
            say,
            (
                "pass_note",
                {"scenes": [{"scene": 2, "instruction": "Show the handle.", "quotes": ["go on"]}]},
            ),
        )
    else:
        calling(fake_model, say, START)
    run(fake_model, steps)

    tries = list(AgentFixTry.objects.order_by("created_at", "pk"))
    assert len({each.picture_id for each in tries}) == 1
    assert all("so it is not filmed again" not in result for result in results_of("film")), (
        results_of("film")
    )
    assert not refusals()
    first = tries[-2] if note else tries[0]
    verdict = (first.clip.ruling or {}).get("verdict") if first.clip else None
    if not_a_judge_fail in ("words", "owner change"):
        assert not first.passed
    else:
        assert verdict == {"unsure": "unsure", "not checked": "not_checked"}[not_a_judge_fail]


def test_a_refusal_with_no_picture_slot_left_ends_the_run_by_the_cap(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_agent.lifecycle import ENDED_BY_CAP
    from jobs.models import AgentFix

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        *(turn(calls=[DRAW]) for _ in range(6)),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(fake_model, say, START)
    run(fake_model, steps)

    failed, ended = results_of("film")
    assert "It fails." in failed
    assert "no picture slot is left" in ended
    assert len(handoffs("make_broll_clip")) == 1
    fixing = AgentFix.objects.get()
    assert fixing.ended_by_cap
    assert fixing.gave_up == ENDED_BY_CAP
    assert fixing.kept_id is not None
    [notice] = refusals()
    assert "ended by the cap" in notice["text"]
    from jobs.models import Job

    assert any(warning["kind"] == "tries_to_pick" for warning in Job.objects.get().warnings)


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
