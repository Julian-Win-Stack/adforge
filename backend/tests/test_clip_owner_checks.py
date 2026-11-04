"""An optional owner check on an unsure clip, through the chat and its scene card."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import CLIP_PRODUCT_OK, HeldSteps, handoffs, paid_for
from .test_h3_scene_film import h3_agent_ready  # noqa: F401
from .test_h3_scene_words import LINE, TALKING_PICTURE, words_ready  # noqa: F401
from .test_producer_broll import calling, run
from .test_scene_film import FILM
from .test_scene_pictures import picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
WHY = "the underside of the handle"


@pytest.fixture
def unsure_clip(
    fake_model: FakeModel,
    words_ready: Job,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> dict[str, Any]:
    fake_model.respond("transcribe_line", {"text": LINE})
    fake_model.answer_unscripted(
        "check_clip_product",
        lambda _: {**CLIP_PRODUCT_OK, "verdict": "unsure", "references_never_show": WHY},
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    clip: dict[str, Any] = card["versions"][-1]
    assert clip["verdict"] == "unsure"
    return clip


def test_looks_right_marks_the_clip_and_chat_without_starting_anything(
    unsure_clip: dict[str, Any], api: APIClient, session_id: str, steps: HeldSteps
) -> None:
    before = paid_for()
    response = api.post(
        f"/api/sessions/{session_id}/scenes/2/check/",
        {"clip": unsure_clip["clip"], "choice": "right"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["versions"][-1]["owner_check"] == "right"
    [notice] = [
        row for row in api.get(f"/api/sessions/{session_id}/messages/").json() if row.get("watch")
    ]
    assert notice["watch"]["owner_check"] == "right"
    assert paid_for() == before
    assert not steps.held
    again = api.post(
        f"/api/sessions/{session_id}/scenes/2/check/",
        {"clip": unsure_clip["clip"], "choice": "right"},
        format="json",
    )
    assert again.status_code == 200
    assert paid_for() == before


def test_looks_wrong_starts_a_scene_note_with_the_judges_reason_and_owner_words(
    unsure_clip: dict[str, Any],
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    steps: HeldSteps,
) -> None:
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
                                "instruction": "Fix the clip the owner marked wrong.",
                                "quotes": [
                                    "This clip looks wrong.",
                                    "The handle should be on the other side.",
                                ],
                            }
                        ]
                    },
                )
            ]
        ),
        turn(says="I passed your check on."),
        turn(says="The scene is ready."),
    )
    response = api.post(
        f"/api/sessions/{session_id}/scenes/2/check/",
        {
            "clip": unsure_clip["clip"],
            "choice": "wrong",
            "note": "The handle should be on the other side.",
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert len(steps.held) == 1
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Need a new view."})]))
    run(fake_model, steps)
    given = str(handoffs("scene_agent")[-1])
    assert "looks wrong" in given
    assert WHY in given
    assert "The handle should be on the other side." in given
    assert response.json()["versions"][-1]["owner_check"] == "wrong"
