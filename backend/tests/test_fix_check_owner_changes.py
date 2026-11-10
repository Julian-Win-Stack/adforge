"""The "was the change made" fix check fails a try only for a change the shop owner asked for."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ModelRequest

from .conftest import CLIP_FIXED, CLIP_MOTION_OK, HeldSteps, facts_ok, paid_for, results_of
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
LINE = "This handmade mug holds warm tea for your cosy mornings every single day."


@pytest.mark.parametrize(
    ("owner_asked", "ends"),
    [(True, "It fails."), (False, "It passes.")],
    ids=["owner-asked change is gated", "producer re-plan is not gated"],
)
def test_a_not_fixed_verdict_fails_the_try_only_for_a_change_the_owner_asked_for(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    owner_asked: bool,
    ends: str,
) -> None:
    fix_questions: list[ModelRequest[Any]] = []

    def motion(request: ModelRequest[Any]) -> dict[str, Any]:
        if "owner_change" not in request.output.model_fields:
            return CLIP_MOTION_OK
        fix_questions.append(request)
        return {
            **CLIP_MOTION_OK,
            "owner_change": {
                **CLIP_FIXED,
                "verdict": "not fixed",
                "still_wrong": "The mug is still poured from the left.",
            },
        }

    note = {
        "scene": 2,
        "instruction": "Pour from the right this time.",
        "quotes": ["pour from the right"],
    }
    if owner_asked:
        note["owner_asked"] = True

    def note_during_render() -> dict[str, Any]:
        fake_model.respond(
            "produce",
            turn(calls=[("pass_note", {"scenes": [note]})]),
            turn(says="I passed it on."),
        )
        say("pour from the right")
        return {"state": "working"}

    fake_model.answer_unscripted("check_clip_motion", motion)
    fake_model.respond("fact_check", facts_ok(2), facts_ok(2))
    fake_model.respond("transcribe_line", {"text": LINE}, {"text": LINE})
    fake_model.respond("collect_clip", note_during_render)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=LINE)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The pour can't be made from the right."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    assert len(fix_questions) == 1, results_of("film")
    told = results_of("film")[-1]
    assert ends in told
    assert ("Whether the owner's change is made: no." in told) is owner_asked
    assert paid_for().count("make_broll_clip") == 2
