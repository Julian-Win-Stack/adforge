"""Owner steering through chat, with only outside services faked (#550)."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel
from jobs.models import ProducedItem, Scene

from .conftest import HeldSteps, paid_for
from .test_producer_broll import checked  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_producer_plan_edits import AS_BROLL, answer_of, owner_says

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("tool", "arguments", "kind_order"),
    [
        (
            "edit_scene",
            {"scene": 1, "shows": "The mug sits on a windowsill.", **AS_BROLL},
            ["broll", "broll", "talking"],
        ),
        ("remove_scene", {"scene": 3}, ["talking", "broll"]),
        ("remove_scene", {"scene": 1}, ["broll", "talking"]),
        ("reorder_scenes", {"order": [2, 1, 3]}, ["broll", "talking", "talking"]),
        (
            "edit_scene",
            {"scene": 3, "line": "Pick up your mug from our shop today, and enjoy every sip."},
            ["talking", "broll", "talking"],
        ),
    ],
)
def test_an_owner_requested_plan_change_after_the_ad_is_shown_is_kept_without_spending(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    tool: str,
    arguments: dict[str, Any],
    kind_order: list[str],
) -> None:
    before = paid_for()
    owner_says(
        fake_model, say, "Change my ad this way.", (tool, {**arguments, "owner_asked": True})
    )
    assert not answer_of(tool).startswith("Refused:"), answer_of(tool)
    assert list(Scene.objects.order_by("number").values_list("kind", flat=True)) == kind_order
    assert paid_for() == before
    assert steps.held == []


@pytest.mark.parametrize(
    ("purpose", "owner_line", "reason"),
    [
        ("lengthen_line", "Our mug.", "short"),
        ("shorten_line", " ".join(["Our mug holds tea"] * 14) + ".", "long"),
        (
            "rewrite_line",
            "This mug costs nineteen dollars and holds your favourite morning tea.",
            "page",
        ),
    ],
)
def test_owner_words_reach_the_line_rewriter_and_a_forced_change_is_explained(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    api: Any,
    session_id: str,
    purpose: str,
    owner_line: str,
    reason: str,
) -> None:
    from .conftest import NO_CHOICES, chat, facts_ok, handoffs
    from .test_producer_checks import failing

    corrected = "This mug costs twenty four dollars and holds your favourite morning tea."
    owner_says(
        fake_model,
        say,
        f'Scene 2 must say exactly: "{owner_line}"',
        ("edit_scene", {"scene": 2, "line": owner_line, "owner_asked": True}),
    )
    if purpose == "rewrite_line":
        fake_model.respond("fact_check", failing(2, "The price is wrong."), facts_ok(2))
        fake_model.respond(
            purpose,
            {
                "line": corrected,
                "shows": Scene.objects.get(number=2).shows,
                "broll_kind": "showcase",
                "person_shown": "no face",
            },
        )
    else:
        fake_model.respond("fact_check", facts_ok(2), facts_ok(2))
        fake_model.respond(purpose, {"line": corrected})
    owner_says(fake_model, say, "Go on.", ("run_planning_checks", NO_CHOICES))
    assert owner_line in handoffs(purpose)[-1]["owner_words"]
    visible = " ".join(text for _, text in chat(api, session_id))
    assert owner_line in visible and corrected in visible
    assert reason in visible.lower()


def test_a_second_rewrite_keeps_the_original_owner_line_after_the_first_rewrite(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from .conftest import NO_CHOICES, facts_ok, handoffs
    from .test_producer_checks import failing

    original = " ".join(["Our mug holds tea"] * 14) + "."
    shorter = "This mug costs nineteen dollars and holds your favourite morning tea."
    corrected = "This mug costs twenty four dollars and holds your favourite morning tea."
    owner_says(
        fake_model,
        say,
        f'Use exactly "{original}" for scene 2.',
        ("edit_scene", {"scene": 2, "line": original, "owner_asked": True}),
    )
    fake_model.respond("fact_check", facts_ok(2), failing(2, "The price is wrong."), facts_ok(2))
    fake_model.respond("shorten_line", {"line": shorter})
    fake_model.respond(
        "rewrite_line",
        {
            "line": corrected,
            "shows": Scene.objects.get(number=2).shows,
            "broll_kind": "showcase",
            "person_shown": "no face",
        },
    )
    owner_says(fake_model, say, "Go on.", ("run_planning_checks", NO_CHOICES))
    assert original in handoffs("shorten_line")[-1]["owner_words"]
    assert original in handoffs("rewrite_line")[-1]["owner_words"]


def test_a_scene_agent_explains_why_it_changes_the_owners_exact_line(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    steps: HeldSteps,
    api: Any,
    session_id: str,
) -> None:
    from gateway.fake import turn

    from .conftest import chat, facts_ok, handoffs
    from .test_producer_broll import run
    from .test_scene_agent_changes import change
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    scene = Scene.objects.get(number=2)
    original = scene.line
    owner_says(
        fake_model,
        say,
        f'Keep exactly "{original}" in scene 2.',
        ("edit_scene", {"scene": 2, "line": original, "owner_asked": True}),
    )
    corrected = "The mug holds your favourite hot drink at home today and every day."
    why = "the earlier line was contradicted by the product page’s description."
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[change(line=corrected, shows=scene.shows, line_change_reason=why)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    note = "Fix the line if the page contradicts it."
    owner_says(
        fake_model,
        say,
        note,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": note, "quotes": [note], "owner_asked": True}]},
        ),
    )
    run(fake_model, steps)
    assert original in str(handoffs("scene_agent")[-1])
    visible = " ".join(text for _, text in chat(api, session_id))
    assert corrected in visible and why in visible


def test_an_owner_mark_does_not_lift_the_opening_lock_before_the_first_ad(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    say: Callable[..., None],
) -> None:
    owner_says(
        fake_model,
        say,
        "Open on the mug.",
        (
            "edit_scene",
            {"scene": 1, "shows": "The mug sits on a windowsill.", **AS_BROLL, "owner_asked": True},
        ),
    )
    assert answer_of("edit_scene").startswith("Refused:")
    assert "first scene" in answer_of("edit_scene")
    assert Scene.objects.get(number=1).kind == "talking"


def test_owner_steering_keeps_the_picture_evidence_checks(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    owner_says(
        fake_model,
        say,
        "Show its box.",
        (
            "edit_scene",
            {
                "scene": 1,
                "product_state": "in its box",
                "packaging_photo": 400,
                "owner_asked": True,
            },
        ),
    )
    assert answer_of("edit_scene").startswith("Refused:")
    assert "no such picture" in answer_of("edit_scene")
    assert Scene.objects.get(number=1).packaging_photo != 400


def test_words_first_supplied_as_a_note_survive_a_fact_rewrite_then_lengthening(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from .conftest import NO_CHOICES, facts_ok, handoffs
    from .test_producer_checks import failing

    original = Scene.objects.get(number=2).line
    owner_says(
        fake_model,
        say,
        f'Keep exactly "{original}", and show the handle.',
        (
            "edit_scene",
            {
                "scene": 2,
                "shows": "A hand turns the mug to reveal the handle.",
                "owner_asked": True,
            },
        ),
    )
    fake_model.respond(
        "fact_check", failing(2, "The line contradicts the page."), facts_ok(2), facts_ok(2)
    )
    fake_model.respond(
        "rewrite_line",
        {
            "line": "A mug.",
            "shows": Scene.objects.get(number=2).shows,
            "broll_kind": "showcase",
            "person_shown": "no face",
        },
    )
    fake_model.respond(
        "lengthen_line",
        {"line": "The mug holds your favourite hot drink at home today and every day."},
    )
    owner_says(fake_model, say, "Go on.", ("run_planning_checks", NO_CHOICES))
    assert original in handoffs("rewrite_line")[-1]["owner_words"]
    assert original in handoffs("lengthen_line")[-1]["owner_words"]
