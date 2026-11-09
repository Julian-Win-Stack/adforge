"""The move check before filming, and still shots filmed as a camera move, through the chat."""

import io
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest

from adforge import file_store
from agents.scene_agent import move_check
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs import notices
from jobs.models import Job, Scene

from .conftest import HeldSteps, facts_ok, handoffs, paid_for, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import ACTION, change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
BLOCKED: dict[str, Any] = {
    "moving_parts": "The heel strap swings from the toe box, around its rivets, to behind "
    "the heel.",
    "blocked": True,
    "says": "The ankle stands inside the strap's loop, so the strap must pass through it.",
}
GIVE_UP = turn(calls=[("give_up", {"reason": "The clip should pass."})])


def started(fake_model: FakeModel, say: Callable[..., None], scene: int) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Show it.", "quotes": ["go on"]}),
    )


def warnings_of(kind: notices.Kind) -> list[str]:
    return [
        warning["text"] for warning in Job.objects.get().warnings if warning["kind"] == kind.name
    ]


def test_a_failed_move_check_refuses_without_a_clip_try_and_a_replanned_scene_films(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond("check_move", BLOCKED)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[change()]),
        turn(calls=[change()]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    refused, filmed = results_of("film")
    assert move_check.MOVE_FAILED in refused
    assert BLOCKED["says"] in refused
    assert "No clip try was used" in refused
    assert "change_scene" in refused
    assert "Do not shrink the move" in refused
    assert "producer's instruction names this action" in refused
    assert move_check.MOVE_FAILED not in filmed
    # One clip, filmed only after the re-plan; the check ran on both films.
    assert paid_for().count("make_broll_clip") == 1
    assert paid_for().count("check_move") == 2
    assert Scene.objects.get(number=2).shows == ACTION
    # The check sees the clip's first frame and the prompt H3 is sent.
    calls = ModelCall.objects.filter(purpose="check_move").order_by("id")
    (clip,) = handoffs("make_broll_clip")
    assert calls.last().handoff["video_prompt"] == clip["prompt"]  # type: ignore[union-attr]
    assert [image["label"] for image in calls.last().images] == [  # type: ignore[union-attr]
        "The clip's first frame:"
    ]


def test_the_move_check_judges_talking_clips_too(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_move", BLOCKED)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        GIVE_UP,
    )
    started(fake_model, say, 3)
    run(fake_model, steps)

    assert Scene.objects.get(number=3).kind == Scene.Kind.TALKING
    (asked,) = handoffs("check_move")
    assert "She talks straight to the lens" in asked["video_prompt"]
    assert move_check.MOVE_FAILED in results_of("film")[0]
    assert "make_broll_clip" not in paid_for()


def test_after_three_failed_move_checks_the_clip_is_filmed_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_move", BLOCKED, BLOCKED, BLOCKED)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    assert paid_for().count("check_move") == 3
    assert paid_for().count("make_broll_clip") == 1
    (notice,) = warnings_of(notices.MOVE_CHECK_FILMED_ANYWAY)
    assert notice.startswith("Scene 2: filmed though its move check failed 3 times")
    assert BLOCKED["says"] in notice


def test_a_move_check_with_no_answer_films_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "check_move",
        *[UnusableReply("no answer", input_tokens=1, output_tokens=1)] * 3,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    assert paid_for().count("make_broll_clip") == 1
    (notice,) = warnings_of(notices.MOVE_UNCHECKED)
    assert notice.startswith("Scene 2's move could not be checked")


def _size(key: str) -> tuple[int, int]:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        return opened.size


@pytest.mark.parametrize(
    "still_shot", ["where it fits: pull back", "how it looks: push in"], ids=["pull", "push"]
)
def test_a_still_shot_is_filmed_between_the_picture_and_a_crop_of_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    still_shot: str,
) -> None:
    still: dict[str, Any] = {**FILM, "still_shot": still_shot, "crop_x": 30, "crop_y": 60}
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", still)]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    (clip,) = handoffs("make_broll_clip")
    assert "Only the camera moves" in clip["prompt"]
    assert "handheld" not in clip["prompt"]
    picture = Job.objects.get().produced.filter(kind="starting_picture").latest("id").file
    first, last = clip["starting_picture"], clip["end_picture"]
    crop = first if still_shot.endswith("pull back") else last
    assert (last if crop == first else first) == picture
    assert crop != picture
    assert _size(crop) == _size(picture)
    # The move check saw the very frames H3 gets.
    (asked,) = ModelCall.objects.filter(purpose="check_move")
    assert [image["key"] for image in asked.images] == [first, last]


def test_only_a_broll_scene_can_be_a_still_shot(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {**FILM, "still_shot": "how it looks: push in"})]),
        GIVE_UP,
    )
    started(fake_model, say, 3)
    run(fake_model, steps)

    assert "Only a B-roll scene can be a planned still shot." in results_of("film")[0]
    assert "make_broll_clip" not in paid_for()
