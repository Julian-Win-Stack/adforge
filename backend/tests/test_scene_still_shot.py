"""A planned still shot is filmed as a small camera move between the starting picture and a
crop cut from its own pixels, through the chat (h3-limits.md section 19)."""

import io
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest

from adforge import file_store
from agents.scene_forms import CODE_LINES, PULL_BACK, PUSH_IN, STILL_SHOT_CAMERA
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import notices
from jobs.models import Job

from .conftest import HeldSteps, handoffs, paid_for, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
GIVE_UP = turn(calls=[("give_up", {"reason": "The clip should pass."})])


def started(fake_model: FakeModel, say: Callable[..., None], scene: int) -> None:
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": scene, "instruction": "Show it.", "quotes": ["go on"]}),
    )


def _opened(key: str) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        return opened.convert("RGB")


@pytest.mark.parametrize("still_shot", [PULL_BACK, PUSH_IN], ids=["pull", "push"])
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
    # Code says only the camera moves, after the agent's camera box; never "handheld".
    camera = STILL_SHOT_CAMERA[still_shot]  # type: ignore[index]
    assert f"{FILM['form']['camera']}. {camera}" in clip["prompt"]
    assert "handheld" not in clip["prompt"]
    # A pull back starts on the crop; a push in ends on it. The other end is the picture.
    picture = Job.objects.get().produced.filter(kind="starting_picture").latest("id").file
    first, last = clip["starting_picture"], clip["end_picture"]
    crop, whole = (first, last) if still_shot == PULL_BACK else (last, first)
    assert whole == picture
    assert crop != picture
    assert _opened(crop).size == _opened(picture).size
    # The move check saw the very frames H3 gets.
    (asked,) = ModelCall.objects.filter(purpose="check_move")
    assert [image["key"] for image in asked.images] == [first, last]
    # The scene agent was told how to film a still shot and code's exact fixed lines.
    (told,) = {
        call.system_prompt.instructions
        for call in ModelCall.objects.filter(purpose="scene_agent")
        if call.system_prompt is not None
    }
    assert CODE_LINES in told
    assert "Film it with still_shot: a pull back for where it fits, a push in" in told


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
        turn(calls=[("film", {**FILM, "still_shot": PUSH_IN})]),
        GIVE_UP,
    )
    started(fake_model, say, 3)
    run(fake_model, steps)

    assert "Only a B-roll scene can be a planned still shot." in results_of("film")[0]
    assert "make_broll_clip" not in paid_for()


def test_a_failed_end_picture_does_not_hold_back_a_still_shot_that_never_uses_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_end_pictures import END_PICTURE
    from .test_scene_pictures import PICTURE_PASSES

    failed = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The label is gone."}}
    fake_model.respond("check_scene_picture", PICTURE_PASSES, failed)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", {**FILM, "still_shot": PUSH_IN})]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    (clip,) = handoffs("make_broll_clip")
    end = Job.objects.get().produced.filter(kind="end_picture").latest("id").file
    assert clip["end_picture"] != end
    assert not [
        warning
        for warning in Job.objects.get().warnings
        if warning["kind"] == notices.FAILED_END_PICTURE_FILMED.name
    ]


def test_a_clip_that_is_not_a_still_shot_has_no_camera_only_line(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        GIVE_UP,
    )
    started(fake_model, say, 2)
    run(fake_model, steps)

    (clip,) = handoffs("make_broll_clip")
    assert "Only the camera moves" not in clip["prompt"]
    assert clip.get("end_picture") is None
