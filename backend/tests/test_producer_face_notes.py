"""The Face note on every product photo: whether it shows a stranger's face you could
recognise, driven through the chat. The picker gives it for the photos it picks; a photo it
never saw (the page's declared photos, or one the shop owner attaches) gets its own call.
The models are faked at the gateway, and the shop is served from a real local web server."""

from collections.abc import Callable

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import post_save
from django.test import Client
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents.tasks import restart_dead_producers
from chat.models import Attachment
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs.models import Job, ProductPhoto

from .conftest import (
    MUG_FRONT,
    MUG_SIDE,
    READABLE,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    handoffs,
    paid_for,
    results_of,
)
from .test_producer_page import notices, reading, serve_the_other_photos
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

FACE = {"has_face": True}
NO_FACE = {"has_face": False}


def faces() -> list[bool]:
    """Each of the job's photos' Face note, in order."""
    return list(Job.objects.get().photos.values_list("has_face", flat=True))


# --- The picker's photos ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("face_images", "expected"),
    [
        # I8 is the shampoo, I10 the splash and I15 the lather.
        pytest.param([10], [False, True, False], id="a picked photo marked with a face"),
        # I16 is a thumbnail of I8's photo: the copy is merged into it, and marks it.
        pytest.param([16], [True, False, False], id="a merged copy marked with a face"),
        # I23 is the conditioner, which wasn't picked, and there is no I99.
        pytest.param([23, 99], [False, False, False], id="marks of photos not kept"),
    ],
)
def test_the_picker_marks_which_picked_photos_have_a_face(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
    face_images: list[int],
    expected: list[bool],
) -> None:
    serve_the_other_photos(httpserver)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "pick_photos",
        {
            **SHAMPOO_PICKED,
            "gallery_images": [8, 10, 16],
            "more_images": [15],
            "face_images": face_images,
        },
    )

    say(f"Make an ad for {shampoo_page_url}")

    assert results_of("read_page")[0].endswith("Kept 3 product photos.")
    assert faces() == expected
    # The picker gave every note in the call that picked the photos.
    assert "note_face" not in paid_for()
    assert notices(api, session_id) == []


# --- Photos the picker never saw ------------------------------------------------------------


