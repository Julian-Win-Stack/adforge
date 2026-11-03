"""The scene agent films its checked picture through the owner's chat."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, paid_for, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
FILM: dict[str, Any] = {
    "form": {
        "shot_size": "Close shot",
        "frame_shows": "Hands hold the mug at a desk",
        "product": "The glazed mug",
        "camera": "The phone drifts gently left",
        "beats": ["Tea pours into the mug", "Steam rises"],
        "shows_up_later": "not needed here",
        "small_life": "The fingers shift slightly",
        "lock_line": "The mug keeps its exact shape, colour and printed words",
        "anything_else": "not needed here",
    }
}


def test_film_without_a_checked_picture_is_refused_without_paying(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Need a picture."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "starting picture has passed" in str(results_of("film"))
    assert "make_broll_clip" not in paid_for()


def test_checked_picture_is_the_only_h3_input_and_judge_sees_its_sources(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import handoffs
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    sent = handoffs("make_broll_clip")
    assert len(sent) == 1, results_of("film")
    assert sent[0]["starting_picture"]
    assert sent[0]["example_pictures"] == []
    assert sent[0].get("reference_videos", []) == []
    assert sent[0]["prompt_enhancement"] is False
    assert "passed" in str(handoffs("produce"))
    from gateway.models import ModelCall
    from jobs.models import ProducedItem

    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    expected = [picture.file, *[p["file"] for p in picture.sent["photos"] if p["library_number"]]]
    judge = ModelCall.objects.filter(purpose="check_clip_product").first()
    assert judge is not None
    assert [image["key"] for image in judge.images[: len(expected)]] == expected
    assert len(judge.images) > len(expected)
