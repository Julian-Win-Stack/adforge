"""An owner note during H3 rendering reaches the same scene chat and its usual clip checks."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import AgentFixTry

from .conftest import (
    CLIP_PRODUCT_OK,
    HeldSteps,
    handoffs,
    paid_for,
    results_of,
)
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "clip_fails_first, another_note", [(False, False), (True, False), (False, True)]
)
def test_note_during_render_is_read_after_collecting_once_with_the_owners_exact_words(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    clip_fails_first: bool,
    another_note: bool,
) -> None:
    def note_during_render() -> dict[str, Any]:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 2,
                                    "instruction": "Keep the handle in view.",
                                    "quotes": ["Show its handle"],
                                    "owner_asked": True,
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed it on."),
        )
        say("Show its handle")
        return {"state": "working"}

    def second_note_during_render() -> dict[str, Any]:
        fake_model.respond(
            "produce",
            turn(
                calls=[
                    (
                        "pass_note",
                        {
                            "scenes": [
                                {
                                    "scene": 2,
                                    "instruction": "Frame the rim closely.",
                                    "quotes": ["Show its rim"],
                                    "owner_asked": True,
                                }
                            ]
                        },
                    )
                ]
            ),
            turn(says="I passed that on too."),
        )
        say("Show its rim")
        return {"state": "working"}

    fake_model.respond("collect_clip", note_during_render)

    def choose_second_film() -> Any:
        if another_note:
            fake_model.respond("collect_clip", second_note_during_render)
        return turn(calls=[("film", FILM)])

    if clip_fails_first:
        fake_model.respond(
            "check_clip_product",
            CLIP_PRODUCT_OK,
            {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": "The mug's handle changes shape."},
        )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        choose_second_film,
        *([turn(calls=[("draw_starting_picture", PICTURE_FORM)])] if clip_fails_first else []),
        *([turn(calls=[("film", FILM)])] if clip_fails_first or another_note else []),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    count = 3 if clip_fails_first or another_note else 2
    assert paid_for().count("make_broll_clip") == count, results_of("film")
    assert len(handoffs("collect_broll_clip")) == count
    requests = handoffs("scene_agent")
    assert "Show its handle" in str(requests[2])
    assert "Keep the handle in view." in str(requests[2])
    assert "check_clip_motion" not in paid_for()
    assert len(handoffs("check_clip_product")) == count
    assert len(handoffs("check_clip_pass_through")) == count
    if another_note:
        assert "Frame the rim closely." in str(requests[-1])
        assert "Show its rim" in str(requests[-1])
    if clip_fails_first:
        assert "handle changes shape." in str(requests[3])
        assert "It fails." in results_of("film")[1]
        assert list(AgentFixTry.objects.order_by("pk").values_list("passed", flat=True)) == [
            True,
            False,
            True,
        ]
    assert not AgentFixTry.objects.filter(fix_check__isnull=False).exists()
    assert "passed" in str(handoffs("produce")[-1])
