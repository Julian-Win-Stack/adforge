"""The owner part's own picture in the library (spec #210 point 20), driven through the chat
from the first message: a plan with a part of the product the page doesn't show has the
part's picture drawn into the library, labelled, and shown to the shop owner; the clip of a
scene holding the part waits for their yes on that picture; a correction draws the one
library item again for every scene holding the part. The planner, the picture model and the
checks are faked at the gateway."""

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
    FACTS_OK,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    broll,
    handoffs,
    photo,
    results_of,
)
from .test_producer_broll import BROLL_CHOICE, DOES_A_JOB, calling, made_ready, run

pytestmark = pytest.mark.django_db(transaction=True)

PART = {
    "what": "the lid",
    "source": "owner's words",
    "looks": "A round wooden lid with a small knob on top.",
}

# The mug plan with scenes 2 and 3 showing the mug with its lid, which no photo shows.
PART_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            PLAN["plan"]["scenes"][0],
            broll(
                {
                    "line": "Hand-thrown, holds 350 ml, and dishwasher safe too.",
                    "shows": "Hot tea poured into the mug, its lid beside it.",
                    **DOES_A_JOB,
                    "usage": "Hot tea is poured into the mug from a teapot.",
                    "result": "The mug is full of steaming tea.",
                    "owner_part": PART,
                }
            ),
            broll(
                {
                    "line": "Yours today for twenty four dollars, lid and all.",
                    "shows": "The mug with its lid on, on a kitchen shelf.",
                    "overlay": "$24.00",
                    "owner_part": PART,
                }
            ),
        ],
    },
}


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
    return list(Message.objects.filter(role="notice", text__contains="which none of the product's"))


def owner_says(
    fake_model: FakeModel, say: Callable[..., None], text: str, *calls: tuple[str, dict[str, Any]]
) -> None:
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say(text)


def yes(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    owner_says(
        fake_model,
        say,
        "Yes, that lid looks right.",
        ("approve_owner_part", {"scene": 2}),
        ("make_clip", {"scene": 2}),
    )
    run(fake_model, steps)


# --- Drawn from the plan -----------------------------------------------------------------------


@pytest.mark.usefixtures("planned")
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
    # Both scenes holding the part take the one picture, unapproved.
    for number in (2, 3):
        assert owner_parts.part_picture(scene(number)) == item
        assert not owner_parts.is_approved(scene(number))
    (planned,) = results_of("plan_ad")
    assert f"library picture {item.number}" in planned
    assert "until they say yes to it" in planned


@pytest.mark.usefixtures("ready")
def test_the_clip_waits_for_the_shop_owners_yes_on_the_parts_picture(
    fake_model: FakeModel, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    (result,) = results_of("make_clip")
    assert result.startswith("Refused: scene 2 shows the lid")
    assert "library picture" in result
    assert ToolCall.objects.get(tool="make_clip").asked_about == "the owner part"
    assert not SceneStep.objects.filter(kind="clip").exists()
    # The starting picture's step result no longer asks for a yes on it.
    picture_ready = Job.objects.get().session.messages.filter(  # type: ignore[union-attr]
        role="notice", text__contains="starting picture"
    )
    assert not picture_ready.filter(text__contains="says yes").exists()


@pytest.mark.usefixtures("ready")
def test_the_shop_owners_yes_on_the_picture_releases_the_clip(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    yes(fake_model, steps, say)

    (approved,) = results_of("approve_owner_part")
    assert not approved.startswith("Refused")
    assert clips_of(2) == 1
    # The part is one thing for the ad: the other scene holding it needs no new yes.
    assert owner_parts.is_approved(scene(3))


def test_a_yes_taken_before_the_shop_owner_answered_the_picture_is_refused(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # The producer, in the turn the picture is drawn and shown, takes it as approved.
    fake_model.respond(
        "produce",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(calls=[("approve_owner_part", {"scene": 2})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("plan_ad", PART_PLAN)
    fake_model.respond("fact_check", FACTS_OK)
    say("Plan it, make the person and check the script")

    (approved,) = results_of("approve_owner_part")
    assert approved.startswith("Refused: the shop owner hasn't answered since the picture of")
    assert not owner_parts.is_approved(scene(2))


# --- Corrections -------------------------------------------------------------------------------


@pytest.mark.usefixtures("ready")
def test_a_correction_in_words_draws_the_one_item_again_and_every_scene_still_waits(
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
        assert not owner_parts.is_approved(scene(number))
    # Its starting picture drawn again with the new part, the clip still waits for the yes.
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert results_of("make_clip")[-1].startswith("Refused: scene 2 shows the lid")
    assert clips_of(2) == 0

    yes(fake_model, steps, say)

    assert clips_of(2) == 1


@pytest.mark.usefixtures("ready")
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


def test_a_picture_that_keeps_failing_its_check_is_said_in_red_until_the_part_is_corrected(
    fake_model: FakeModel, page_read: str, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("check_part_picture", PART_WRONG, PART_WRONG, PART_WRONG)
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PART_PLAN)
    say("Plan it")

    assert len(handoffs("make_part_picture")) == 3
    assert part_items() == []
    (red,) = Message.objects.filter(role="notice", level="problem", text__contains="the lid")
    assert "failed" in red.text
    assert red.text in [warning["text"] for warning in Job.objects.get().warnings]
    assert "making its picture failed" in results_of("plan_ad")[0]

    # Its clip asked for, it isn't drawn or said again, and the clip waits.
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    assert "making its picture failed" in results_of("make_clip")[-1]
    assert len(handoffs("make_part_picture")) == 3
    assert Message.objects.filter(role="notice", level="problem", text=red.text).count() == 1
    assert not SceneStep.objects.filter(kind="clip").exists()

    # The shop owner's words draw it afresh.
    owner_says(
        fake_model,
        say,
        "Just the lid, please.",
        ("correct_owner_part", {"scene": 2, "words": "A round wooden lid.", "photo": None}),
    )
    (item,) = part_items()
    assert f"library picture {item.number}" in results_of("correct_owner_part")[-1]


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
    assert "making its picture failed" in planned
    assert "drawn again when its clip is next asked for" in planned
    assert Job.objects.get().scenes.count() == 3
    (red,) = Message.objects.filter(role="notice", level="problem", text__contains="the lid")
    assert "tried again" in red.text
    assert red.text in [warning["text"] for warning in Job.objects.get().warnings]
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
    calling(fake_model, say, ("make_clip", {"scene": 2}))

    (item,) = part_items()
    assert f"library picture {item.number}" in results_of("make_clip")[-1]
    assert not owner_parts.is_approved(scene(2))


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
        ],
    },
}


def test_one_part_in_two_sentences_is_drawn_once_and_a_yes_holds_for_the_sentence_drawn(
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

    owner_says(fake_model, say, "Yes, that lid is right.", ("approve_owner_part", {"scene": 3}))

    for number in (2, 3):
        assert owner_parts.is_approved(scene(number))
        assert owner_parts.owner_part(scene(number)).looks == PART["looks"]  # type: ignore[union-attr]
