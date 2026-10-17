"""A fix's new starting picture whose fix check fails is drawn again by itself, at most
fix_check.MOST_REDRAWS times, each redraw told what the check found still wrong, before the
shop owner is asked (Julian 2026-10-17 17:04). Each redraw has its own picture check and
its own fix check, against the picture the shop owner complained about. The scene models
are faked at the gateway, the clips are real tiny videos and ffmpeg runs for real; the
producer's model is never scripted."""

from typing import Any

import pytest

from agents import run_plan, tasks
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import fix_check
from jobs.models import FixCheck, FixPlanScene, SceneStep

from .conftest import PICTURE_OK, HeldSteps
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_fix_loop import HANDLE_SHOWING
from .test_producer_broll import TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
    status,
)
from .test_run_plan import BRIGHTER, notes, propose, row, rows_of, say_yes

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "assembled"),
]

STILL_DARK = "The workshop is still as dark as before."


@pytest.fixture
def held(steps: HeldSteps, fake_model: FakeModel) -> HeldSteps:
    return steps


def picture_steps(plan_row: FixPlanScene) -> list[SceneStep]:
    return list(plan_row.steps.filter(kind=SceneStep.Kind.STARTING_PICTURE).order_by("id"))


def asked(purpose: str) -> list[ModelCall]:
    return list(ModelCall.objects.filter(purpose=purpose).order_by("created_at", "id"))


def fails(fake_model: FakeModel, times: int, still_wrong: str = STILL_DARK) -> None:
    """The fix check fails the next `times` pictures, each by two answers of its three."""
    fake_model.respond("check_fix", *[not_made(still_wrong)] * (2 * times))


