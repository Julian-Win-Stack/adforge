"""Cut-outs and paints that work (spec #273): what a B-roll clip's pictures go through before
they are sent, from the shop owner's first message to the finished ad. The picture model is
asked for the photo's own shape; a photo is cut out only when it is sent; a picture is painted
only when its colour, an entry of the product's colour list, isn't the clip's; a paint is never
checked.

Every model is faked at the gateway, the shop is served on this machine through the stand-in
Firecrawl, and the fake picture model and video API are read for exactly what they were
asked."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.library import library_of, sent_list_of
from jobs.models import Job

from .conftest import HeldSteps, handoffs, paid_for
from .test_broll_sent_list_end_to_end import (  # noqa: F401 (fixtures)
    PICKED,
    PRESSED,
    a_plan,
    item_of,
    make_the_ad,
    page_with_a_video,
    watched,
)
from .test_picker_labels import label, labelled_page  # noqa: F401 (a fixture)
from .test_product_videos import picks_videos  # noqa: F401 (a fixture)
from .test_recolour import RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)

# The page's added photos are 112 by 140 pixels, 4:5, and its front photo 300 by 400, 3:4. The
# picture model's sizes are multiples of 16 with about as many pixels as 1152 by 2048: at 4:5,
# 1376 by 1712; at 3:4, 1328 by 1776.
FOUR_BY_FIVE = "1376x1712"
THREE_BY_FOUR = "1328x1776"


def black_video_ad(
    fake_model: FakeModel, say: Callable[..., None], steps: HeldSteps, link: str
) -> None:
    """The shampoo's ad, its scene 2 cut from a page video showing the bottle black, so the
    amber photos it sends are painted black. A check of a paint, if one were asked, passes."""
    fake_model.answer_unscripted("watch_video", lambda _: watched("black", PRESSED))
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
    make_the_ad(fake_model, say, steps, link, lambda job: a_plan(item_of(job, "I104"), 1))


def test_a_paint_is_made_once_and_never_checked(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    black_video_ad(fake_model, say, steps, page_with_a_video)

    paid = paid_for()
    assert Job.objects.get().produced.filter(kind="finished_ad").exists()
    assert "recolour_picture" in paid
    assert "check_recolour" not in paid


def test_a_cut_out_and_a_paint_are_asked_for_in_the_photos_shape(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    black_video_ad(fake_model, say, steps, page_with_a_video)

    asked = dict(fake_model.edits_asked)
    assert Job.objects.get().produced.filter(kind="finished_ad").exists()
    assert ("make_product_only", FOUR_BY_FIVE) in fake_model.edits_asked
    assert ("recolour_picture", FOUR_BY_FIVE) in fake_model.edits_asked
    assert ("recolour_picture", THREE_BY_FOUR) in fake_model.edits_asked
    # A talking scene's starting picture keeps its own size.
    assert asked["make_starting_picture"] == "1152x2048"


# The shampoo is sold in amber, black and green, and its page has a top view in each, its
# front in amber and a hand using it, the product's body not seen.
THREE_COLOURS = {
    **PICKED,
    # Written as the page names them: each kept once, as an entry, and never "none".
    "colours": ["Amber", "black", "Black", "green", "none"],
    "pictures": [
        label("I8", 1, "side", "front", "The amber bottle from the front, cap on."),
        label("I101", 2, "side", "top", "The black bottle from the top.", colour="black"),
        label("I102", 3, "side", "top", "The amber bottle from the top."),
        label("I103", 4, "side", "top", "The green bottle from the top.", colour="green"),
        label(
            "I104",
            5,
            "in-use",
            "none",
            "A hand pressing the pump, the shampoo coming out.",
            alone="no",
            colour="none",
        ),
    ],
}


def colours_allowed(output: type[BaseModel], *path: str) -> list[str]:
    """The colours a call's answer may name at `path` in its schema."""
    schema = output.model_json_schema()
    at: dict[str, Any] = schema
    for step in path:
        at = at["properties"][step] if step != "[]" else at["items"]
        while "$ref" in at or "anyOf" in at:
            if "anyOf" in at:
                at = next(each for each in at["anyOf"] if each.get("type") != "null")
                continue
            at = schema["$defs"][at["$ref"].rsplit("/", 1)[-1]]
    return sorted(at.get("enum", []))


