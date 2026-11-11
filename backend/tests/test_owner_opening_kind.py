"""An owner's opening kind survives later automatic fixes (#550, #563, #567)."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem, Scene

from .conftest import NO_CHOICES, HeldSteps, facts_ok, paid_for, results_of
from .test_producer_broll import checked, run  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_producer_plan_edits import AS_BROLL, answer_of, owner_says
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)
OWNER_NOTE = "Make the opening a close view of the product instead of her talking."
PLANNED = "The mug sits on a sunny windowsill."
AUTOMATIC = "A hand rests the mug on a folded cloth."


def choose_product_opening(fake_model: FakeModel, say: Callable[..., None]) -> None:
    owner_says(
        fake_model,
        say,
        OWNER_NOTE,
        ("edit_scene", {"scene": 1, "shows": PLANNED, **AS_BROLL, "owner_asked": True}),
    )
    assert not answer_of("edit_scene").startswith("Refused:"), answer_of("edit_scene")


def test_a_later_producer_action_edit_keeps_the_owner_chosen_opening_kind(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    choose_product_opening(fake_model, say)
    before = paid_for()
    owner_says(
        fake_model,
        say,
        "Carry on.",
        ("edit_scene", {"scene": 1, "shows": AUTOMATIC, "owner_asked": False}),
    )
    assert not answer_of("edit_scene").startswith("Refused:"), answer_of("edit_scene")
    first = Scene.objects.get(number=1)
    assert (first.kind, first.shows) == ("broll", AUTOMATIC)
    assert paid_for() == before


def test_an_automatic_scene_run_changes_the_action_without_reverting_the_owner_opening_kind(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    choose_product_opening(fake_model, say)
    fake_model.respond("fact_check", facts_ok(1), facts_ok(1))
    owner_says(fake_model, say, "Check the chosen opening.", ("run_planning_checks", NO_CHOICES))
    first = Scene.objects.get(number=1)
    changed = change(line=first.line, shows=AUTOMATIC)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[changed]),
        turn(calls=[changed]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    owner_says(
        fake_model,
        say,
        "Carry on.",
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 1,
                        "instruction": AUTOMATIC,
                        "quotes": [OWNER_NOTE],
                        "owner_asked": False,
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    assert results_of("change_scene")[-1].startswith("The scene row changed:"), results_of(
        "change_scene"
    )
    first.refresh_from_db()
    assert (first.kind, first.shows) == ("broll", AUTOMATIC)


def test_an_automatic_run_cannot_newly_change_the_opening_to_broll(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    first = Scene.objects.get(number=1)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                change(
                    line=first.line, shows=AUTOMATIC, kind="broll", kind_why="A close product view."
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Keep the planned opening."})]),
    )
    owner_says(
        fake_model,
        say,
        "Continue automatically.",
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 1,
                        "instruction": "Try a closer product view.",
                        "quotes": ["Continue automatically."],
                        "owner_asked": False,
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    assert results_of("change_scene")[-1].startswith("Refused:")
    assert "first scene" in results_of("change_scene")[-1]
    first.refresh_from_db()
    assert first.kind == "talking"
