"""Video pictures: each moment the reader kept from a product video is kept with the job as
a product photo, after the page's photos, through the same keep path. The page is read
through the chat; the reader's kept moments and the video's picked frames are set by the
test, as the reader and the thinning store them."""

import dataclasses
import io
import random
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest
from pytest_httpserver import HTTPServer

from adforge import file_store
from gateway.fake import FakeModel
from jobs import work
from jobs.models import Job, ProductPhoto, ProductVideo, ReferencePhoto
from jobs.product_videos import save_videos
from jobs.work import save_video_pictures

from .conftest import FakeFirecrawl, picture
from .test_producer_checks import images_shown
from .test_product_videos import player, read, tiny_mp4  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def frame_picture(seed: int, width: int = 640, height: int = 360) -> bytes:
    """A JPEG of random noise: no two seeds' fingerprints are near each other, nor near a
    photo of one colour."""
    noise = random.Random(seed).randbytes(32 * 18)
    small = PIL.Image.frombytes("L", (32, 18), noise).convert("RGB")
    file = io.BytesIO()
    small.resize((width, height)).save(file, format="JPEG")
    return file.getvalue()


# Two frames the thinning picked, unlike each other and unlike the page's photo.
OPENED = frame_picture(1)
IN_USE = frame_picture(2, 360, 640)


