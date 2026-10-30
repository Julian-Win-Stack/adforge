"""The job's library is built after the page read (spec #210 point 1): one item per kept page
photo with the picker's fields, and one per photo the shop owner sent, given the same fields
by the labelling call. Driven through the chat: the owner's first message, the picker and the
labelling call faked at the gateway, the shop's page on the local web server through the
stand-in Firecrawl."""

import contextvars
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs import work
from jobs.library import LibraryEntry, OwnerPhoto, PagePhoto, library_of
from jobs.library_items import MOST_PRODUCT_TEXT, add_kept_photos, add_owner_photo
from jobs.models import Job, ProductPhoto, ReferencePhoto, Scene
from jobs.photos import PICK_INSTRUCTIONS
from jobs.product_only import to_send
from jobs.threads import closing_its_connection

from .conftest import CUT_OUT_OK, LABELLED, READABLE, SHAMPOO_PICKED, paid_for, photo
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
# The same, alone on a plain background: it needs no product-only version (spec #210 point 6),
# so it adds one item.
OWNER_ALONE: dict[str, Any] = {
    **OWNER_LABEL,
    "alone": "yes",
    "label": "The amber bottle with its cap off, alone on a plain background.",
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
        # The owner's photo shows other things: its product-only version is made only when
        # it is first sent (#273 point 5).
        (6, "owner_photo", "state", "The amber bottle on a bathroom shelf, cap off."),
    ]
    photos = list(job.photos.order_by("position"))
    items = library_of(job)
    assert [item.source for item in items[:5]] == [
        PagePhoto(photo=photo.position) for photo in photos[:5]
    ]
    assert [item.file for item in items[:6]] == [photo.file for photo in photos]
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


def test_a_thing_from_the_owners_photo_cut_out_already_is_cut_out_on_its_own_for_a_note(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    # The photo's product-only version keeps only the product: a thing the product's name
    # holds, as a rack's towel, is another thing, cut out of the photo on its own.
    first_message(fake_model, say, labelled_page)
    Job.objects.update(product_name="Bamboo Towel Rack")
    job = Job.objects.get()
    product_only = to_send(job, library_of(job)[5])
    assert product_only.source.came_from == "product_only"
    scene = Scene.objects.create(job=job, number=1, line="It dries fast.", shows="It dries.")
    row = ReferencePhoto.objects.create(
        scene=scene,
        file=job.photos.get(position=6).file,
        thing="the towel",
        use=ReferencePhoto.Use.PICTURE,
    )

    drawn = work.clean_picture(scene, row)

    assert ModelCall.objects.filter(purpose="make_reference_photo_picture").count() == 1
    assert drawn != product_only.file


def test_the_clean_picture_of_an_owners_thing_is_checked_as_that_thing_beside_their_photo(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page)
    job = Job.objects.get()
    scene = Scene.objects.create(job=job, number=1, line="It dries fast.", shows="It dries.")
    row = ReferencePhoto.objects.create(
        scene=scene,
        file=job.photos.get(position=6).file,
        thing="towel",
        use=ReferencePhoto.Use.PICTURE,
    )
    checks = len(ModelCall.objects.filter(purpose="check_cut_out"))
    # The check's filter refuses the photo beside it once: it is checked without it.
    fake_model.respond(
        "check_cut_out", BlockedBySafetyFilter("content_policy_violation"), CUT_OUT_OK
    )

    work.clean_picture(scene, row)

    asked = ModelCall.objects.filter(purpose="check_cut_out").order_by("id")[checks:]
    assert [call.handoff["keep"] for call in asked] == ["the shop owner's towel"] * 2
    assert {
        "level": "problem",
        "text": "The AI model's safety filter refused to look at the shop owner's photo of "
        "their towel, so the clean picture of the shop owner's towel was checked without it.",
    }.items() <= {**job.warnings[-1]}.items()


def test_the_labelling_call_is_given_the_pickers_label_rules_and_the_photo(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    asked: list[ModelRequest[Any]] = []

    def labelling(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request)
        return OWNER_ALONE

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
    broken = {**OWNER_LABEL, **changed}
    # Told why and asked again, the labelling breaks the same rule again.
    fake_model.respond("label_photo", broken)
    first_message(fake_model, say, labelled_page, broken)

    labelling = ModelCall.objects.filter(purpose="label_photo")
    assert [call.outcome for call in labelling] == [ModelCall.Outcome.FAILED] * 2
    assert [item.source.came_from for item in library_of(Job.objects.get())] == ["page_photo"] * 5
    (problem,) = [text for level, text in notices(api, session_id) if level == "problem"]
    # Said in plain words, never as the answer's validation error.
    assert problem == (
        "Saying what a photo the shop owner sent shows failed (the AI model's answer couldn't "
        "be used), so it isn't in the ad's picture library and can't be sent to the video "
        "model. It is tried again when photos are next added."
    )
    assert [warning["text"] for warning in Job.objects.get().warnings] == [problem]


def test_a_photo_whose_labelling_answer_broke_the_rules_is_asked_again_when_photos_are_added(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    no_label = {**OWNER_LABEL, "label": " "}
    # Told why and asked again, the labelling breaks the same rule again.
    fake_model.respond("label_photo", no_label)
    first_message(fake_model, say, labelled_page, no_label)
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("use_photos", {"photos": [{"photo": n, "shows": "the product"} for n in (1, 2)]})
            ]
        ),
        turn(says="Added."),
    )
    fake_model.respond("label_photo", OWNER_ALONE)

    say("Here's one more of mine.", ("my-other-bottle.png", photo(202)))

    owners = [item for item in library_of(Job.objects.get()) if item.number > 5]
    assert [(item.number, item.label) for item in owners] == [
        (6, OWNER_ALONE["label"]),
        (7, LABELLED["label"]),
    ]
    assert ModelCall.objects.filter(purpose="label_photo").count() == 4


