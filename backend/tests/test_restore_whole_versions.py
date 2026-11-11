"""Whole versions return through the card and chat without another paid check."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Scene

from .conftest import HeldSteps, facts_ok, paid_for, results_of
from .test_card_versions import cards
from .test_pick_from_tries import current_clip, use_on_card
from .test_producer_broll import checked, run  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_producer_picture_then_clip import ads
from .test_producer_plan_edits import owner_says

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("via", ["card", "chat"])
@pytest.mark.parametrize("change_line", [False, True], ids=["action", "line-and-action"])
def test_swapping_restores_the_clip_and_scene_without_paying_again(
    shown: object,  # noqa: F811
    steps: HeldSteps,
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    say: Callable[..., None],
    via: str,
    change_line: bool,
) -> None:
    scene = Scene.objects.get(number=2)
    original = (scene.line, scene.shows, scene.result, scene.broll_kind)
    old = cards(api, session_id)[2][0]
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fields = {
        "line": scene.line,
        "shows": "The mug stands on a shelf.",
        "usage": "",
        "result": "",
        "broll_kind": "showcase",
        "person_shown": "no face",
    }
    if change_line:
        fields["line"] = "Hand-thrown and dishwasher safe, a mug for your daily tea at home."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", fields)]),
        *([] if change_line else [turn(calls=[("change_scene", fields)])]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    owner_says(
        fake_model,
        say,
        "Show the mug on a shelf.",
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": "Show the mug on a shelf.",
                        "quotes": ["Show the mug on a shelf."],
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    scene.refresh_from_db()
    assert scene.shows == fields["shows"], results_of("change_scene")
    paid = paid_for()

    if via == "card":
        response = use_on_card(api, session_id, fake_model, 1)
        assert response.status_code == 200, response.json()
    else:
        owner_says(
            fake_model, say, "Put the old one back", ("use_version", {"scene": 2, "version": 1})
        )

    scene.refresh_from_db()
    assert (scene.line, scene.shows, scene.result, scene.broll_kind) == original
    assert current_clip(2).pk != old["clip"]
    assert cards(api, session_id)[2][0]["in_ad"]
    assert paid_for() == paid
    assert scene.fact_checked
    assert [cut["clip"] for cut in ads()[-1].cuts if cut["scene"] == 2] == [current_clip(2).pk]


def test_old_voice_version_quotes_refilm_before_restoring_anything(
    shown: object,  # noqa: F811
    fake_model: FakeModel,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from agents.models import ToolCall
    from chat.models import Message

    proposal = {"remake": "voice", "voice": "A soft, measured woman's voice."}
    owner_says(fake_model, say, "Change her voice.", ("remake_person", proposal))
    quote = ToolCall.objects.filter(tool="remake_person").latest("pk")
    owner_says(
        fake_model,
        say,
        "Yes, change the voice",
        (
            "remake_person",
            {**proposal, "cost_quote": quote.pk, "confirmation": "Yes, change the voice"},
        ),
    )
    before = current_clip(2)
    paid = paid_for()
    owner_says(fake_model, say, "Put the old one back", ("use_version", {"scene": 2, "version": 1}))
    assert paid_for() == paid
    assert current_clip(2) == before
    version = cards(api, session_id)[2][0]
    assert not version["free"]
    assert "old voice" in version["refilm"] and "$" in version["refilm"]
    notices = list(
        Message.objects.filter(role="agent", text__contains="old voice").values_list(
            "text", flat=True
        )
    )
    assert any("$" in notice and "refilm" in notice for notice in notices)
    refilm_quote = ToolCall.objects.filter(tool="use_version").latest("pk")
    owner_says(
        fake_model,
        say,
        "Yes, refilm that version",
        (
            "use_version",
            {
                "scene": 2,
                "version": 1,
                "cost_quote": refilm_quote.pk,
                "confirmation": "Yes, refilm that version",
            },
        ),
    )
    assert Scene.objects.get(number=2).status == Scene.Status.PLANNED
    assert paid_for() == paid
