"""Judge words and picture bounds are observable in the real scene chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import ToolUse

from .conftest import HeldSteps, facts_ok, results_of
from .test_clip_judge_references import DETAIL_NOT_SHOWN
from .test_make_picture import PASSES
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
        # Each judge-failed clip is fixed with a new starting picture.
        *[
            turn(calls=[call])
            for _ in range(3)
            for call in (("draw_starting_picture", PICTURE_FORM), ("film", FILM))
        ],
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
    assert "fill the room kept for them" in results_of("look_at_library")[25]
    words = "\n".join(
        each.result if isinstance(each, ToolUse) else each.text
        for each in last.handoff.conversation
    )
    assert FIXED_FORM in words
    assert "real_motion" not in words
    assert "The judge left out one detail the references never show: the mug's base." in words


@pytest.mark.parametrize("redraw", [False, True])
def test_latest_visual_block_borrows_library_room_and_a_later_draw_stays_visible(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    redraw: bool,
) -> None:
    from adforge import file_store
    from jobs.library import SentList, SentPicture, SentPiece, keep_sent_list, library_of
    from jobs.models import Job, ProducedItem

    from .conftest import a_video, picture
    from .legacy_records import legacy_clip_of_scene_2

    fake_model.answer_unscripted("check_made_picture", lambda _: PASSES)
    calling(
        fake_model,
        say,
        *[
            (
                "make_picture",
                {
                    "prompt": f"A neutral detail view {number}.",
                    "references": [1],
                    "label": "Another view of the product.",
                },
            )
            for number in range(25)
        ],
    )
    step = legacy_clip_of_scene_2(fake_model, steps, say, prepared=True)
    clip = step.produced.get(kind=ProducedItem.Kind.CLIP)
    # The historical ad already has a room. Looking at it must not generate a new one.
    ProducedItem.objects.create(
        job=step.scene.job,
        kind=ProducedItem.Kind.ROOM_ALONE,
        file=file_store.save("historical-room.png", picture(180, 320, (128, 128, 128))),
    )
    # Historical records can contain nine photos, two pieces and a 15-second clip.
    # Seed stored data, then let the actual look tool and current scene chat read it.
    clip.file = file_store.save("historical-long.mp4", a_video(180, 320, sound=True, seconds=15))
    clip.seconds = 15
    clip.ruling = None
    clip.save(update_fields=["file", "seconds", "ruling"])
    originals = library_of(Job.objects.get())[:9]
    piece = file_store.save("historical-piece.mp4", a_video(180, 320, sound=False, seconds=7.5))
    keep_sent_list(
        clip,
        SentList(
            colour="white",
            pictures=[
                SentPicture(
                    image=n,
                    tier=2,
                    library_number=item.number,
                    drawn=None,
                    for_=None,
                    label=item.label,
                    file=item.file,
                )
                for n, item in enumerate(originals, 1)
            ],
            pieces=[
                SentPiece(
                    number=n,
                    video=n,
                    moment=1,
                    moment_start=0,
                    moment_end=7.5,
                    start=0,
                    end=7.5,
                    trim=None,
                    file=piece,
                )
                for n in (1, 2)
            ],
            left_out=[],
            recolours=[],
            prompt="Film this in real-time speed. A hand uses it.",
        ),
    )
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                ("look_at_library", {"item": n, "video": None, "moment": None})
                for n in range(1, 26)
            ]
        ),
        turn(calls=[("look_at_try", {"number": 0})]),
        turn(
            calls=[
                ("look_at_library", {"item": n, "video": None, "moment": None})
                for n in range(1, 26)
            ]
        ),
        *(
            [turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": []})])]
            if redraw
            else []
        ),
        turn(calls=[("give_up", {"reason": "Need another view."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Look at the old clip.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    last = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
    labels = [image.label for image in last.images]
    assert len(labels) <= 50
    assert "Shared room alone" in labels
    assert len([label for label in labels if label.startswith("Library item ")]) == (
        25 if redraw else 24
    )
    visual = next(
        each
        for each in reversed(last.handoff.conversation)
        if isinstance(each, ToolUse)
        and each.tool == ("draw_starting_picture" if redraw else "look_at_try")
    )
    assert len(visual.pictures) == (1 if redraw else 25)
    if redraw:
        assert any(label.startswith("Starting picture ") for label in labels)