@pytest.fixture
def gallery_video(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    tiny_mp4: bytes,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> ProductVideo:
    """The shampoo page read through the chat, with one gallery video picked off it."""
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picked = work.pick_photos

    def picks(job: Job, page_read: work.PageRead) -> work.Picked:
        return dataclasses.replace(
            picked(job, page_read), videos=(player(1, f"{firecrawl.shop}/cdn/videos/gallery.mp4"),)
        )

    monkeypatch.setattr(work, "pick_photos", picks)
    read(fake_model, say, shampoo_page_url)
    return ProductVideo.objects.get()


def frames(*pictures: tuple[float, bytes]) -> list[dict[str, Any]]:
    """Picked frames as the thinning stores them on the video: each one's key and second."""
    return [
        {"key": file_store.save(f"jobs/test/videos/1-pick-{number}.jpg", content), "second": at}
        for number, (at, content) in enumerate(pictures, 1)
    ]


def kept(second: float, shows: str) -> dict[str, Any]:
    """A moment the reader kept, as its answer is stored on the video."""
    return {"second": second, "shows": shows, "why": "A scene needs it."}


def test_two_kept_moments_become_two_product_photos_after_the_page_photos(
    gallery_video: ProductVideo, fake_model: FakeModel
) -> None:
    job = gallery_video.job
    (page_photo,) = job.photos.all()
    # The second picture shows a face: it is noted by a call of its own, as a page photo
    # the picker never saw is.
    fake_model.respond("note_face", {"has_face": False}, {"has_face": True})

    saved = save_video_pictures(
        job,
        gallery_video,
        [kept(0.4, "The bottle opened."), kept(1.6, "The shampoo worked into wet hair.")],
        frames((0.4, OPENED), (1.0, frame_picture(3)), (1.6, IN_USE)),
    )

    photos = list(job.photos.all())
    assert photos[0] == page_photo
    assert saved == photos[1:]
    assert [
        (photo.position, photo.from_video, photo.at_second, photo.note, photo.has_face)
        for photo in saved
    ] == [
        (2, gallery_video, 0.4, "The bottle opened.", False),
        (3, gallery_video, 1.6, "The shampoo worked into wet hair.", True),
    ]
    # The picked frame at that second, at its full size, from the video's link.
    assert [file_store.read(photo.file) for photo in saved] == [OPENED, IN_USE]
    assert {photo.source_url for photo in saved} == {gallery_video.source_url}
    # A page photo isn't a video picture.
    assert (page_photo.from_video, page_photo.at_second, page_photo.note) == (None, None, "")


def test_a_kept_moment_that_copies_a_page_photo_or_has_no_picked_frame_is_not_kept(
    gallery_video: ProductVideo, fake_model: FakeModel
) -> None:
    job = gallery_video.job
    (page_photo,) = job.photos.all()
    fake_model.respond("note_face", {"has_face": False})

    saved = save_video_pictures(
        job,
        gallery_video,
        [
            kept(0.2, "The bottle on its own."),
            kept(0.8, "The bottle opened."),
            # The same moment named twice is one picture.
            kept(0.8, "The cap off."),
            # No picked frame is near this second.
            kept(9.0, "Something else."),
        ],
        # The first is the page's photo, cut from the video at another size.
        frames((0.2, file_store.read(page_photo.file)), (0.8, OPENED)),
    )

    assert [(photo.position, photo.at_second) for photo in saved] == [(2, 0.8)]
    assert job.photos.count() == 2


def test_a_video_picture_is_never_a_reference_photo(
    gallery_video: ProductVideo, fake_model: FakeModel
) -> None:
    fake_model.respond("note_face", {"has_face": False})

    save_video_pictures(
        gallery_video.job, gallery_video, [kept(0.4, "The bottle opened.")], frames((0.4, OPENED))
    )

    assert not ReferencePhoto.objects.exists()


def test_48_page_photos_and_5_video_pictures_show_the_planner_50_and_say_which_are_left_out(
    gallery_video: ProductVideo, fake_model: FakeModel
) -> None:
    job = gallery_video.job
    for position in range(2, 49):
        work.keep_photo(
            job,
            position,
            picture(100 + position, 100, (143, 170, 140)),
            "image/png",
            source_url=f"https://shop.example/{position}.png",
        )
    fake_model.respond("note_face", *[{"has_face": False}] * 5)
    seconds = (0.2, 0.6, 1.0, 1.4, 1.8)
    save_video_pictures(
        job,
        gallery_video,
        [kept(second, f"Moment {second}.") for second in seconds],
        frames(*((second, frame_picture(10 + n)) for n, second in enumerate(seconds))),
    )
    # A photo the shop owner attached comes after everything.
    work.keep_photo(job, 54, picture(300, 300, (9, 9, 9)), "image/png")
    assert ProductPhoto.objects.filter(from_video=gallery_video).count() == 5
    fake_model.respond(
        "plan_ad",
        {
            "decision": "ask",
            "reason": "The size is unclear.",
            "question": "Which size?",
            "plan": None,
        },
    )

    work.plan(job)

    assert images_shown("plan_ad") == [
        [f"Photo {position}" for position in range(1, 49)]
        + [
            "Photo 49 (from the product's video at 0.2 s: Moment 0.2.)",
            "Photo 50 (from the product's video at 0.6 s: Moment 0.6.)",
        ]
    ]
    job.refresh_from_db()
    assert [warning["text"] for warning in job.warnings] == [
        "The ad has 54 product photos, more than the 50 a model can be shown at once, so "
        "photos 51, 52, 53 and 54 are left out when it's planned or a line is rewritten."
    ]


def test_a_page_read_again_drops_its_video_pictures_with_its_videos_and_keeps_the_new_ones(
    gallery_video: ProductVideo,
    fake_model: FakeModel,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    job = gallery_video.job
    fake_model.respond("note_face", *[{"has_face": False}] * 3)
    save_video_pictures(
        job,
        gallery_video,
        [kept(0.4, "The bottle opened."), kept(1.6, "In use.")],
        frames((0.4, OPENED), (1.6, IN_USE)),
    )
    # The worker stopped before the page counted as read, so the page is read again.
    Job.objects.filter(pk=job.pk).update(page_html_key="", status=Job.Status.READING_PAGE)

    read(fake_model, say, shampoo_page_url)

    (again,) = job.videos.all()
    assert again.pk != gallery_video.pk
    assert [(photo.position, photo.from_video) for photo in job.photos.all()] == [(1, None)]
    (saved,) = save_video_pictures(
        job, again, [kept(0.4, "The bottle opened.")], frames((0.4, OPENED))
    )
    assert (saved.position, saved.from_video) == (2, again)


def test_videos_kept_again_drop_their_video_pictures(
    gallery_video: ProductVideo, fake_model: FakeModel
) -> None:
    job = gallery_video.job
    fake_model.respond("note_face", {"has_face": False})
    save_video_pictures(
        job, gallery_video, [kept(0.4, "The bottle opened.")], frames((0.4, OPENED))
    )

    save_videos(job, [])

    assert not job.photos.exclude(from_video=None).exists()
    assert job.photos.count() == 1