def test_a_colour_of_two_words_from_the_labelling_call_is_kept_joined_by_a_hyphen(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, {**OWNER_LABEL, "colour": "Light Amber"})

    assert library_of(Job.objects.get())[5].colour == "light-amber"


def test_an_owner_photo_added_later_joins_at_the_next_number_and_the_others_keep_theirs(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    first_message(fake_model, say, labelled_page, OWNER_ALONE)
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


def test_a_page_photo_the_picker_gave_no_fields_is_labelled_by_the_call_and_said_so(
    api: APIClient,
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    session_id: str,
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
    assert listed(job)[2] == (3, "page_photo", "side", LABELLED["label"])
    assert library_of(job)[2].source == PagePhoto(photo=side.position)
    (labelling,) = ModelCall.objects.filter(purpose="label_photo")
    assert labelling.handoff["photo"] == side.source_url
    # Labelled while the page is read, before its text is kept, it is still given that text.
    assert job.page_text
    assert labelling.handoff["product_text"] == job.page_text[:MOST_PRODUCT_TEXT]
    told = (
        "The photo picker didn't say what 1 of the page's photo shows, so a call of its own "
        "looked at it and labelled it for the ad's picture library, as the picker labels the "
        "photos it picks."
    )
    assert ("info", told) in notices(api, session_id)
    assert told in [warning["text"] for warning in job.warnings]


def test_a_page_read_again_puts_a_photo_its_labelling_left_out_into_the_library(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    # The picker gives the side photo no fields, and its labelling call fails the first time.
    without_the_side = [label for label in FIVE_PICKED["pictures"] if label["picture"] != "I102"]
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": labelled_page, "target_seconds": None})]),
        turn(says="Done."),
        turn(calls=[("read_page", {"link": labelled_page, "target_seconds": None})]),
        turn(says="It was already read."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**FIVE_PICKED, "pictures": without_the_side})
    fake_model.respond("label_photo", *[OutsideServiceDown("503 from the provider")] * 3)
    say(f"Make an ad for {labelled_page}")
    assert [item.number for item in library_of(Job.objects.get())] == [1, 2, 3, 4]

    say("Read my page again")

    job = Job.objects.get()
    side = job.photos.get(source_url__endswith="102.png")
    assert [item.number for item in library_of(job)] == [1, 2, 3, 4, 5]
    assert library_of(job)[4].source == PagePhoto(photo=side.position)
    assert paid_for().count("pick_photos") == 1


def test_two_steps_adding_the_same_owner_photo_at_once_add_it_once(
    fake_model: FakeModel,
) -> None:
    job = Job.objects.create(product_url="https://shop.example/p")
    key = file_store.save(f"jobs/{job.pk}/owner.png", photo(203))
    both_asking = threading.Barrier(2)

    def slowly(_: Any) -> dict[str, Any]:
        time.sleep(0.3)
        return LABELLED

    fake_model.answer_unscripted("label_photo", slowly)

    def adding() -> None:
        both_asking.wait()
        closing_its_connection(add_owner_photo, job, key, key, "the product")

    with ThreadPoolExecutor(max_workers=2) as pool:
        for done in [pool.submit(contextvars.copy_context().run, adding) for _ in range(2)]:
            done.result()

    assert [(item.number, item.file) for item in library_of(job)] == [(1, key)]


def test_a_page_photo_whose_stored_fields_no_longer_fit_is_labelled_again(
    fake_model: FakeModel,
) -> None:
    job = Job.objects.create(product_url="https://shop.example/p")
    key = file_store.save(f"jobs/{job.pk}/photos/1.png", photo(204))
    ProductPhoto.objects.create(
        job=job,
        position=1,
        source_url="https://shop.example/1.png",
        file=key,
        kind="packshot",  # A kind the set no longer holds.
        side="front",
        alone="yes",
        colour="amber",
        label="The bottle.",
    )

    (item,) = add_kept_photos(job)

    assert (item.kind, item.label) == ("side", LABELLED["label"])


def test_a_photo_sent_as_is_enters_beside_a_clean_picture_drawn_from_it(
    fake_model: FakeModel,
) -> None:
    job = Job.objects.create(product_url="https://shop.example/p")
    sent = file_store.save(f"jobs/{job.pk}/sent.png", photo(205))
    clean = file_store.save(f"jobs/{job.pk}/clean.png", photo(206))
    add_owner_photo(job, sent, clean, "the shop owner's black mug")
    ProductPhoto.objects.create(job=job, position=1, file=sent)

    add_kept_photos(job)

    assert [(item.number, item.file) for item in library_of(job)] == [(1, clean), (2, sent)]


def test_a_colour_the_labelling_call_names_in_words_is_kept_as_one_entry(
    fake_model: FakeModel,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
) -> None:
    # The picker wrote no colour list, so the call names the colour in its own words.
    first_message(fake_model, say, labelled_page, {**OWNER_LABEL, "colour": "Rose gold"})

    owners = [item for item in library_of(Job.objects.get()) if isinstance(item.source, OwnerPhoto)]
    assert [item.colour for item in owners] == ["rose-gold"]