def test_when_picking_fails_each_declared_photo_gets_its_own_face_note(
    fake_model: FakeModel,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", *[OutsideServiceDown("503 from the provider")] * 3)
    fake_model.respond("note_face", FACE, NO_FACE)

    say(f"Make an ad for {shampoo_page_url}")

    assert faces() == [True, False]
    # Each call was shown its own photo, as kept, and named it by its link on the page.
    photos = list(Job.objects.get().photos.all())
    calls = list(ModelCall.objects.filter(purpose="note_face").order_by("created_at"))
    assert [call.handoff["photo"] for call in calls] == [photo.source_url for photo in photos]
    assert [[image["key"] for image in call.images] for call in calls] == [
        [photo.file] for photo in photos
    ]
    assert paid_for().count("note_face") == 2


def test_a_photo_the_shop_owner_attaches_gets_its_own_face_note(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    link = a_page_with_no_photos(httpserver)
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {link}")
    fake_model.respond("produce", turn(calls=[("use_photos", {})]), turn(says="Got them."))
    fake_model.respond("note_face", NO_FACE, FACE)

    say("Here are two photos of it", ("front.png", MUG_FRONT), ("side.png", MUG_SIDE))

    assert results_of("use_photos") == [
        "Added 2 of the shop owner's photos. The job now has 2 product photos."
    ]
    assert faces() == [False, True]
    attached = list(Attachment.objects.order_by("position").values_list("file", flat=True))
    assert [given["photo"] for given in handoffs("note_face")] == attached
    assert [file_store.read(key) for key in attached] == [MUG_FRONT, MUG_SIDE]


@pytest.mark.parametrize(
    "fails",
    [
        pytest.param(
            UnusableReply("The model refused", input_tokens=1_000, output_tokens=10),
            id="an answer that can't be used",
        ),
        pytest.param(OutsideServiceDown("503 from the provider"), id="the provider stays down"),
    ],
)
def test_a_failed_face_note_is_the_safe_side_and_doesnt_stop_the_job(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
    fails: BaseException,
) -> None:
    # The mug's page is read plainly, so its declared photos are used.
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)
    tries = 3 if isinstance(fails, OutsideServiceDown) else 1
    fake_model.respond("note_face", *[fails] * tries, NO_FACE)

    say(f"Make an ad for {product_page_url}")

    assert results_of("read_page")[0].endswith("Kept 2 product photos.")
    # A photo that couldn't be noted counts as having a face; nothing is said about it.
    assert faces() == [True, False]
    assert Job.objects.get().status == Job.Status.PAGE_READ
    assert all("face" not in text.lower() for _, text in notices(api, session_id))


def test_an_attached_photo_whose_face_note_fails_counts_as_having_a_face(
    fake_model: FakeModel, httpserver: HTTPServer, say: Callable[..., None]
) -> None:
    link = a_page_with_no_photos(httpserver)
    reading(fake_model, link)
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {link}")
    fake_model.respond("produce", turn(calls=[("use_photos", {})]), turn(says="Got it."))
    fake_model.respond(
        "note_face", UnusableReply("The model refused", input_tokens=1_000, output_tokens=10)
    )

    say("Here's a photo of it", ("front.png", MUG_FRONT))

    assert results_of("use_photos") == [
        "Added 1 of the shop owner's photos. The job now has 1 product photo."
    ]
    assert faces() == [True]


# --- Paid once ------------------------------------------------------------------------------


def test_a_page_read_again_after_a_restart_pays_for_no_face_note_twice(
    api: APIClient,
    fake_model: FakeModel,
    product_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "produce", turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})])
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("note_face", FACE, NO_FACE)
    # Both photos are noted, and the worker stops as the second is kept.
    second_photo_kept = the_worker_stops(
        post_save, ProductPhoto, when=lambda photo: photo.position == 2
    )
    with second_photo_kept, pytest.raises(WorkerStopped):
        say(f"Make an ad for {product_page_url}")
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="I read your mug's page."))
    # What would be noted, were the photos wrongly paid for again.
    fake_model.respond("note_face", NO_FACE, FACE)

    restart_dead_producers()

    # The photos are kept again under new keys, and their notes are the ones paid for.
    photos = Job.objects.get().photos.all()
    assert [(file_store.read(photo.file), photo.has_face) for photo in photos] == [
        (MUG_FRONT, True),
        (MUG_SIDE, False),
    ]
    assert paid_for().count("note_face") == 2


# --- Shown in the admin -----------------------------------------------------------------------


def test_the_face_note_is_shown_in_the_admin(
    admin_client: Client, fake_model: FakeModel, product_page_url: str, say: Callable[..., None]
) -> None:
    reading(fake_model, product_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("note_face", FACE, NO_FACE)
    say(f"Make an ad for {product_page_url}")

    page = admin_client.get(f"/admin/jobs/job/{Job.objects.get().pk}/change/")

    assert page.status_code == 200
    assert "Has face" in page.content.decode()


def a_page_with_no_photos(httpserver: HTTPServer) -> str:
    """A product page with no photos on it. Gives the link."""
    httpserver.expect_request("/products/mug").respond_with_data(
        "<html><body><h1>Stoneware Mug</h1><p>$24.00</p></body></html>",
        content_type="text/html",
    )
    return httpserver.url_for("/products/mug")


def test_a_photo_kept_before_face_notes_counts_as_having_a_face() -> None:
    # Never noted, so it's taken as one with a face, as a photo whose noting failed is.
    executor = MigrationExecutor(connection)
    before = [("jobs", "0027_firecrawl_answers_kept")]
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    job = old.get_model("jobs", "Job").objects.create(product_url="https://shop.example/mug")
    old.get_model("jobs", "ProductPhoto").objects.create(job=job, position=1, file="photo-1")

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())

    assert ProductPhoto.objects.get().has_face is True
