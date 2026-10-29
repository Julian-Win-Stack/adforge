"""The job's library is built after the page read (spec #210 point 1): one item per kept page
photo with the picker's fields, and one per photo the shop owner sent, given the same fields
by the labelling call. Driven through the chat: the owner's first message, the picker and the
labelling call faked at the gateway, the shop's page on the local web server through the
stand-in Firecrawl."""

import re
from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelRequest
from jobs.library import LibraryEntry, OwnerPhoto, PagePhoto, library_of
from jobs.models import Job
from jobs.photos import PICK_INSTRUCTIONS

from .conftest import LABELLED, READABLE, SHAMPOO_PICKED, photo
from .test_picker_labels import LABELS, labelled_page, notices  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]

# Five photos off the page: the front, the back, the left side, the cap off and in use.
FIVE = [8, 101, 102, 103, 104]
FIVE_PICKED = {
    **SHAMPOO_PICKED,
    "gallery_images": FIVE,
    "pictures": [label for label in LABELS if label["picture"] in {f"I{n}" for n in FIVE}],
}
OWNER_PHOTO = ("my-bottle.png", photo(201))

# What the labelling call answers for the shop owner's photo.
OWNER_LABEL: dict[str, Any] = {
    "kind": "state",
    "side": "front",
    "alone": "no",
    "colour": "Amber",
    "target": "",
    "label": "The amber bottle on a bathroom shelf, cap off.",
}


def first_message(
    fake_model: FakeModel, say: Say, link: str, labelled: dict[str, Any] | None = OWNER_LABEL
) -> None:
    """The owner's first message: the page's link and one photo of theirs, read and added,
    the photo `labelled` by the labelling call (or as the test answers it, for None)."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("use_photos", {"photos": [{"photo": 1, "shows": "the product"}]})]),
        turn(says="Done."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", FIVE_PICKED)
    if labelled is not None:
        fake_model.respond("label_photo", labelled)
    say(f"Make an ad for {link}, and use my photo too.", OWNER_PHOTO)


def listed(job: Job) -> list[tuple[int, str, str, str]]:
    """Each library item: its number, where it came from, its kind and its label."""
    return [(item.number, item.source.came_from, item.kind, item.label) for item in library_of(job)]


def test_the_first_message_builds_a_library_of_every_page_photo_and_the_owners_photo(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page)

    job = Job.objects.get()
    assert listed(job) == [
        (1, "page_photo", "side", "The amber bottle from the front, cap on."),
        (2, "page_photo", "side", "The amber bottle from the back, its label showing."),
        (3, "page_photo", "side", "The amber bottle from its left side."),
        (4, "page_photo", "state", "The amber bottle with its cap off, the pump showing."),
        (5, "page_photo", "in-use", "A hand pressing the pump, the shampoo coming out."),
        (6, "owner_photo", "state", "The amber bottle on a bathroom shelf, cap off."),
    ]
    photos = list(job.photos.order_by("position"))
    items = library_of(job)
    assert [item.source for item in items[:5]] == [
        PagePhoto(photo=photo.position) for photo in photos[:5]
    ]
    assert [item.file for item in items] == [photo.file for photo in photos]
    owners = items[5]
    assert owners == LibraryEntry(
        number=6,
        file=photos[5].file,
        source=OwnerPhoto(original=photos[5].file),
        kind="state",
        side="front",
        alone="no",
        colour="amber",
        target="",
        label="The amber bottle on a bathroom shelf, cap off.",
    )


def test_the_labelling_call_is_given_the_pickers_label_rules_and_the_photo(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    asked: list[ModelRequest[Any]] = []

    def labelling(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request)
        return OWNER_LABEL

    fake_model.answer_unscripted("label_photo", labelling)

    first_message(fake_model, say, labelled_page, None)

    (request,) = asked
    # Every rule the picker labels a picture's fixed fields by, word for word.
    rules = re.findall(
        r"^- (?:kind|side|alone|colour|target|label): .*?(?=^- |^\n|\Z)",
        PICK_INSTRUCTIONS,
        re.M | re.S,
    )
    assert len(rules) == 6
    assert all(rule.strip() in request.instructions for rule in rules)
    owners = Job.objects.get().photos.get(source_url="")
    assert request.handoff.model_dump() == {
        "photo": owners.file,
        "shows": "the product the advert is for",
        "product_text": Job.objects.get().page_text,
    }
    assert [image.key for image in request.images] == [owners.file]


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"kind": "packshot"}, id="kind outside its set"),
        pytest.param({"colour": "light amber"}, id="colour of two words"),
        pytest.param({"kind": "side", "target": "the bottle on the left"}, id="target, no line-up"),
        pytest.param({"label": " "}, id="no label"),
    ],
)
def test_a_labelling_answer_that_breaks_the_fields_rules_leaves_the_photo_out_in_red(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
    say: Say,
    changed: dict[str, str],
) -> None:
    first_message(fake_model, say, labelled_page, {**OWNER_LABEL, **changed})

    (labelling,) = ModelCall.objects.filter(purpose="label_photo")
    assert labelling.outcome == ModelCall.Outcome.FAILED
    assert [item.source.came_from for item in library_of(Job.objects.get())] == ["page_photo"] * 5
    (problem,) = [text for level, text in notices(api, session_id) if level == "problem"]
    assert problem.startswith("Saying what a photo the shop owner sent shows failed")
    assert [warning["text"] for warning in Job.objects.get().warnings] == [problem]


def test_an_owner_photo_added_later_joins_at_the_next_number_and_the_others_keep_theirs(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page)
    before = library_of(Job.objects.get())
    fake_model.respond(
        "produce",
        turn(
            calls=[
                (
                    "use_photos",
                    {
                        "photos": [
                            {"photo": 1, "shows": "the product"},
                            {"photo": 2, "shows": "the product"},
                        ]
                    },
                )
            ]
        ),
        turn(says="Added."),
    )

    say("Here's one more of mine.", ("my-other-bottle.png", photo(202)))

    after = library_of(Job.objects.get())
    assert after[:6] == before
    (seventh,) = after[6:]
    newest = Job.objects.get().photos.order_by("position").last()
    assert newest is not None
    assert (seventh.number, seventh.source, seventh.file) == (
        7,
        OwnerPhoto(original=newest.file),
        newest.file,
    )
    # Labelled by the default answer: only the new photo was asked about.
    assert seventh.label == LABELLED["label"]
    assert ModelCall.objects.filter(purpose="label_photo").count() == 2


def test_a_page_photo_the_picker_gave_no_fields_stays_out_of_the_library(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    without_the_side = [label for label in FIVE_PICKED["pictures"] if label["picture"] != "I102"]
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": labelled_page, "target_seconds": None})]),
        turn(says="Done."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**FIVE_PICKED, "pictures": without_the_side})

    say(f"Make an ad for {labelled_page}")

    job = Job.objects.get()
    side = job.photos.get(source_url__endswith="102.png")
    assert [item.number for item in library_of(job)] == [1, 2, 3, 4]
    assert PagePhoto(photo=side.position) not in [item.source for item in library_of(job)]
    assert not ModelCall.objects.filter(purpose="label_photo").exists()