def test_a_clip_in_one_of_three_colours_is_sent_its_top_view_and_nothing_is_painted(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    fake_model.answer_unscripted("watch_video", lambda _: watched("amber", PRESSED))
    make_the_ad(
        fake_model,
        say,
        steps,
        page_with_a_video,
        lambda job: a_plan(item_of(job, "I104"), 1),
        THREE_COLOURS,
    )

    job = Job.objects.get()
    assert job.colours == ["amber", "black", "green"]
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    assert sent.colour == "amber"
    assert [(p.tier, p.library_number) for p in sent.pictures if p.library_number] == [
        (1, item_of(job, "I8")),
        (2, item_of(job, "I104")),
        (4, item_of(job, "I102")),
    ]
    assert sent.recolours == []
    assert "recolour_picture" not in paid_for()


def test_every_call_that_names_a_colour_picks_from_the_products_list(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    fake_model.answer_unscripted("watch_video", lambda _: watched("amber", PRESSED))
    # The picker names the green top view's colour in words not on its own list, so that
    # picture loses its fields and the labelling call labels it.
    unlisted = {
        **THREE_COLOURS,
        "pictures": [
            {**picture, "colour": "bright lime"} if picture["picture"] == "I103" else picture
            for picture in THREE_COLOURS["pictures"]
        ],
    }
    make_the_ad(
        fake_model,
        say,
        steps,
        page_with_a_video,
        lambda job: a_plan(item_of(job, "I104"), 1),
        unlisted,
    )

    listed = ["amber", "black", "green"]
    labelled = [asked for asked in fake_model.asked if asked.purpose == "label_photo"]
    assert len(labelled) == 1
    assert colours_allowed(labelled[0].output, "colour") == [*listed, "none"]
    (watched_video,) = fake_model.videos_watched
    assert colours_allowed(watched_video.output, "colour") == [*listed, "none"]
    assert colours_allowed(watched_video.output, "look_frames", "[]", "colour") == [
        *listed,
        "none",
    ]
    (planned,) = [asked for asked in fake_model.asked if asked.purpose == "plan_ad"]
    assert colours_allowed(planned.output, "plan", "product_colour") == listed


def test_a_photo_is_cut_out_once_when_it_is_first_sent_not_when_the_page_is_read(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    black_video_ad(fake_model, say, steps, page_with_a_video)

    paid = paid_for()
    # The back stands beside a towel: its cut-out is made for scene 2's clip, after the plan.
    assert paid.count("make_product_only") == 1
    assert paid.index("make_product_only") > paid.index("plan_ad")
    assert paid.count("pick_cut_out_helpers") == 1


def test_the_photos_that_show_a_hidden_part_go_to_the_picture_model_and_the_check(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    def helpers(_: Any) -> dict[str, Any]:
        job = Job.objects.get()
        back, front, side = (item_of(job, mark) for mark in ("I101", "I8", "I102"))
        # A number the library doesn't hold and the photo itself are dropped.
        return {"covered": True, "covered_part": "its pump", "helpers": [99, front, back, side]}

    fake_model.answer_unscripted("pick_cut_out_helpers", helpers)
    black_video_ad(fake_model, say, steps, page_with_a_video)

    job = Job.objects.get()
    library = {item.number: item for item in library_of(job)}
    back, front, side = (library[item_of(job, mark)] for mark in ("I101", "I8", "I102"))
    (asked,) = [asked for asked in fake_model.asked if asked.purpose == "pick_cut_out_helpers"]
    assert asked.images[0].label.startswith("The photo to cut out")
    (cut,) = handoffs("make_product_only")
    assert cut["pictures"] == [back.file, front.file, side.file]
    assert "its pump" in cut["prompt"]
    (check,) = handoffs("check_cut_out")
    assert check["hidden_part"] == "its pump"
    shown = ModelCall.objects.get(purpose="check_cut_out").images
    assert [image["key"] for image in shown][1:] == [back.file, front.file, side.file]
