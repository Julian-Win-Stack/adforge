"""Producer doors select scene agents for B-roll and keep talking-scene tools."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel

from .conftest import HeldSteps, results_of
from .test_producer_broll import calling, checked  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_broll_clip_and_picture_tools_name_the_scene_agent_instead(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    calling(
        fake_model,
        say,
        ("make_clip", {"scene": 2}),
        ("make_starting_picture", {"scene": 2, "note": None}),
    )

    assert not steps.held
    assert "start_scene_agent" in results_of("make_clip")[-1]
    assert "agent draws its starting picture" in results_of("make_starting_picture")[-1]


def test_producer_keeps_owner_exclusions_but_the_scene_agent_picks_the_photos(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from gateway.fake import turn

    from .test_producer_broll import run
    from .test_scene_pictures import PICTURE_FORM

    calling(
        fake_model,
        say,
        ("set_references", {"scene": 2, "pictures": [{"number": 1, "for": "state"}]}),
    )
    assert "scene agent" in str(results_of("set_references"))
    calling(fake_model, say, ("set_references", {"scene": 2, "exclude": [1]}))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "The needed view is excluded."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the cup.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert "excluded photo cannot be picked" in str(results_of("draw_starting_picture"))


def test_a_finished_scene_agent_shows_its_clip_once_in_the_owners_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: object,
    session_id: str,
) -> None:
    from typing import cast

    from rest_framework.test import APIClient

    from .scene_script import make_scene_clip

    make_scene_clip(fake_model, steps, say)
    messages = cast(APIClient, api).get(f"/api/sessions/{session_id}/").json()["messages"]
    clips = [
        attachment
        for message in messages
        for attachment in message["attachments"]
        if attachment["kind"] == "video" and attachment["scene"] == 2
    ]
    assert len(clips) == 1
    assert clips[0]["sent"]["starting_picture"]
