"""Recolouring a library picture to a colour reference's colour (spec #210 point 8): the
picture model is shown the picture and the reference, the result is checked once, a failed
check is drawn again up to twice, and when it still fails, or the picture model refuses
twice, the original goes back with a red notice. The recoloured picture enters the library
beside the original, made once per colour. The models are faked at the gateway."""

import contextvars
import threading
from typing import Any

import pytest

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.library import (
    LookFrame,
    PagePhoto,
    PictureLabel,
    Recolour,
    Recoloured,
    add_to_library,
    library_of,
)
from jobs.models import Job
from jobs.recolour import recolour
from jobs.threads import closing_its_connection

from .conftest import photo

pytestmark = pytest.mark.django_db(transaction=True)

# What the check answers for a recoloured picture that passes, and for one that fails.
RECOLOUR_OK: dict[str, Any] = {
    "changes": "Only the product's colour changed.",
    "colour_match": "The product is the reference's colour.",
    "verdict": "pass",
    "problem": None,
}
RECOLOUR_BAD: dict[str, Any] = {
    "changes": "The background turned grey too.",
    "colour_match": "The product is the reference's colour.",
    "verdict": "fail",
    "problem": "The background changed as well as the product.",
}

BLACK_SIDE = PictureLabel(
    kind="side", side="back", alone="yes", colour="black", label="The press from the back."
)
RED_FRAME = PictureLabel(
    kind="state", side="front", alone="no", colour="red", label="The red press, open."
)


def a_library() -> Job:
    """A job whose library holds 11 black page photos and, as item 12, a red look frame."""
    job = Job.objects.create(product_url="https://shop.example/press")
    for position in range(1, 12):
        file = file_store.save(f"jobs/{job.pk}/photo-{position}.png", photo(position))
        add_to_library(job, file, BLACK_SIDE, PagePhoto(photo=position))
    frame = file_store.save(f"jobs/{job.pk}/frame.png", photo(99))
    add_to_library(job, frame, RED_FRAME, LookFrame(video=1, second=2))
    return job


def paid(purpose: str) -> int:
    return ModelCall.objects.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED).count()


def warnings(job: Job) -> list[tuple[str, str]]:
    job.refresh_from_db()
    return [(warning["level"], warning["text"]) for warning in job.warnings]


def test_a_recolour_that_passes_its_check_enters_the_library_once_and_is_reused(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_OK)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record == Recolour(of=3, colour="red", outcome="done", made=13)
    assert made.picture == library_of(job)[12]
    assert made.picture.source == Recoloured(of=3, colour="red")
    assert made.picture.colour == "red"
    assert (made.picture.kind, made.picture.side, made.picture.label) == (
        "side",
        "back",
        BLACK_SIDE.label,
    )
    assert (paid("recolour_picture"), paid("check_recolour")) == (1, 1)
    assert warnings(job) == []

    again = recolour(job, 3, "red", reference=12, scene=2)

    assert again == made
    assert (paid("recolour_picture"), paid("check_recolour")) == (1, 1)
    assert len(library_of(job)) == 13


def test_the_picture_model_is_shown_the_picture_and_the_reference_and_named_no_colour(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_OK)

    made = recolour(job, 3, "red", reference=12, scene=2)

    library = library_of(job)
    edit = ModelCall.objects.get(purpose="recolour_picture")
    assert edit.handoff["pictures"] == [library[2].file, library[11].file]
    prompt = edit.handoff["prompt"].lower()
    assert "picture 1" in prompt and "picture 2" in prompt
    assert not {"red", "black"} & set(prompt.replace(".", " ").split())
    check = ModelCall.objects.get(purpose="check_recolour")
    assert [image["key"] for image in check.images] == [
        library[2].file,
        library[11].file,
        made.picture.file,
    ]


def test_a_check_that_fails_twice_then_passes_makes_three_pictures_and_one_item(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_BAD, RECOLOUR_BAD, RECOLOUR_OK)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "done"
    assert (paid("recolour_picture"), paid("check_recolour")) == (3, 3)
    assert len(library_of(job)) == 13
    checked = list(ModelCall.objects.filter(purpose="check_recolour").order_by("created_at", "id"))
    assert made.picture.file == checked[-1].images[-1]["key"]
    assert warnings(job) == []


def test_a_check_that_fails_three_times_sends_the_original_with_a_red_notice(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_BAD, RECOLOUR_BAD, RECOLOUR_BAD)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record == Recolour(of=3, colour="red", outcome="fell_back", made=None)
    assert made.picture == library_of(job)[2]
    assert len(library_of(job)) == 12
    assert (paid("recolour_picture"), paid("check_recolour")) == (3, 3)
    [(level, text)] = warnings(job)
    assert level == "problem"
    assert "Picture 3" in text and "made red, for scene 2" in text and "colour, black" in text
    assert RECOLOUR_BAD["problem"] in text


