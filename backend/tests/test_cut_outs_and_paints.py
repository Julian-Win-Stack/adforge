"""Cut-outs and paints that work (spec #273): what a B-roll clip's pictures go through before
they are sent, from the shop owner's first message to the finished ad. The picture model is
asked for the photo's own shape; a photo is cut out only when it is sent; a picture is painted
only when its colour, an entry of the product's colour list, isn't the clip's; a paint is never
checked.

Every model is faked at the gateway, the shop is served on this machine through the stand-in
Firecrawl, and the fake picture model and video API are read for exactly what they were
asked."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel
from jobs.models import Job

from .conftest import HeldSteps, paid_for
from .test_broll_sent_list_end_to_end import (  # noqa: F401 (fixtures)
    PRESSED,
    a_plan,
    item_of,
    make_the_ad,
    page_with_a_video,
    watched,
)
from .test_picker_labels import labelled_page  # noqa: F401 (a fixture)
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
