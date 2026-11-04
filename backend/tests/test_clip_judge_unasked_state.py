"""An unrequested product state reaches PRODUCT instead of bypassing its check."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import ProducedItem

from .conftest import (
    CLIP_PRODUCT_OK,
    REFERENCES_ENOUGH,
    HeldSteps,
    chat,
    paid_for,
    recorded,
    results_of,
)
from .test_producer_broll import BROLL_PLAN, calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
UNSEEN = "the mug's underside"
FAULT = "The mug grows a second handle at 2 s; the reference shows one."


@pytest.mark.parametrize(
    "asked_for, product_verdict, expected",
    [
        (False, "fail", "fail"),
        (False, "pass", "pass"),
        (False, "unsure", "unsure"),
        (True, "fail", "unsure"),
        (None, "fail", "unsure"),
    ],
)
def test_only_product_can_fail_an_unrequested_state_and_requested_states_stay_optional(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    asked_for: bool | None,
    product_verdict: str,
    expected: str,
) -> None:
    first: dict[str, Any] = {
        **REFERENCES_ENOUGH,
        "verdict": "product not shown",
        "references_never_show": UNSEEN,
        **({"asked_for": asked_for} if asked_for is not None else {}),
    }
    fake_model.respond("check_clip_references", first, first)
    fake_model.respond(
        "check_clip_product",
        {
            **CLIP_PRODUCT_OK,
            "verdict": product_verdict,
            "compared_against": "Image 1" if product_verdict == "fail" else None,
            "clip_shows": FAULT if product_verdict == "fail" else None,
            "real_product_shows": "One handle." if product_verdict == "fail" else None,
            "fault_frame_times": [2.0] if product_verdict == "fail" else [],
            "fault": FAULT if product_verdict == "fail" else None,
            "references_never_show": UNSEEN if product_verdict == "unsure" else None,
        },
    )
    fake_model.respond("transcribe_line", {"text": BROLL_PLAN["plan"]["scenes"][1]["line"]})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The failed product needs another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Pour tea into the mug.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind=ProducedItem.Kind.CLIP)
    ruling = clip.ruling
    assert ruling is not None
    assert ruling["verdict"] == expected
    assert ("check_clip_product" in paid_for()) is (asked_for is False)
    if asked_for is not None:
        assert ruling["answers"]["references"]["asked_for"] is asked_for
    if expected == "fail":
        assert "It fails." in str(results_of("film"))
        assert FAULT in str(results_of("film"))
    else:
        assert "It passes." in str(results_of("film"))
    if expected == "unsure":
        assert any("Please watch scene 2's clip" in text for _, text in chat(api, session_id))
    if asked_for is None:
        assert ModelCall.objects.filter(purpose="check_clip_references").count() == 2
        assert any("references show enough" in text for _, text in recorded(api, session_id))
