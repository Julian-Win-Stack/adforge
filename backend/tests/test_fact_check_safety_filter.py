"""Fact-check photo refusals, driven through the owner's chat with outside models faked."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs.models import Job, ProducedItem

from .conftest import FACTS_OK, HeldSteps, a_plan_with, chat, facts_ok, recorded, results_of
from .test_producer_checks import checking, images_shown
from .test_safety_filter_split import refused_over

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_refused_fact_check_photo_is_left_out_and_filming_can_start(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    api: APIClient,
    session_id: str,
) -> None:
    checking(
        fake_model,
        product_page_url,
        a_plan_with(colour_photos=[1, 2]),
        after_checks=[
            turn(
                calls=[
                    (
                        "start_scene_agent",
                        {
                            "scene": 2,
                            "instruction": "Make this scene.",
                            "quotes": [product_page_url],
                        },
                    )
                ]
            ),
        ],
    )
    fake_model.answer_unscripted("fact_check", refused_over("Photo 2", FACTS_OK))

    say(product_page_url)

    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    assert images_shown("fact_check") == [["Photo 1", "Photo 2"], ["Photo 1"]]
    job = Job.objects.get()
    assert job.status == Job.Status.READY_TO_RENDER
    assert job.scenes.filter(fact_checked=True).count() == 3
    assert len(steps.held) == 1
    ((level, notice),) = [
        (level, text) for level, text in recorded(api, session_id) if "safety filter" in text
    ]
    assert level == "problem"
    assert "Photo 2" in notice
    assert not any(notice == text for _, text in chat(api, session_id))


def test_a_fact_check_refused_even_without_photos_never_passes(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
) -> None:
    checking(fake_model, product_page_url, a_plan_with(colour_photos=[1, 2]))

    def refuse(request: ModelRequest[Any]) -> dict[str, Any]:
        raise BlockedBySafetyFilter("content_filter")

    fake_model.answer_unscripted("fact_check", refuse)

    say(product_page_url)

    assert images_shown("fact_check") == [
        ["Photo 1", "Photo 2"],
        ["Photo 1"],
        ["Photo 2"],
        [],
    ]
    assert results_of("run_planning_checks")[0].startswith("Failed:")
    job = Job.objects.get()
    assert job.status != Job.Status.READY_TO_RENDER
    assert not job.scenes.filter(fact_checked=True).exists()


def test_a_filmed_scene_can_change_after_a_fact_check_photo_is_left_out(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    api: APIClient,
    session_id: str,
) -> None:
    from .test_producer_broll import calling, run
    from .test_scene_agent_changes import change
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    plan = a_plan_with(colour_photos=[1, 2])
    checking(fake_model, product_page_url, plan)
    fake_model.respond("fact_check", FACTS_OK)
    say(product_page_url)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond("transcribe_line", {"text": plan["plan"]["scenes"][1]["line"]})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Make the scene.",
                "quotes": [product_page_url],
            },
        ),
    )
    run(fake_model, steps)
    assert ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP, scene__number=2).exists()

    line = "This mug holds three hundred and fifty millilitres of your favourite tea."
    fake_model.answer_unscripted("fact_check", refused_over("Photo 2", facts_ok(2)))
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Change the scene and film it.",
                "quotes": [product_page_url],
            },
        ),
    )
    run(fake_model, steps)

    assert results_of("change_scene")[-1].startswith("The scene row changed")
    scene = Job.objects.get().scenes.get(number=2)
    assert scene.line == line and scene.fact_checked
    assert images_shown("fact_check")[-2:] == [["Photo 1", "Photo 2"], ["Photo 1"]]
    assert len(results_of("film")) == 2
    assert all("passes" in result for result in results_of("film"))
    (notice,) = [text for _, text in recorded(api, session_id) if "safety filter" in text]
    assert "Photo 2" in notice
    assert not any(text == notice for _, text in chat(api, session_id))
