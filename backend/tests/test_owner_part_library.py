"""Drawn parts enter the library, are shown as information and film without approval."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge.retry import OutsideServiceDown
from agents.models import ToolCall
from chat.models import Message
from gateway.fake import FakeModel, turn
from jobs import owner_parts
from jobs.library import LibraryEntry, OwnerPart, library_of
from jobs.models import Job, Scene, SceneStep

from .conftest import (
    NO_CHOICES,
    PLAN,
    HeldSteps,
    broll,
    facts_ok,
    handoffs,
    photo,
    results_of,
)
from .part_scene import part_scene_agent  # noqa: F401
from .test_producer_broll import DOES_A_JOB, calling, made_ready, run

pytestmark = [pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def recolours_pass(fake_model: FakeModel) -> None:
    """The owner part is part of the product, so it is recoloured to the clip's colour like
    the product's photos (cut_and_send.in_their_own_colour); each recolour passes its check."""


PART = {
    "what": "the lid",
    "source": "owner's words",
    "looks": "A round wooden lid with a small knob on top.",
}

# The mug plan with scenes 2 and 3 showing the mug with its lid, which no photo shows, and
# its talking closing scene last, as the last scene always is.
PART_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            PLAN["plan"]["scenes"][0],
            broll(
                {
                    "line": "Hand-thrown, holds 350 ml, and dishwasher safe too. "
                    "At home, every day.",
                    "shows": "Hot tea poured into the mug, its lid beside it.",
                    **DOES_A_JOB,
                    "usage": "Hot tea is poured into the mug from a teapot.",
                    "result": "The mug is full of steaming tea.",
                    "owner_part": PART,
                }
            ),
            broll(
                {
                    "line": "Yours today for twenty four dollars, with its matching lid. "
                    "At home, every day.",
                    "shows": "The mug with its lid on, on a kitchen shelf.",
                    "overlay": "$24.00",
                    "owner_part": PART,
                }
            ),
            PLAN["plan"]["scenes"][2],
        ],
    },
}

# The part plan's fact check, when all four lines match the page.
FACTS_OK = facts_ok(1, 2, 3, 4)


