"""Scene questions travel through the same owner chat as production and notes."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, handoffs, recorded, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("legacy_path")]
QUESTION = "Could you send a photo of the underside?"
ASK = ("ask_owner", {"question": QUESTION, "wants": "photo"})


def test_a_scene_asks_once_per_run_and_keeps_working(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[ASK]),
        turn(calls=[ASK]),
        turn(calls=[("give_up", {"reason": "The underside is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its base.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    results = results_of("ask_owner")
    assert "asked" in results[0].lower()
    assert results[1].startswith("Refused:")
    assert sum(QUESTION in text for _, text in recorded(api, session_id)) == 1
    assert "The underside is needed" in str(handoffs("produce"))


def test_photo_answer_after_scene_gives_up_opens_a_fresh_run_in_the_same_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.library import OwnerPhoto, library_of
    from jobs.models import Job

    from .conftest import picture

    fake_model.respond(
        "scene_agent",
        turn(calls=[ASK]),
        turn(calls=[("give_up", {"reason": "The underside needs a photo."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the base.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "pass_note",
                    {
                        "scenes": [
                            {
                                "scene": 2,
                                "instruction": "Use the owner's underside photo.",
                                "quotes": ["Here is its underside"],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="Thanks."),
    )
    say("Here is its underside.", ("underside.png", picture(40, 30, (20, 160, 40))))
    fake_model.respond(
        "scene_agent",
        turn(calls=[ASK]),
        turn(calls=[("give_up", {"reason": "This photo still misses a needed view."})]),
    )
    run(fake_model, steps)
    assert any(isinstance(item.source, OwnerPhoto) for item in library_of(Job.objects.get()))
    assert "Owner's words (checked word for word):\\nHere is its underside" in str(
        handoffs("scene_agent")[-1]
    )
    assert "Their photo is library item" in str(handoffs("scene_agent")[-1])
    assert "The underside needs a photo" in str(handoffs("scene_agent")[-1])
    assert not results_of("ask_owner")[-1].startswith("Refused:")
