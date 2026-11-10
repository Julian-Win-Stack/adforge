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


def test_the_owners_answer_to_a_question_keeps_the_runs_owner_mark(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import picture

    def motion(request: ModelRequest[Any]) -> dict[str, Any]:
        if "owner_change" not in request.output.model_fields:
            return CLIP_MOTION_OK
        return {
            **CLIP_MOTION_OK,
            "owner_change": {
                **CLIP_FIXED,
                "verdict": "not fixed",
                "still_wrong": "The mug is still poured from the left.",
            },
        }

    def owner_note_during_render() -> dict[str, Any]:
        note = {
            "scene": 2,
            "instruction": "Pour from the right this time.",
            "quotes": ["pour from the right"],
            "owner_asked": True,
        }
        fake_model.respond(
            "produce",
            turn(calls=[("pass_note", {"scenes": [note]})]),
            turn(says="I passed it on."),
        )
        say("pour from the right")
        return {"state": "working"}

    def answer_then_film() -> Any:
        answer = {
            "scene": 2,
            "instruction": "Use the owner's photo of the right side.",
            "quotes": ["Here is its right side"],
        }
        fake_model.respond(
            "produce",
            turn(calls=[("pass_note", {"scenes": [answer]})]),
            turn(says="Thanks."),
        )
        say("Here is its right side.", ("right.png", picture(40, 30, (20, 160, 40))))
        return turn(calls=[("film", FILM)])

    fake_model.answer_unscripted("check_clip_motion", motion)
    fake_model.answer_unscripted("fact_check", lambda _: facts_ok(2))
    fake_model.answer_unscripted("transcribe_line", lambda _: {"text": LINE})
    fake_model.respond("collect_clip", owner_note_during_render)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=LINE)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(
            calls=[("ask_owner", {"question": "Could you send its right side?", "wants": "photo"})]
        ),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        answer_then_film,
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

    told = results_of("film")
    assert paid_for().count("make_broll_clip") == 3, told
    assert "Handed the owner's answer" in str(results_of("pass_note")), results_of("pass_note")
    assert "Whether the owner's change is made: no." in told[-1]
    assert "It fails." in told[-1]