def test_a_picture_that_fails_its_fix_check_is_drawn_again_told_why_and_its_clip_made(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    ads_before = len(ads())
    plan = propose(row(1, "new_picture", change=BRIGHTER))
    say_yes(plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE, TALKING_CHOICE)
    fails(fake_model, 1)

    held.run_held()

    one = rows_of(plan)[1]
    first, again = picture_steps(one)
    assert again.redraw_of == first
    # The redraw's picture writer is told what the check found still wrong.
    choices = asked("choose_starting_picture")
    assert STILL_DARK not in (choices[-2].handoff["note"] or "")
    assert STILL_DARK in choices[-1].handoff["note"]
    # Each try is checked against the picture the shop owner complained about.
    failed, passed = FixCheck.objects.order_by("id")
    assert (failed.verdict, passed.verdict) == ("failed", "passed")
    assert passed.step == again and passed.old == failed.old
    assert failed.old is not None and failed.old.version == 1
    # The shop owner was never asked, nor the producer told of a failed check: the redraw's
    # clip was made, the scene landed and the new ad was assembled.
    assert failed.notice is None
    assert notes("check_failed") == []
    made = again.produced.get()
    assert clip(1).picture == made
    assert status(1) == "finished"
    assert one.landed_at is not None
    assert len(ads()) == ads_before + 1


def test_a_picture_failing_its_fix_check_every_try_asks_the_shop_owner_once(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    ads_before = len(ads())
    plan = propose(row(1, "new_picture", change=BRIGHTER))
    say_yes(plan)
    tries = fix_check.MOST_REDRAWS + 1
    fake_model.respond("choose_starting_picture", *[TALKING_CHOICE] * tries)
    fails(fake_model, tries - 1)
    fails(fake_model, 1, HANDLE_HIDDEN)

    held.run_held()

    one = rows_of(plan)[1]
    steps = picture_steps(one)
    assert len(steps) == tries
    assert [step.redraw_of for step in steps] == [None, *steps[:-1]]
    checks = list(FixCheck.objects.order_by("id"))
    assert [check.verdict for check in checks] == ["failed"] * tries
    # Only the last try's check is shown to the shop owner, with the picture they complained
    # about and the last one drawn, and why that one failed.
    last = checks[-1]
    assert [check.notice is None for check in checks] == [True] * (tries - 1) + [False]
    assert last.old == checks[0].old and last.new == steps[-1].produced.get()
    assert last.notice is not None
    assert HANDLE_HIDDEN in last.notice.text
    shown = list(last.notice.attachments.order_by("position").values_list("file", flat=True))
    assert shown == [last.old.file, last.new.file]  # type: ignore[union-attr]
    (told,) = notes("check_failed")
    assert HANDLE_HIDDEN in told
    assert not SceneStep.objects.filter(scene__number=1, kind="clip", plan_scene__isnull=False)
    assert run_plan.held_on_a_check(plan.session) == [one]
    assert len(ads()) == ads_before

    # Go ahead makes the clip of the last picture drawn.
    run_plan.go_ahead(one)
    held.run_held()

    assert clip(1).picture == steps[-1].produced.get()
    assert rows_of(plan)[1].landed_at is not None
    assert len(ads()) == ads_before + 1


def test_a_broll_redraw_is_told_the_fix_checks_reason_and_shown_the_picture_that_failed(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    plan = propose(row(2, "new_picture", change="The mug's handle faces the camera."))
    say_yes(plan)
    fake_model.respond("choose_broll_picture", HANDLE_SHOWING, HANDLE_SHOWING)
    fails(fake_model, 1, HANDLE_HIDDEN)

    held.run_held()

    first, again = picture_steps(rows_of(plan)[2])
    failed_picture = first.produced.get()
    writes = asked("choose_broll_picture")
    assert "redo" not in writes[-2].handoff
    redo: dict[str, Any] = writes[-1].handoff["redo"]
    assert redo["problems"] == [HANDLE_HIDDEN]
    assert redo["picture_prompt"] == first.prompt
    assert redo["video_prompt"] == first.motion_prompt
    assert writes[-1].images[-1] == {"label": "The last picture", "key": failed_picture.file}
    assert status(2) == "finished"
    assert clip(2).picture == again.produced.get()


def test_a_redraw_whose_picture_check_fails_is_still_told_the_fix_checks_reason(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    plan = propose(row(2, "new_picture", change="The mug's handle faces the camera."))
    say_yes(plan)
    fake_model.respond("choose_broll_picture", *[HANDLE_SHOWING] * 3)
    fails(fake_model, 1, HANDLE_HIDDEN)
    # The redraw's first picture fails its own picture check, by two answers of three.
    wrong = {**PICTURE_OK, "verdict": "fail", "problem": "The mug floats."}
    fake_model.respond("check_starting_picture", PICTURE_OK, PICTURE_OK, wrong, wrong)

    held.run_held()

    redo = asked("choose_broll_picture")[-1].handoff["redo"]
    # Each failing answer of the picture check is a problem of its own.
    assert redo["problems"] == [HANDLE_HIDDEN, "The mug floats.", "The mug floats."]


def test_a_newer_plan_while_the_picture_is_drawn_again_stops_the_redraws(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    old = propose(row(1, "new_picture", change=BRIGHTER))
    say_yes(old)
    fake_model.respond("choose_starting_picture", *[TALKING_CHOICE] * 3)
    fails(fake_model, 2)
    held.run_next()
    first, again = picture_steps(rows_of(old)[1])
    assert again.status == "running"

    # The shop owner plans the scene again while its picture is drawn again.
    newer = propose(row(1, "new_picture", change="Make it warmer."))
    say_yes(newer)
    held.run_held()

    # The old plan's redraw that was running finishes, and nothing more is drawn for it, nor
    # asked; the newer plan's picture is made.
    stale = rows_of(old)[1]
    assert stale.error and stale.landed_at is None
    assert len(picture_steps(stale)) == 2
    assert FixCheck.objects.filter(step__plan_scene=stale, notice__isnull=False).count() == 0
    assert notes("check_failed") == []
    assert run_plan.held_on_a_check(old.session) == []
    assert len(picture_steps(rows_of(newer)[1])) == 1


def test_a_redraw_that_cant_be_made_asks_the_shop_owner_about_the_try_before_it(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    ads_before = len(ads())
    plan = propose(row(1, "new_picture", change=BRIGHTER))
    say_yes(plan)
    fake_model.respond("choose_starting_picture", *[TALKING_CHOICE] * 2)
    fails(fake_model, 1)
    held.run_next()

    # The worker restarts while the picture is drawn again.
    held.held.clear()
    tasks.carry_on_after_the_worker_starts()

    one = rows_of(plan)[1]
    first, again = picture_steps(one)
    assert again.status == "failed"
    failed = FixCheck.objects.get(step=first)
    assert failed.notice is not None and STILL_DARK in failed.notice.text
    (told,) = notes("check_failed")
    assert STILL_DARK in told
    assert one.error == ""
    assert run_plan.held_on_a_check(plan.session) == [one]

    # Go ahead makes the clip of the try before the one that couldn't be made.
    run_plan.go_ahead(one)
    held.run_held()

    assert clip(1).picture == first.produced.get()
    assert len(ads()) == ads_before + 1
