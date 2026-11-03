"""How the owner-notes evals read round 4's ads from their folders and pace their picture
calls. Plain checks kept apart from tests/test_owner_notes.py so they run without a database."""

import time
from pathlib import Path
from typing import Any

import pytest

from evals.owner_notes import provider as provider_module
from evals.owner_notes.provider import OwnerNotesProvider
from evals.owner_notes.saved_ads import as_planned, saved_ads
from gateway.fake import FakeModel
from gateway.types import ModelRequest
from jobs.planning import PlanHandoff, producer_decision_for

from .conftest import a_plan_with
from .test_owner_notes import saved_round_4
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import assembled  # noqa: F401 (a fixture)


def test_round_4s_ads_are_read_from_their_folders(tmp_path: Path) -> None:
    ads = saved_ads(saved_round_4(tmp_path))

    ad = ads["8"]
    assert (ad.product_name, ad.url, ad.answers) == (
        "Molly's Suds Cleaner",
        "https://shop.test/cleaner",
        ["It's in stock."],
    )
    talking, broll = ad.scenes
    assert (talking.number, talking.line, talking.talking) == (1, "Rings again?", True)
    assert (broll.shows, broll.motion_prompt, broll.picture_prompt) == (
        "A brush scrubs the ring.",
        "The brush scrubs the ring once.",
        "A white toilet.",
    )
    assert broll.picture == b"scene-2"
    # Le Duo is graded, but has no folder here.
    assert list(ads) == ["8"]


def test_a_saved_scene_is_planned_with_only_the_photos_the_page_has_now(tmp_path: Path) -> None:
    broll = saved_ads(saved_round_4(tmp_path))["8"].scene(2)

    assert as_planned(broll, photo_count=5)["needs"] == [{"what": "the gel", "photos": [2]}]
    assert as_planned(broll, photo_count=1)["needs"] == []
    assert as_planned(saved_ads(tmp_path)["8"].scene(1), photo_count=5) == {"line": "Rings again?"}


def test_the_first_version_gets_round_4s_scenes_in_the_planners_plan(tmp_path: Path) -> None:
    ad = saved_ads(saved_round_4(tmp_path))["8"]
    planner = FakeModel()
    provider = OwnerNotesProvider(ad, real=planner)
    planner.respond(
        "plan_ad",
        a_plan_with(
            scenes=[
                {"line": "Rings A", "part": "hook", "overlay": "New"},
                {"line": "B", "part": "proof", "overlay": None},
                {"line": "C", "part": "call to action", "overlay": "$9"},
            ],
            product_name="Rings",
        ),
    )
    request = ModelRequest(
        purpose="plan_ad",
        model="planner",
        instructions="",
        handoff=PlanHandoff(
            moments="",
            library_pictures=9,
            pictures_not_shown=0,
            video_transcripts=[],
            page_text="",
            product_url=ad.url,
            target_seconds=None,
            conversation=[],
        ),
        output=producer_decision_for(library=range(1, 10), moments={}),
    )

    planned = provider.complete(request).output.model_dump()
    scenes = planned["plan"]["scenes"]

    assert [scene["line"] for scene in scenes] == ["Rings again?", "It fights rings."]
    # The last scene keeps the planner's last part, whatever came between.
    assert [(scene["part"], scene["overlay"]) for scene in scenes] == [
        ("hook", "New"),
        ("call to action", "$9"),
    ]
    assert scenes[1]["needs"] == [{"what": "the gel", "photos": [2, 7]}]


def test_the_first_version_keeps_round_4s_lines_through_the_fact_check(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from jobs.checks import FactCheckHandoff, LineToCheck, fact_check_for

    checker = FakeModel()
    provider = OwnerNotesProvider(saved_ads(saved_round_4(tmp_path))["8"], real=checker)
    handoff = FactCheckHandoff(
        page_text="",
        conversation=[],
        product_colour="white",
        lines=[
            LineToCheck(scene=1, line="Rings again?"),
            LineToCheck(scene=2, line="It fights rings.", shows="A brush scrubs the ring."),
        ],
        video_transcripts=[],
    )
    request: Any = SimpleNamespace(
        purpose="fact_check", handoff=handoff, output=fact_check_for([1, 2], showing=[2])
    )

    answer = provider.complete(request).output

    assert [line.verdict for line in answer.lines] == ["ok", "ok"]
    # The notes' fact checks are the real model's.
    checker.respond("fact_check", {**answer.model_dump(), "reason": "checked today"})
    provider.start_notes(2.0)
    assert provider.complete(request).output.reason == "checked today"


def test_pictures_for_the_notes_are_asked_for_one_at_a_time_and_spaced_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(provider_module, "PICTURE_LOCK", tmp_path / "pictures.lock")
    monkeypatch.setattr(provider_module, "PICTURE_GAP_SECONDS", 0.5)
    provider = OwnerNotesProvider(saved_ads(saved_round_4(tmp_path))["8"], real=FakeModel())
    provider.start_notes(2.0)

    started = time.monotonic()
    for _ in range(3):
        provider.draw(model="pictures", prompt="A white toilet.")

    # The first is drawn at once, each after it waits out the gap.
    assert time.monotonic() - started >= 1.0
