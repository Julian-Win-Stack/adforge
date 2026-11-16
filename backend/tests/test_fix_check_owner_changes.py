"""The "was the change made" fix check fails a try only for a change the shop owner asked for."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ModelRequest
from jobs.models import ProducedItem

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
    # Only the remake is described (#673): the first clip skips the description.
    assert paid_for().count("check_clip_motion") == 1
    first, remake = ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("pk")
    assert first.ruling is not None and first.ruling["answers"]["motion"] == []
    assert remake.ruling is not None and len(remake.ruling["answers"]["motion"]) == 1
    told = results_of("film")[-1]
    assert ends in told
    assert ("Whether the owner's change is made: no." in told) is owner_asked
    assert paid_for().count("make_broll_clip") == 2


def test_a_volunteered_photo_note_keeps_the_owner_change_check(
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
            "owner_asked": True,
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
    assert paid_for().count("make_broll_clip") == 2, told
    assert "opens a new run" in str(results_of("pass_note")), results_of("pass_note")
    assert "Whether the owner's change is made: no." in told[-1]
    assert "It fails." in told[-1]


def test_a_remake_the_judge_cant_describe_is_said_and_kept_on_its_other_checks(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    """A remake's description is the only one asked for (#673); when it can't be read, the
    owner-change check can't be told and a notice says so, while product and pass-through
    still decide the verdict."""
    from gateway.types import UnusableReply

    from .conftest import chat, recorded

    def motion(_: ModelRequest[Any]) -> dict[str, Any]:
        raise UnusableReply("unreadable", input_tokens=0, output_tokens=0)

    def references(_: ModelRequest[Any]) -> dict[str, Any]:
        raise RuntimeError("the first question broke")

    fake_model.answer_unscripted("check_clip_references", references)

    def note_during_render() -> dict[str, Any]:
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
        turn(calls=[("give_up", {"reason": "Done."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["Go on"]}),
    )
    fake_model.answer_unscripted("produce", lambda _: turn(says="The scene is ready."))
    steps.run_held()

    told = results_of("film")[-1]
    assert paid_for().count("make_broll_clip") == 2, results_of("film")
    assert "Whether the owner's change is made: it couldn't be told." in told
    # The first question went unanswered on both clips: each notice names only the checks
    # that clip ran in its place.
    unanswered = "The clip judge couldn't ask whether scene 2's references show enough"
    said = [text for _, text in recorded(api, session_id) if text.startswith(unanswered)]
    assert [text.split(", ")[-1] for text in said] == [
        "so it ran the product check anyway.",
        "so it ran the product check anyway and the owner-change check.",
    ]
    unavailable = (
        "The clip judge couldn't describe scene 2's clip or check any requested change. "
        "The product and pass-through checks still decide its verdict."
    )
    assert ("problem", unavailable) in recorded(api, session_id)
    assert unavailable not in [text for _, text in chat(api, session_id)]
