"""TRY (throwaway, never merged): the faked ad with one how-to photo; prints what each scene
agent's first turn is shown."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel
from gateway.types import ToolUse
from jobs.models import Job

from .conftest import HeldSteps
from .test_broll_sent_list_end_to_end import (
    PAGE_PHOTOS,
    PICKED,
    PRESSED,
    a_plan,
    item_of,
    make_the_ad,
    page_with_a_video,  # noqa: F401
    watched,
)
from .test_picker_labels import label, labelled_page  # noqa: F401
from .test_product_videos import picks_videos, player  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]

HOW_TO = label("I103", 4, "how-to", "none", "A guide: fill the bottle, shake, pump twice.", alone="no")
PICKED_HOW_TO = {**PICKED, "pictures": [*PAGE_PHOTOS[:3], HOW_TO, PAGE_PHOTOS[4]]}


@pytest.mark.without_end_card_rules
def test_try_how_to_photos_in_scene_context(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,  # noqa: F811
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
    for scene in job.scenes.order_by("number"):
        print("SCENE", scene.number, scene.kind, scene.broll_kind, repr(scene.usage), scene.product_state)
    print("LIBRARY", [(i.number, i.kind) for i in job.library.order_by("number")])
    firsts: dict[str, Any] = {}
    for request in fake_model.turns_taken:
        if request.purpose != "scene_agent":
            continue
        intro = request.handoff.conversation[0].text  # type: ignore[union-attr]
        scene = intro.split("You own scene ")[1].split(".")[0]
        if scene in firsts:
            continue
        firsts[scene] = request
        print("=== scene", scene, "first turn")
        for each in request.handoff.conversation[1:]:
            if isinstance(each, ToolUse):
                print(" ToolUse", each.tool, each.call_id, [p.label for p in each.pictures])
                print("  result:", each.result[:400])
    how_to = [
        each
        for each in firsts["2"].handoff.conversation
        if isinstance(each, ToolUse) and each.call_id == "how-to-photos"
    ]
    assert len(how_to) == 1
    for scene in ("1",):
        assert not any(
            isinstance(each, ToolUse) and each.call_id == "how-to-photos"
            for each in firsts[scene].handoff.conversation
        )
    # Later turns of scene 2 keep it.
    later = [
        r
        for r in fake_model.turns_taken
        if r.purpose == "scene_agent" and "You own scene 2." in r.handoff.conversation[0].text  # type: ignore[union-attr]
    ]
    print("scene 2 turns", len(later))
    assert all(
        any(isinstance(e, ToolUse) and e.call_id == "how-to-photos" and e.pictures for e in r.handoff.conversation)
        for r in later
    )
