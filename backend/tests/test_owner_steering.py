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


# An owner's exact line too long for one clip: 14 times "Our mug holds tea", about 20 s.
LONG_OWNER_LINE = " ".join(["Our mug holds tea"] * 14) + "."


def owner_line_too_long(fake_model: FakeModel, say: Callable[..., None]) -> None:
    """After the first ad, the owner gives scene 2 an exact line too long for one clip, and
    the planning checks run on it."""
    from .conftest import NO_CHOICES, facts_ok

    owner_says(
        fake_model,
        say,
        f'Scene 2 must say exactly: "{LONG_OWNER_LINE}"',
        ("edit_scene", {"scene": 2, "line": LONG_OWNER_LINE, "owner_asked": True}),
    )
    fake_model.respond("fact_check", facts_ok(2))
    owner_says(fake_model, say, "Go on.", ("run_planning_checks", NO_CHOICES))


def test_an_owners_too_long_line_is_never_cut_and_the_owner_is_asked(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    owner_line_too_long(fake_model, say)

    assert "shorten_line" not in paid_for()
    assert Scene.objects.get(number=2).line == LONG_OWNER_LINE
    told = answer_of("run_planning_checks")
    assert not told.startswith("The checks passed")
    assert LONG_OWNER_LINE in told and "shorten_owner_line" in told


def test_an_owner_who_chooses_shorten_gets_the_line_shortened_and_is_told(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from .conftest import chat, handoffs

    owner_line_too_long(fake_model, say)
    shorter = "Our mug holds your favourite morning tea, hot and ready."
    fake_model.respond("shorten_line", {"line": shorter})

    owner_says(fake_model, say, "Shorten it.", ("shorten_owner_line", {"scene": 2}))

    assert Scene.objects.get(number=2).line == shorter
    assert LONG_OWNER_LINE in handoffs("shorten_line")[-1]["owner_words"]
    visible = " ".join(text for _, text in chat(api, session_id))
    assert LONG_OWNER_LINE in visible and shorter in visible


def test_an_owner_who_chooses_split_keeps_every_word_across_two_scenes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from .conftest import NO_CHOICES, facts_ok

    owner_line_too_long(fake_model, say)
    words = LONG_OWNER_LINE.split()
    first, second = " ".join(words[:28]), " ".join(words[28:])
    fake_model.respond("fact_check", facts_ok(2, 3))

    owner_says(
        fake_model,
        say,
        "Split it.",
        ("edit_scene", {"scene": 2, "line": first, "owner_asked": True}),
        (
            "add_scene",
            {
                "at": 3,
                "line": second,
                "shows": "A hand lifts the mug from the table.",
                **AS_BROLL,
                "owner_asked": True,
            },
        ),
        ("run_planning_checks", NO_CHOICES),
    )

    lines = [scene.line for scene in Scene.objects.order_by("number")][1:3]
    assert " ".join(lines) == LONG_OWNER_LINE
    assert "shorten_line" not in paid_for()
    assert answer_of("run_planning_checks").startswith("The checks passed")


def test_shortening_an_owner_line_that_already_fits_is_refused(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    line = Scene.objects.get(number=2).line
    owner_says(
        fake_model,
        say,
        f'Scene 2 must say exactly: "{line}"',
        ("edit_scene", {"scene": 2, "line": line, "owner_asked": True}),
        ("shorten_owner_line", {"scene": 2}),
    )

    assert answer_of("shorten_owner_line").startswith("Refused:")
    assert "shorten_line" not in paid_for()
    assert Scene.objects.get(number=2).line == line


def test_an_owner_line_still_too_long_after_shortening_is_handed_back_to_split(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from .conftest import recorded

    owner_line_too_long(fake_model, say)
    still_long = " ".join(["Our mug holds hot tea"] * 10) + "."
    fake_model.respond("shorten_line", *[{"line": still_long}] * 3)

    owner_says(fake_model, say, "Shorten it.", ("shorten_owner_line", {"scene": 2}))

    assert "split it" in answer_of("shorten_owner_line")
    assert any("still takes too long to say" in text for _, text in recorded(api, session_id))


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


def test_an_owner_reorder_records_waived_defaults_for_the_developer(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from .conftest import chat, recorded

    owner_says(
        fake_model,
        say,
        "Open on the mug and move her opening line second.",
        ("reorder_scenes", {"order": [2, 1, 3], "owner_asked": True}),
    )
    assert not answer_of("reorder_scenes").startswith("Refused:")
    detail = "Scene 2 is a middle scene talking in another style."
    assert any(detail in text for _, text in recorded(api, session_id))
    assert not any(detail in text for _, text in chat(api, session_id))


def test_an_exact_owner_line_omitted_by_target_shortening_is_explained(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    api: Any,
    session_id: str,
) -> None:
    from .conftest import NO_CHOICES, chat, facts_ok
    from .test_h3_planning import script_plan
    from .test_producer_broll import BROLL_PLAN, assemble_through_the_chat

    unchanged = {
        "lines": [
            {"scene": number, "line": scene["line"]}
            for number, scene in enumerate(BROLL_PLAN["plan"]["scenes"], 1)
        ]
    }
    script_plan(fake_model, product_page_url, BROLL_PLAN, target=12, checks=True)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    fake_model.respond("shorten_script", unchanged, unchanged)
    say(f"Make a 12 second ad for {product_page_url}")
    owner_says(fake_model, say, "Make the presenter.", ("create_person", {}))
    assemble_through_the_chat(fake_model, steps, say)
    removed = Scene.objects.get(number=2)
    kept_last = Scene.objects.get(number=3)
    old_clip = ProducedItem.objects.filter(scene=removed, kind="clip").latest("version")
    last_clip = ProducedItem.objects.filter(scene=kept_last, kind="clip").latest("version")
    original = "The mug holds your favourite hot drink at home today and every day."
    owner_says(
        fake_model,
        say,
        f'Scene 2 must say exactly "{original}".',
        ("edit_scene", {"scene": 2, "line": original, "owner_asked": True}),
    )
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("shorten_script", {"lines": [unchanged["lines"][0], unchanged["lines"][2]]})
    owner_says(fake_model, say, "Go on.", ("run_planning_checks", NO_CHOICES))
    visible = " ".join(text for _, text in chat(api, session_id))
    assert f'I left out "{original}"' in visible
    assert "12-second target length" in visible
    # Paid history stays with its original scene; the closing scene keeps its identity.
    assert Scene.with_removed.get(pk=removed.pk).removed_at is not None
    old_clip.refresh_from_db()
    assert old_clip.scene_id == removed.pk
    assert Scene.objects.get(number=2).pk == kept_last.pk
    last_clip.refresh_from_db()
    assert last_clip.scene_id == kept_last.pk
    from chat.models import Attachment

    assert Attachment.objects.get(file=old_clip.file).scene is None
    assert Attachment.objects.get(file=last_clip.file).scene == 2
