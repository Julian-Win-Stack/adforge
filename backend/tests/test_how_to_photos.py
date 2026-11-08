"""A page's how-to photos (ticket #477): the scene agent of every scene that uses the product
sees them as if it had opened them, on every turn of its fix, and a scene that doesn't use the
product never does. Driven through the chat from the shop owner's first message, outside
services faked."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import ToolUse, TurnRequest
from jobs.library import library_of
from jobs.models import Job

from .conftest import HeldSteps
from .test_broll_sent_list_end_to_end import (
    PAGE_PHOTOS,
    PICKED,
    PRESSED,
    a_plan,
    item_of,
    make_the_ad,
    page_with_a_video,  # noqa: F401 (a fixture)
    watched,
)
from .test_picker_labels import label, labelled_page  # noqa: F401 (a fixture)
from .test_product_videos import picks_videos, player  # noqa: F401 (fixtures)

pytestmark = [pytest.mark.django_db(transaction=True)]

HOW_TO = label(
    "I103", 4, "how-to", "none", "A guide: fill the bottle, shake, pump twice.", alone="no"
)
PICKED_HOW_TO = {**PICKED, "pictures": [*PAGE_PHOTOS[:3], HOW_TO, PAGE_PHOTOS[4]]}


def scene_of(request: TurnRequest) -> str:
    intro = request.handoff.conversation[0].text  # type: ignore[union-attr]
    return str(intro.split("You own scene ")[1].split(".")[0])


def how_to_shown(request: TurnRequest) -> list[ToolUse]:
    return [
        each
        for each in request.handoff.conversation
        if isinstance(each, ToolUse) and each.call_id == "how-to-photos" and each.pictures
    ]


@pytest.mark.without_end_card_rules
def test_how_to_photos_reach_the_planner_and_every_scene_that_uses_the_product(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811 (the fixture)
) -> None:
    fake_model.answer_unscripted("watch_video", lambda _: watched("black", PRESSED))
    make_the_ad(
        fake_model,
        say,
        steps,
        page_with_a_video,
        lambda job: a_plan(item_of(job, "I104"), 1),
        PICKED_HOW_TO,
    )

    job = Job.objects.get()
    how_to = library_of(job)[item_of(job, "I103") - 1]
    assert how_to.kind == "how-to"
    (planned,) = ModelCall.objects.filter(purpose="plan_ad")
    assert planned.images[-1] == {
        "label": f"Library picture {how_to.number}, a how-to photo, whole",
        "key": how_to.file,
    }
    turns: dict[str, list[TurnRequest]] = {}
    for request in fake_model.turns_taken:
        if request.purpose == "scene_agent":
            turns.setdefault(scene_of(request), []).append(request)
    # Scene 1 talks with the product out of its box; scene 2 does a job with it.
    assert job.scenes.get(number=2).broll_kind == "does a job"
    assert not any(how_to_shown(each) for each in turns["1"])
    assert turns["2"] and all(how_to_shown(each) for each in turns["2"])
    (shown,) = how_to_shown(turns["2"][0])
    assert [picture.key for picture in shown.pictures] == [how_to.file]
    assert "Follow their method" in shown.result
