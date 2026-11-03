"""Page-video scenes draw in the video's colour and film with its movement reference."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import NO_CHOICES, READABLE, HeldSteps, facts_ok, handoffs
from .test_broll_sent_list_end_to_end import (
    PICKED,
    PRESSED,
    a_plan,
    item_of,
    page_with_a_video,  # noqa: F401
    watched,
)
from .test_picker_labels import labelled_page  # noqa: F401
from .test_producer_broll import calling, run
from .test_product_videos import picks_videos  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def video_scene_ready(
    fake_model: FakeModel,
    page_with_a_video: str,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    request: pytest.FixtureRequest,
) -> None:
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", PICKED)
    watch = watched("black", PRESSED)
    if getattr(request, "param", False):
        watch["moments"][0].update(start=5.8, end=6.0)
    fake_model.answer_unscripted("watch_video", lambda _: watch)
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": page_with_a_video, "target_seconds": None})]),
        turn(says="The page is read."),
    )
    say(page_with_a_video)
    fake_model.respond("plan_ad", a_plan(item_of(Job.objects.get(), "I104"), 1))
    calling(fake_model, say, ("plan_ad", {}))
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    calling(fake_model, say, ("make_line_audio", {"scene": 2}))
    run(fake_model, steps)
    calling(fake_model, say, ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)


@pytest.mark.parametrize("use_closest", [False, True])
def test_page_video_scene_sends_its_starting_picture_and_the_piece_only(
    fake_model: FakeModel,
    video_scene_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    use_closest: bool,
) -> None:
    import re

    from gateway.types import Turn

    from .conftest import results_of

    if use_closest:
        failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "It floats."}}
        fake_model.answer_unscripted("check_scene_picture", lambda _: failed)

    def filming() -> Turn:
        arguments: dict[str, Any] = {
            "form": {**FILM["form"], "video_for": "Copy the pump pressing movement"}
        }
        if use_closest:
            matched = re.search(r"Starting picture (\d+)", results_of("draw_starting_picture")[0])
            assert matched is not None
            arguments.update(closest_picture=int(matched[1]), reason="The shape is closest.")
        return turn(calls=[("film", arguments)])

    fake_model.respond(
        "scene_agent",
        turn(calls=[("look_at_library", {"item": None, "video": 1, "moment": 1})]),
        *[
            turn(calls=[("draw_starting_picture", PICTURE_FORM)])
            for _ in range(6 if use_closest else 1)
        ],
        filming,
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    sent = handoffs("make_broll_clip")[-1]
    assert sent["starting_picture"] is None
    assert len(sent["example_pictures"]) == 1
    assert len(sent["reference_videos"]) == 1
    assert "The clip starts exactly like Image 1." in sent["prompt"]
    assert "Copy the pump pressing movement" in sent["prompt"]
    assert sent["prompt_enhancement"] is False
    assert "black" in handoffs("draw_scene_picture")[-1]["prompt"]
    assert handoffs("check_scene_picture")[-1]["product_colour"] == "black"


@pytest.mark.parametrize("video_scene_ready", [True], indirect=True)
def test_a_piece_too_short_to_send_falls_back_to_start_frame_with_a_notice(
    fake_model: FakeModel,
    video_scene_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: object,
    session_id: str,
) -> None:
    from typing import cast

    from rest_framework.test import APIClient

    from .conftest import recorded

    fake_model.respond(
        "scene_agent",
        turn(calls=[("look_at_library", {"item": None, "video": 1, "moment": 1})]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(
            calls=[
                ("film", {"form": {**FILM["form"], "video_for": "Copy the pump pressing movement"}})
            ]
        ),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    sent = handoffs("make_broll_clip")[-1]
    assert sent["starting_picture"]
    assert sent["example_pictures"] == []
    assert sent.get("reference_videos", []) == []
    assert "Image 1" not in sent["prompt"]
    assert any(
        "isn't sent" in text and "piece of video 1" in text
        for _, text in recorded(cast(APIClient, api), session_id)
    )


def test_page_video_film_requires_a_look_and_the_video_purpose_before_paying(
    fake_model: FakeModel,
    video_scene_ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import paid_for, results_of

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "video_for": "Copy the movement"}})]),
        turn(calls=[("look_at_library", {"item": None, "video": 1, "moment": 1})]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert "Look at the planned page-video" in results_of("film")[0]
    assert "Say what to take" in results_of("film")[1]
    assert "make_broll_clip" not in paid_for()