@pytest.fixture
def planned(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """The shop owner's chat, from the first message: the page read, then the ad planned
    with the part in scenes 2 and 3."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PART_PLAN)
    say("Plan it")


@pytest.fixture
def ready(planned: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """The person made, the script checked, and scene 2's starting picture, audio and
    transcript made."""
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    made_ready(fake_model, steps, say, (2,))


def scene(number: int) -> Scene:
    return Scene.objects.get(number=number)


def part_items() -> list[LibraryEntry]:
    return [item for item in library_of(Job.objects.get()) if isinstance(item.source, OwnerPart)]


def clips_of(number: int) -> int:
    return Job.objects.get().produced.filter(kind="clip", scene__number=number).count()


def part_notices() -> list[Message]:
    return list(
        Message.objects.filter(role="notice", text__contains="which none of your photos shows")
    )


def owner_says(
    fake_model: FakeModel, say: Callable[..., None], text: str, *calls: tuple[str, dict[str, Any]]
) -> None:
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say(text)


# --- Drawn from the plan -----------------------------------------------------------------------


@pytest.mark.usefixtures("planned")
@pytest.mark.without_end_card_rules
def test_the_plan_draws_the_parts_picture_into_the_library_and_shows_it() -> None:
    (item,) = part_items()
    assert item.source == OwnerPart(part="lid")
    # Labelled, as any library picture is.
    assert item.label
    # Drawn once from the part's sentence beside a product photo, and checked once.
    (drawing,) = handoffs("make_part_picture")
    assert PART["looks"] in drawing["prompt"]
    assert len(drawing["pictures"]) == 1
    (checked,) = handoffs("check_part_picture")
    assert checked["looks"] == PART["looks"]
    # Shown to the shop owner, once, with the picture.
    (shown,) = part_notices()
    assert [attached.file for attached in shown.attachments.all()] == [item.file]
    # Both scenes holding the part take the same current picture.
    for number in (2, 3):
        assert owner_parts.part_picture(scene(number)) == item
        assert owner_parts.picture_is_current(scene(number))
    (planned,) = results_of("plan_ad")
    assert f"library picture {item.number}" in planned
    assert "say yes" not in planned


@pytest.mark.without_end_card_rules
def test_an_invented_part_is_drawn_and_filmed_in_one_owner_turn(
    fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Callable[..., None]
) -> None:
    from copy import deepcopy

    plan = deepcopy(PART_PLAN)
    for row in plan["plan"]["scenes"]:
        if row.get("owner_part"):
            row["owner_part"]["source"] = "invented"
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(
            calls=[
                (
                    "start_scene_agent",
                    {
                        "scene": 2,
                        "instruction": "Show the planned lid in use.",
                        "quotes": ["Make the ad"],
                    },
                )
            ]
        ),
        turn(says="I drew the lid and started filming."),
    )
    fake_model.respond("plan_ad", plan)
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the ad")
    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    run(fake_model, steps)
    assert clips_of(2) == 1
    assert not ToolCall.objects.filter(tool="approve_owner_part").exists()
    notices = part_notices()
    assert len(notices) == 1
    assert "?" not in notices[0].text
    assert "say yes" not in notices[0].text.lower()


# --- Corrections -------------------------------------------------------------------------------


@pytest.mark.usefixtures("ready")
@pytest.mark.without_end_card_rules
def test_a_correction_in_words_redraws_one_item_and_films_without_approval(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    (before,) = part_items()
    looks = "A round pink lid with a small knob on top."

    owner_says(
        fake_model,
        say,
        "Make it pink.",
        ("correct_owner_part", {"scene": 2, "words": looks, "photo": None}),
    )

    (after,) = part_items()
    assert after.number == before.number
    assert after.file != before.file
    assert looks in handoffs("make_part_picture")[-1]["prompt"]
    assert len(part_notices()) == 2
    for number in (2, 3):
        assert owner_parts.owner_part(scene(number)).looks == looks  # type: ignore[union-attr]
        assert owner_parts.part_picture(scene(number)) == after
        assert owner_parts.picture_is_current(scene(number))
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    run(fake_model, steps)
    assert clips_of(2) == 1


@pytest.mark.usefixtures("ready")
@pytest.mark.without_end_card_rules
def test_the_shop_owners_photo_of_the_part_is_cleaned_and_used_instead_of_a_drawing(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    drawings = len(handoffs("make_part_picture"))
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": None, "photo": 1})]),
        turn(says="On it."),
    )
    say("Here is the lid.", ("lid.png", photo(9)))

    (cleaned,) = handoffs("make_reference_photo_picture")
    (item,) = part_items()
    assert len(handoffs("make_part_picture")) == drawings
    assert item.file == scene(2).reference_photos.get().drawn
    assert cleaned["pictures"] == [scene(2).reference_photos.get().file]
    assert "cleaned from your photo of it" in part_notices()[-1].text


@pytest.mark.usefixtures("ready")
@pytest.mark.without_end_card_rules
def test_the_shop_owners_photo_with_words_is_changed_as_they_said(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    looks = "The lid from the photo, in pink."
    fake_model.respond(
        "produce",
        turn(calls=[("correct_owner_part", {"scene": 2, "words": looks, "photo": 1})]),
        turn(says="On it."),
    )
    say("Take the lid from this, make it pink.", ("lid.png", photo(9)))

    row = scene(2).reference_photos.get()
    changed = handoffs("make_part_picture")[-1]
    assert changed["pictures"] == [row.drawn]
    assert looks in changed["prompt"]
    (item,) = part_items()
    assert item.file != row.drawn


# --- When it can't be drawn --------------------------------------------------------------------

PART_WRONG = {
    "picture_shows": "The whole mug with a lid on it.",
    "verdict": "fail",
    "problem": "The whole mug is drawn, not the lid alone.",
}


@pytest.mark.without_end_card_rules
def test_an_exhausted_part_drawing_is_recorded_without_asking_the_owner(
    fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_part_picture", PART_WRONG, PART_WRONG, PART_WRONG)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PART_PLAN)
    say("Plan it")
    assert len(handoffs("make_part_picture")) == 3
    assert part_items() == []
    warnings = Job.objects.get().warnings
    failures = [row for row in warnings if row["kind"] == "part_tried_again"]
    assert len(failures) == 1
    assert "all 3 tries failed" in failures[0]["text"]
    assert not Message.objects.filter(role="notice", text__contains="the lid").exists()
    assert "send a photo" not in str(warnings)
    assert "could not be drawn" in results_of("plan_ad")[0]

    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    run(fake_model, steps)
    assert len(handoffs("make_part_picture")) == 3
    assert clips_of(2) == 0
    assert not SceneStep.objects.filter(status="running").exists()


@pytest.mark.without_end_card_rules
def test_a_picture_service_down_keeps_the_plan_and_draws_the_part_when_the_clip_is_asked_for(
    fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Callable[..., None]
) -> None:
    drawing = fake_model.edit

    def down(**asked: Any) -> Any:
        raise OutsideServiceDown("the picture service answered 503")

    fake_model.edit = down  # type: ignore[method-assign]
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PART_PLAN)
    say("Plan it")

    (planned,) = results_of("plan_ad")
    assert not planned.startswith("Failed")
    assert "could not be drawn" in planned
    assert "Start its scene agent" in planned
    assert Job.objects.get().scenes.count() == 4
    # Tried again on its own, so only recorded for the developer, in red (spec #272).
    (red,) = [
        warning["text"]
        for warning in Job.objects.get().warnings
        if warning["level"] == "problem" and "the lid" in warning["text"]
    ]
    assert "normal recovery" in red
    assert not Message.objects.filter(role="notice", text__contains="the lid").exists()
    assert part_items() == []

    fake_model.edit = drawing  # type: ignore[method-assign]
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    made_ready(fake_model, steps, say, (2,))
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )

    assert not results_of("start_scene_agent")[-1].startswith("Refused")
    run(fake_model, steps)
    (item,) = part_items()
    assert owner_parts.part_picture(scene(2)) == item
    assert clips_of(2) == 1


# Scene 3 given the same part in other words, as the planner writes each scene's part.
TWO_SENTENCES: dict[str, Any] = {
    **PART_PLAN,
    "plan": {
        **PART_PLAN["plan"],
        "scenes": [
            *PART_PLAN["plan"]["scenes"][:2],
            {
                **PART_PLAN["plan"]["scenes"][2],
                "owner_part": {**PART, "looks": "A square black lid with no knob."},
            },
            *PART_PLAN["plan"]["scenes"][3:],
        ],
    },
}


@pytest.mark.without_end_card_rules
def test_one_part_in_two_sentences_is_drawn_once_for_both_scenes(
    fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("plan_ad", TWO_SENTENCES)
    fake_model.respond("fact_check", FACTS_OK)
    say("Plan it, make the person and check the script")

    # Drawn once, from scene 2's sentence, which scene 3 takes.
    (drawing,) = handoffs("make_part_picture")
    assert PART["looks"] in drawing["prompt"]
    assert len(part_notices()) == 1
    assert owner_parts.owner_part(scene(3)).looks == PART["looks"]  # type: ignore[union-attr]
    (item,) = part_items()
    assert owner_parts.part_picture(scene(3)) == item

    for number in (2, 3):
        assert owner_parts.picture_is_current(scene(number))
        assert owner_parts.owner_part(scene(number)).looks == PART["looks"]  # type: ignore[union-attr]
