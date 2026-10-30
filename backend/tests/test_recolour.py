"""Painting a library picture to a colour reference's colour (spec #210 point 8, #273 point
4): the picture model is shown the picture and the reference, and the paint it makes is kept
unchecked; a paint the picture model refuses is asked for again once, and refused twice the
original goes back with a red notice. The paint enters the library beside the original, made
once per colour. The models are faked at the gateway."""

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

from .conftest import paid_for, photo

pytestmark = pytest.mark.django_db(transaction=True)

# What a check of a paint would answer, passing it: a test that lets one be asked, to show
# none is, answers with this.
RECOLOUR_OK: dict[str, Any] = {
    "changes": "Only the product's colour changed.",
    "colour_match": "The product is the reference's colour.",
    "verdict": "pass",
    "problem": None,
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


def test_a_paint_enters_the_library_unchecked_once_and_is_reused(fake_model: FakeModel) -> None:
    job = a_library()

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
    assert made.picture.file == ModelCall.objects.get(purpose="recolour_picture").output["file"]  # type: ignore[index]
    assert paid_for() == ["recolour_picture"]
    assert warnings(job) == []

    again = recolour(job, 3, "red", reference=12, scene=2)

    assert again == made
    assert paid_for() == ["recolour_picture"]
    assert len(library_of(job)) == 13


def test_the_picture_model_is_shown_the_picture_and_the_reference_and_named_no_colour(
    fake_model: FakeModel,
) -> None:
    job = a_library()

    recolour(job, 3, "red", reference=12, scene=2)

    library = library_of(job)
    edit = ModelCall.objects.get(purpose="recolour_picture")
    assert edit.handoff["pictures"] == [library[2].file, library[11].file]
    prompt = edit.handoff["prompt"].lower()
    assert "picture 1" in prompt and "picture 2" in prompt
    assert not {"red", "black"} & set(prompt.replace(".", " ").split())


def test_a_picture_model_that_refuses_twice_sends_the_original_with_a_red_notice(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no"), BlockedBySafetyFilter("no"))

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record == Recolour(of=3, colour="red", outcome="fell_back", made=None)
    assert made.picture == library_of(job)[2]
    assert len(library_of(job)) == 12
    assert fake_model.edits == 0
    [(level, text)] = warnings(job)
    assert level == "problem"
    assert "Picture 3" in text and "made red, for scene 2" in text and "colour, black" in text
    assert "safety filter" in text


def test_a_picture_model_that_refuses_once_is_asked_again(fake_model: FakeModel) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no"))

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record == Recolour(of=3, colour="red", outcome="done", made=13)
    assert fake_model.edits == 1
    assert warnings(job) == []


def test_another_colour_is_its_own_recolour(fake_model: FakeModel) -> None:
    job = a_library()
    blue_frame = file_store.save(f"jobs/{job.pk}/blue-frame.png", photo(98))
    add_to_library(
        job,
        blue_frame,
        RED_FRAME.model_copy(update={"colour": "blue"}),
        LookFrame(video=2, second=1),
    )

    red = recolour(job, 3, "red", reference=12, scene=2)
    blue = recolour(job, 3, "blue", reference=13, scene=2)

    assert (red.picture.number, blue.picture.number) == (14, 15)
    assert blue.picture.source == Recoloured(of=3, colour="blue")
    assert paid("recolour_picture") == 2


def test_a_recolour_that_fell_back_is_said_once_naming_the_first_scene_that_asked(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", *[BlockedBySafetyFilter("no")] * 2)
    recolour(job, 3, "red", reference=12, scene=2)
    fake_model.respond("recolour_picture", *[BlockedBySafetyFilter("no")] * 2)

    later = recolour(job, 3, "red", reference=12, scene=4)

    assert later.record.outcome == "fell_back"
    [(_, text)] = warnings(job)
    assert "for scene 2" in text and "scene 4" not in text


def test_run_again_after_a_refusal_and_a_stop_it_asks_once_more_only(
    fake_model: FakeModel,
) -> None:
    job = a_library()
    fake_model.respond(
        "recolour_picture", BlockedBySafetyFilter("no"), RuntimeError("the worker stopped")
    )
    with pytest.raises(RuntimeError):
        recolour(job, 3, "red", reference=12, scene=2)
    fake_model.respond("recolour_picture", BlockedBySafetyFilter("no again"))

    made = recolour(job, 3, "red", reference=12, scene=2)

    assert made.record.outcome == "fell_back"
    assert fake_model.left("recolour_picture") == 0
    [(_, text)] = warnings(job)
    assert "Try 1: the picture model's safety filter refused it (no)" in text
    assert "Try 2: the picture model's safety filter refused it (no again)" in text


def test_an_outage_of_the_picture_model_is_raised(fake_model: FakeModel) -> None:
    job = a_library()
    fake_model.respond("recolour_picture", *[OutsideServiceDown("down")] * 3)

    with pytest.raises(OutsideServiceDown):
        recolour(job, 3, "red", reference=12, scene=2)

    assert len(library_of(job)) == 12
    assert warnings(job) == []
    assert fake_model.edits == 0


def test_another_picture_is_recoloured_while_one_is_being_made(fake_model: FakeModel) -> None:
    job = a_library()
    painting, release = threading.Event(), threading.Event()

    def a_slow_refusal() -> dict[str, Any]:
        painting.set()
        release.wait(60)
        raise BlockedBySafetyFilter("no")

    fake_model.respond("recolour_picture", a_slow_refusal)
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
    painting.wait(60)
    recolouring(4).join(30)
    made_while_waiting = dict(made)
    release.set()
    slow.join(60)

    assert made_while_waiting == {4: 13}
    assert made == {4: 13, 3: 14}
