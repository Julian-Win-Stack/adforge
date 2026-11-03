"""Judge words and picture bounds are observable in the real scene chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ToolUse

from .conftest import HeldSteps, facts_ok, results_of
from .test_clip_judge_references import DETAIL_NOT_SHOWN
from .test_make_picture import PASSES
from .test_producer_auto_fix import failing
from .test_producer_broll import SHOWS, calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)

PRODUCT_FAULT = {
    "product_from_references": "A white mug with a lid.",
    "frames": [],
    "verdict": "fail",
    "compared_against": "Image 2",
    "clip_shows": "a second handle on the back, at 2.3 s",
    "real_product_shows": "one handle, on the right",
    "fault_frame_times": [2.3, 2.5],
    "fault": "The mug grows a second handle.",
    "references_never_show": None,
}


FIXED_FORM = (
    "- Compared against Image 2: the clip shows a second handle on the back, at 2.3 s; the "
    "real product shows one handle, on the right (at 2.3 s, 2.5 s). The fault: The mug "
    "grows a second handle."
)


def test_scene_chat_keeps_fault_words_shared_picture_and_latest_frames_under_the_budget(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.answer_unscripted("check_made_picture", lambda _: PASSES)
    calling(
        fake_model,
        say,
        *[
            (
                "make_picture",
                {
                    "prompt": f"A neutral product detail view, angle {number}.",
                    "references": [1],
                    "label": "The product from another angle.",
                },
            )
            for number in range(25)
        ],
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.answer_unscripted(
        "check_clip_references",
        lambda _: {
            **DETAIL_NOT_SHOWN,
            "references_never_show": "the mug's base",
        },
    )
    fake_model.answer_unscripted("check_clip_product", lambda _: PRODUCT_FAULT)
    lid = "The lid floats off by itself."
    fake_model.answer_unscripted("check_clip_motion", lambda _: failing(lid))
    line = (
        "This carefully made mug keeps your morning coffee close while you work at your "
        "desk and gives your hands a comfortable place to hold every warm drink."
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=line, shows=SHOWS)]),
        turn(
            calls=[
                ("look_at_library", {"item": item, "video": None, "moment": None})
                for item in range(1, 27)
            ]
        ),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *[turn(calls=[("film", FILM)]) for _ in range(3)],
        turn(calls=[("look_at_library", {"item": 25, "video": None, "moment": None})]),
        turn(calls=[("give_up", {"reason": "Need another product view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show its use.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    requests = [request for request in fake_model.turns_taken if request.purpose == "scene_agent"]
    last = requests[-1]
    labels = [image.label for image in last.images]
    assert len(labels) <= 50
    library = [label for label in labels if label.startswith("Library item ")]
    assert len(library) == 25
    assert any(label.startswith("Library item 25:") for label in library)
    assert "Shared room alone" in labels
    assert any("Try 3, new clip" in label for label in labels)
    assert not any("Try 1, new clip" in label for label in labels)
    assert "fill the room kept for them" in results_of("look_at_library")[25]
    words = "\n".join(
        each.result if isinstance(each, ToolUse) else each.text
        for each in last.handoff.conversation
    )
    assert FIXED_FORM in words
    assert f"- It found: {lid}" in words
    assert words.index(FIXED_FORM) < words.index(f"- It found: {lid}")
    assert "The judge left out one detail the references never show: the mug's base." in words
