"""An owner note during H3 rendering reaches the same scene chat and its fix check."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import (
    CLIP_MOTION_OK,
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
    "change_fails_first, another_note", [(False, False), (True, False), (False, True)]
)
def test_note_during_render_is_read_after_collecting_once_and_checked_in_exact_words(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    change_fails_first: bool,
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
                                    "owner_change": True,
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
                                    "owner_change": True,
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

    if change_fails_first:
        fake_model.respond(
            "check_clip_motion",
            CLIP_MOTION_OK,
            {
                **CLIP_MOTION_OK,
                "owner_change": {
                    "old_shows": "The handle is hidden.",
                    "new_shows": "The handle stays hidden.",
                    "verdict": "not fixed",
                    "still_wrong": "The handle is still hidden.",
                },
            },
        )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        choose_second_film,
        *([turn(calls=[("film", FILM)])] if change_fails_first or another_note else []),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    count = 3 if change_fails_first or another_note else 2
    assert paid_for().count("make_broll_clip") == count, results_of("film")
    assert len(handoffs("collect_broll_clip")) == count
    requests = handoffs("scene_agent")
    assert "Show its handle" in str(requests[2])
    assert "Keep the handle in view." in str(requests[2])
    judged = handoffs("check_clip_motion")
    assert len(judged) == count, results_of("film")
    assert "producer_instruction" not in judged[0]
    assert judged[1]["producer_instruction"] == "Keep the handle in view."
    assert judged[1]["owner_quotes"] == ["Show its handle"]
    if another_note:
        assert judged[2]["producer_instruction"] == "Frame the rim closely."
        assert judged[2]["owner_quotes"] == ["Show its rim"]
    if change_fails_first:
        assert "The handle is still hidden." in str(requests[3])
    assert "passed" in str(handoffs("produce")[-1])