def test_a_picture_model_that_refuses_twice_sends_the_original_with_a_red_notice(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no"), BlockedBySafetyFilter("no"))

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "fell_back"
    assert made.picture.number == 3
    assert paid("check_recolour") == 0
    [(level, text)] = warnings(job)
    assert level == "problem" and "safety filter" in text


def test_a_picture_model_that_refuses_once_is_asked_again(fake_model: FakeModel) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no"))
    fake_model.respond("check_recolour", RECOLOUR_OK)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record == Recolour(of=3, colour="red", outcome="done", made=13)
    assert warnings(job) == []


def test_a_check_that_cant_be_made_fails_the_picture(fake_model: FakeModel) -> None:
    job = a_library()
    fake_model.respond("check_recolour", *[BlockedBySafetyFilter("no")] * 3)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "fell_back"
    [(level, text)] = warnings(job)
    assert level == "problem" and "the check's safety filter refused" in text


def test_another_colour_is_its_own_recolour(fake_model: FakeModel) -> None:
    job = a_library()
    blue_frame = file_store.save(f"jobs/{job.pk}/blue-frame.png", photo(98))
    add_to_library(
        job,
        blue_frame,
        RED_FRAME.model_copy(update={"colour": "blue"}),
        LookFrame(video=2, second=1),
    )
    fake_model.respond("check_recolour", RECOLOUR_OK, RECOLOUR_OK)

    red = recolour(job, 3, "red", reference=12, scene=2)
    blue = recolour(job, 3, "blue", reference=13, scene=2)

    assert (red.picture.number, blue.picture.number) == (14, 15)
    assert blue.picture.source == Recoloured(of=3, colour="blue")
    assert paid("recolour_picture") == 2


def test_run_again_after_a_stop_it_pays_for_no_picture_or_check_twice(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_BAD, RuntimeError("the worker stopped"))
    with pytest.raises(RuntimeError):
        recolour(job, 3, "red", reference=12, scene=2)
    fake_model.respond("check_recolour", RECOLOUR_OK)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "done"
    assert fake_model.edits == 2
    assert paid("check_recolour") == 2


def test_a_recolour_that_fell_back_is_said_once_naming_the_first_scene_that_asked(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("check_recolour", RECOLOUR_BAD, RECOLOUR_BAD, RECOLOUR_BAD)
    recolour(job, 3, "red", reference=12, scene=2)

    later = recolour(job, 3, "red", reference=12, scene=4)

    assert later.record.outcome == "fell_back"
    [(_, text)] = warnings(job)
    assert "for scene 2" in text and "scene 4" not in text
    assert fake_model.edits == 3


def test_run_again_after_a_refusal_and_a_stop_it_makes_no_extra_picture(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no"))
    fake_model.respond("check_recolour", RECOLOUR_BAD, RuntimeError("the worker stopped"))
    with pytest.raises(RuntimeError):
        recolour(job, 3, "red", reference=12, scene=2)
    fake_model.respond("check_recolour", RECOLOUR_BAD)

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "fell_back"
    assert fake_model.edits == 2
    [(_, text)] = warnings(job)
    assert "Try 1: the picture model's safety filter refused it" in text


@pytest.mark.parametrize("purpose", ["recolour_picture", "check_recolour"])
def test_an_outage_of_the_picture_model_or_the_check_is_raised(
    fake_model: FakeModel, purpose: str
) -> None:
    job = a_library()
    fake_model.respond(purpose, *[OutsideServiceDown("down")] * 3)
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)

    with pytest.raises(OutsideServiceDown):
        recolour(job, 3, "red", reference=12, scene=2)

    assert len(library_of(job)) == 12
    assert warnings(job) == []
    assert fake_model.edits <= 1


def test_another_picture_is_recoloured_while_one_is_being_made(fake_model: FakeModel) -> None:
    job = a_library()
    checking, release = threading.Event(), threading.Event()

    def a_slow_check() -> dict[str, Any]:
        checking.set()
        release.wait(60)
        return RECOLOUR_OK

    fake_model.respond("check_recolour", a_slow_check, RECOLOUR_OK)
    made: dict[int, int] = {}

    def recolouring(number: int) -> threading.Thread:
        def run() -> None:
            made[number] = recolour(job, number, "red", reference=12, scene=2).picture.number

        thread = threading.Thread(
            target=contextvars.copy_context().run, args=(closing_its_connection, run)
        )
        thread.start()
        return thread

    slow = recolouring(3)
    checking.wait(60)
    recolouring(4).join(30)
    made_while_waiting = dict(made)
    release.set()
    slow.join(60)

    assert made_while_waiting == {4: 13}
    assert made == {4: 13, 3: 14}
