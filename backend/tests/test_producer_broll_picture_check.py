"""A B-roll scene's starting picture is judged before any clip is paid for, driven through
the chat: a judge shown the picture alone looks for objects that can't exist, asked up to
3 times and failing it on 2 fails. One that fails is drawn again from prompts rewritten to
fix what the judge found, at most twice; when the third picture fails too, the run stops
and the chat shows the three pictures and why each failed (Julian, 2026-10-10 21:48: "the
best to crash the run"; the judge tested in /mnt/project-files/evals/picture-judge/). The
producer's model and the scene models are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge.retry import OutsideServiceDown
from agents import tasks
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job, SceneStep

from .conftest import PICTURE_OK, HeldSteps, WorkerStopped, chat, handoffs, paid_for
from .test_producer_broll import (
    BROLL_CHOICE,
    TALKING_CHOICE,
    calling,
    checked,  # noqa: F401 (a fixture)
    instructed,  # noqa: F401 (a fixture)
    picture_of_scene_2,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]


def impossible(problem: str) -> dict[str, Any]:
    """The judge's answer for a picture with an object that can't exist."""
    return {
        "objects": [
            {
                "name": "mug",
                "side_facing_camera": "the front",
                "parts_seen": "a handle on each side, one fused into the rim",
                "a_real_one_could_look_like_this": False,
                "why": "A handle can't grow out of the rim.",
            }
        ],
        "verdict": "fail",
        "problem": problem,
    }


FUSED_HANDLE = "The mug's handle is fused into its rim; a real handle joins the side."
TWO_SPOUTS = "The mug has a spout on both sides; a real mug has none."

# The prompts written again to fix it.
FIXED_CHOICE: dict[str, Any] = {
    **BROLL_CHOICE,
    "prompt": "The mug upright on a workbench, its handle rising straight from its side.",
}


def pictures_drawn() -> list[str]:
    """The file of each starting picture paid for, oldest first."""
    calls = ModelCall.objects.filter(purpose="make_starting_picture").order_by("id")
    return [call.output["file"] for call in calls if call.output is not None]


def test_a_picture_that_passes_is_judged_beside_the_shop_photo_it_was_drawn_from(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    picture = step.produced.get()
    # Two passes settle it, so the third ask isn't made.
    assert paid_for()[-4:] == [
        "choose_broll_picture",
        "make_starting_picture",
        "check_starting_picture",
        "check_starting_picture",
    ]
    # The picture first, then the one shop photo it was drawn from, for how the product is
    # built (Julian 23:05: the phone was caught 5 of 5 with it, 3 of 5 without). No prompt
    # or line: a judge told what to expect tends to see it.
    photo = Job.objects.get().photos.get(position=BROLL_CHOICE["photo"])
    for check in ModelCall.objects.filter(purpose="check_starting_picture"):
        assert check.images == [
            {"label": "The picture to judge:", "key": picture.file},
            {"label": "The shop photo:", "key": photo.file},
        ]
    assert [ask["ask"] for ask in handoffs("check_starting_picture")] == [1, 2]
    assert step.status == "finished"


def test_one_fail_in_three_asks_passes_the_picture(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", impossible(FUSED_HANDLE), PICTURE_OK, PICTURE_OK)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert len(pictures_drawn()) == 1
    assert paid_for().count("check_starting_picture") == 3
    assert step.status == "finished"


def test_a_picture_that_fails_twice_is_drawn_again_from_prompts_written_to_fix_it(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_starting_picture", impossible(FUSED_HANDLE), PICTURE_OK, impossible(TWO_SPOUTS)
    )

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    first, second = handoffs("make_starting_picture")
    assert (first["prompt"], second["prompt"]) == (BROLL_CHOICE["prompt"], FIXED_CHOICE["prompt"])
    # The model writing the prompts again is told each problem found, and shown the picture.
    _, rewrite = handoffs("choose_broll_picture")
    assert rewrite["redo"] == {
        "picture_prompt": BROLL_CHOICE["prompt"],
        "video_prompt": BROLL_CHOICE["motion_prompt"],
        "problems": [FUSED_HANDLE, TWO_SPOUTS],
    }
    first_picture = pictures_drawn()[0]
    rewritten = ModelCall.objects.filter(purpose="choose_broll_picture").last()
    assert rewritten is not None
    assert rewritten.images[-1] == {"label": "The last picture", "key": first_picture}
    told_first, told_again = instructed["choose_broll_picture"]
    assert "failed its check" in told_again and "failed its check" not in told_first
    # The picture kept is the one that passed, with the prompts it was drawn from.
    assert step.produced.get().file == pictures_drawn()[1]
    assert step.prompt == FIXED_CHOICE["prompt"]


def test_two_fails_settle_a_picture_without_a_third_ask(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", impossible(FUSED_HANDLE), impossible(TWO_SPOUTS))

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    first, second = pictures_drawn()
    # The failed picture was asked about twice, and the one drawn to fix it twice.
    assert paid_for().count("check_starting_picture") == 4
    asked = [
        (check.images[0]["key"], check.handoff["ask"])
        for check in ModelCall.objects.filter(purpose="check_starting_picture").order_by("id")
    ]
    assert asked == [(first, 1), (first, 2), (second, 1), (second, 2)]
    assert step.produced.get().file == second


def test_a_picture_that_fails_three_times_stops_the_run_and_shows_each_picture_and_why(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)
    turns_before = paid_for().count("produce")

    steps.run_held()

    step = SceneStep.objects.get(kind="starting_picture", scene__number=2)
    assert step.status == "stopped"
    assert not step.produced.exists()
    drawn = pictures_drawn()
    assert len(drawn) == 3
    # Two fails settle each picture, so none is asked about a third time.
    assert paid_for().count("check_starting_picture") == 6
    # The chat shows the three pictures and what the judge found in each.
    notice = Message.objects.get(role="notice", text__startswith="Scene 2")
    assert [a.file for a in notice.attachments.all()] == drawn
    assert all(a.kind == Attachment.Kind.PICTURE for a in notice.attachments.all())
    for number in (1, 2, 3):
        assert f"Picture {number}: {FUSED_HANDLE}" in notice.text
    # It also tells the chat scene 2's picture is no longer being made, with nothing made.
    (said,) = [m for m in api.get(f"/api/sessions/{session_id}/messages/").json() if m["step"]][1:]
    assert (said["seq"], said["step"]) == (
        notice.seq,
        {"scene": 2, "kind": "starting_picture", "state": "not_made"},
    )
    # The producer takes no turn to carry on: the run is stopped and the chat says so.
    assert paid_for().count("produce") == turns_before
    role, said = chat(api, session_id)[-1]
    assert role == "agent" and said.startswith("I had to stop: scene 2's starting picture")


def test_a_step_finishing_after_the_run_stopped_doesnt_start_it_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    calling(
        fake_model,
        say,
        ("make_starting_picture", {"scene": 2, "note": None}),
        ("make_starting_picture", {"scene": 1, "note": None}),
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    turns_before = paid_for().count("produce")

    steps.run_held()

    # Scene 1's picture was made after scene 2's stopped the run; nobody spoke since.
    assert SceneStep.objects.get(kind="starting_picture", scene__number=1).status == "finished"
    assert paid_for().count("produce") == turns_before
    assert Message.objects.filter(role="agent", text__startswith="I had to stop").count() == 1


def test_a_stop_during_the_producers_turn_refuses_the_rest_of_its_calls(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], monkeypatch: Any
) -> None:
    # Scene 2's picture runs as soon as it is started, so the run stops while the producer
    # is still settling the calls of the same turn: scene 1's picture is then not paid for.
    monkeypatch.setattr(tasks.run_scene_step, "delay", tasks.run_scene_step)
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)

    calling(
        fake_model,
        say,
        ("make_starting_picture", {"scene": 2, "note": None}),
        ("make_starting_picture", {"scene": 1, "note": None}),
    )

    assert "choose_starting_picture" not in paid_for()
    assert not SceneStep.objects.filter(scene__number=1).exists()
    assert Message.objects.filter(role="agent", text__startswith="I had to stop").count() == 1


def test_after_the_run_stopped_a_message_from_the_user_carries_on(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", *[impossible(FUSED_HANDLE)] * 6)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE, FIXED_CHOICE)
    steps.run_held()
    fake_model.respond("produce", turn(says="Let's try again."))

    say("Try again")

    assert Message.objects.filter(role="agent").last().text == "Let's try again."  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "unreadable",
    [
        pytest.param(BlockedBySafetyFilter("content_filter"), id="blocked by the safety filter"),
        # A fail that doesn't say why can't be read.
        pytest.param({**impossible(FUSED_HANDLE), "problem": " "}, id="a fail with no problem"),
    ],
)
def test_a_judge_answer_that_cant_be_read_counts_as_not_checked(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], unreadable: Any
) -> None:
    # The two readable answers settle it: one fail, one pass, and the picture is paid for.
    fake_model.respond("check_starting_picture", unreadable, impossible(FUSED_HANDLE), PICTURE_OK)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert step.status == "finished"
    assert len(pictures_drawn()) == 1


def test_a_picture_step_run_again_after_the_worker_stopped_pays_for_no_picture_twice(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, FIXED_CHOICE)
    # The first picture fails its check; the worker stops as the second is checked.
    fake_model.respond(
        "check_starting_picture",
        impossible(FUSED_HANDLE),
        impossible(FUSED_HANDLE),
        WorkerStopped(),
    )
    with pytest.raises(WorkerStopped):
        steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert paid_for().count("make_starting_picture") == 2
    assert paid_for().count("choose_broll_picture") == 2
    # The first picture's two asks are answered from their record; the second's are paid for
    # as it is checked again, its stopped ask having been recorded as nothing.
    assert paid_for().count("check_starting_picture") == 4
    assert [ask["ask"] for ask in handoffs("check_starting_picture")] == [1, 2, 1, 2]
    step = SceneStep.objects.get(pk=step_id)
    assert step.produced.get().file == pictures_drawn()[1]


def test_a_picture_whose_judge_service_is_down_is_kept_and_not_paid_for_again(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_starting_picture", *[OutsideServiceDown("azure answered 503")] * 3)

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE)

    assert step.status == "finished"
    assert step.produced.get().file == pictures_drawn()[0]
    assert paid_for().count("make_starting_picture") == 1


def test_a_picture_is_judged_even_with_quality_checks_switched_off(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], settings: Any
) -> None:
    # Julian 2026-10-15 14:53: the picture check always runs; nothing switches it off.
    settings.QUALITY_CHECKS = False
    fake_model.respond("check_starting_picture", impossible(FUSED_HANDLE), impossible(TWO_SPOUTS))

    step = picture_of_scene_2(fake_model, steps, say, BROLL_CHOICE, FIXED_CHOICE)

    assert step.status == "finished"
    # The first picture's two fails send it back to be drawn again; the second passes.
    assert paid_for().count("check_starting_picture") == 4
    assert len(pictures_drawn()) == 2
    assert step.produced.get().file == pictures_drawn()[1]
