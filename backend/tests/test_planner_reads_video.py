"""The planner and the fact check read the product's videos: what each video says, and the
pictures cut from them. Through the chat, with each video's transcript and its video pictures
set on the job by the test where the page read would keep them."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProductPhoto, ProductVideo
from jobs.work import keep_photo

from .conftest import (
    NO_CHOICES,
    PLAN,
    FakeFirecrawl,
    a_plan_with,
    broll,
    facts_ok,
    handoffs,
    picture,
    results_of,
)
from .test_product_videos import picks_videos, player, read, tiny_mp4  # noqa: F401 (fixtures)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

SAYS = "Rinse it under warm water and the glaze comes up shining."
PICTURE_NOTE = "the mug's inside, which no photo shows"
PICTURE_LABEL = f"Photo 3 (from the product's video at 1.8 s: {PICTURE_NOTE})"


def video(job: Job, position: int, *, transcript: str, file: str = "") -> ProductVideo:
    """A gallery video of the job, as the page read keeps it, with its transcript."""
    return ProductVideo.objects.create(
        job=job,
        position=position,
        source_url=f"https://shop.example/videos/{position}.mp4",
        kind=ProductVideo.Kind.VIDEO,
        file=file or (f"jobs/{job.pk}/videos/{position}.mp4" if transcript else ""),
        transcript=transcript,
    )


def video_picture(
    job: Job, position: int, from_video: ProductVideo, at_second: float, note: str
) -> ProductPhoto:
    """A picture cut from `from_video`, kept as a product photo with the reader's note."""
    photo = keep_photo(
        job,
        position,
        picture(120, 120, (90, 120, 80)),
        "image/png",
        source_url=from_video.source_url,
    )
    photo.from_video = from_video
    photo.at_second = at_second
    photo.note = note
    photo.save(update_fields=["from_video", "at_second", "note"])
    return photo


@pytest.fixture
def mug_with_videos(page_read: str) -> Job:
    """The mug's page read with its 2 photos, a video that talks, a music-only one, one with
    no file, and a picture cut from the first."""
    job = Job.objects.get()
    talking = video(job, 1, transcript=SAYS)
    video(job, 2, transcript="music only")
    video(job, 3, transcript="")
    video_picture(job, 3, talking, 1.8, PICTURE_NOTE)
    return job


def plan_it(fake_model: FakeModel, say: Callable[..., None], plan: dict[str, Any]) -> None:
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", plan)
    say("Plan it")


def test_the_planner_is_told_what_each_video_says_and_shown_its_pictures_with_their_notes(
    fake_model: FakeModel, mug_with_videos: Job, say: Callable[..., None]
) -> None:
    plan_it(fake_model, say, PLAN)

    (call,) = ModelCall.objects.filter(purpose="plan_ad")
    # One per video with a file, in the videos' order; a video with no file says nothing.
    assert call.handoff["video_transcripts"] == [SAYS, "music only"]
    assert call.handoff["photo_count"] == 3
    assert [image["label"] for image in call.images] == ["Photo 1", "Photo 2", PICTURE_LABEL]


def test_a_job_without_videos_tells_the_planner_its_videos_say_nothing(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    plan_it(fake_model, say, PLAN)

    assert handoffs("plan_ad")[0]["video_transcripts"] == []


def test_the_fact_check_takes_what_the_video_says_as_the_shops_words_and_sees_its_picture(
    fake_model: FakeModel, mug_with_videos: Job, say: Callable[..., None]
) -> None:
    # Scene 2's line is what the video says, and it shows the inside only the video showed.
    plan_it(
        fake_model,
        say,
        a_plan_with(
            scenes=[
                PLAN["plan"]["scenes"][0],
                broll(
                    {
                        "line": "Rinse it under warm water and it comes up shining.",
                        "shows": "The mug's glazed inside under running water.",
                        "needs": [{"what": "the mug's inside", "photos": [3]}],
                    }
                ),
                PLAN["plan"]["scenes"][2],
            ]
        ),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say("Make the person and check the script")

    (call,) = ModelCall.objects.filter(purpose="fact_check")
    assert call.handoff["video_transcripts"] == [SAYS, "music only"]
    # The colour photo and the picture scene 2 needs, the picture by where it was cut.
    assert [image["label"] for image in call.images] == ["Photo 1", PICTURE_LABEL]
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")


def test_48_page_photos_and_5_video_pictures_are_planned_from_50_page_photos_first_saying_so(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    # The page's 2 photos and 46 more, 5 pictures from its video, then the shop owner's 1.
    job = Job.objects.get()
    for position in range(3, 49):
        keep_photo(
            job,
            position,
            picture(100 + position, 100, (143, 170, 140)),
            "image/png",
            source_url=f"https://shop.example/{position}.png",
        )
    talking = video(job, 1, transcript=SAYS)
    for n, position in enumerate(range(49, 54)):
        video_picture(job, position, talking, float(n), f"moment {n}")
    keep_photo(job, 54, picture(80, 80, (10, 10, 10)), "image/png")

    plan_it(fake_model, say, PLAN)

    (call,) = ModelCall.objects.filter(purpose="plan_ad")
    assert [image["label"] for image in call.images] == [
        *(f"Photo {position}" for position in range(1, 49)),
        "Photo 49 (from the product's video at 0.0 s: moment 0)",
        "Photo 50 (from the product's video at 1.0 s: moment 1)",
    ]
    job.refresh_from_db()
    assert job.warnings[-1]["text"] == (
        "The ad has 54 product photos, more than the 50 a model can be shown at once, so "
        "photos 51, 52, 53 and 54 are left out when it's planned or a line is rewritten."
    )


# --- The whole way, from the shop owner's link ------------------------------------------------


def test_a_moment_only_the_gallery_video_shows_is_planned_as_a_scene_and_passes_the_checks(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    picks_videos: list[dict[str, Any]],  # noqa: F811 (a fixture)
    tiny_mp4: bytes,  # noqa: F811 (a fixture)
) -> None:
    httpserver.expect_request("/cdn/videos/gallery.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/gallery.mp4"))
    read(fake_model, say, shampoo_page_url)

    # What the page read keeps of the video: what it says, and the one moment its reader
    # kept, cut from the frame at that second.
    job = Job.objects.get()
    (gallery,) = job.videos.all()
    gallery.transcript = "Two pumps lather through even the thickest hair."
    gallery.save(update_fields=["transcript"])
    page_photos = job.photos.count()
    kept = ProductPhoto.objects.create(
        job=job,
        position=page_photos + 1,
        source_url=gallery.source_url,
        file=gallery.frames[1],
        from_video=gallery,
        at_second=0.6,
        note="the pump pressed and the lather in a palm",
    )

    plan_it(
        fake_model,
        say,
        a_plan_with(
            scenes=[
                {"line": "Meet the detox shampoo.", "overlay": None},
                broll(
                    {
                        "line": "Two pumps lather through even the thickest hair.",
                        "shows": "Two pumps of the shampoo lathered in a palm.",
                        "needs": [{"what": "the lather", "photos": [kept.position]}],
                    }
                ),
                {"line": "Get yours today.", "overlay": None},
            ],
            colour_photos=[1],
            product_name="detox shampoo",
        ),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    say("Make the person and check the script")

    picture_label = (
        f"Photo {kept.position} (from the product's video at 0.6 s: the pump pressed and the "
        "lather in a palm)"
    )
    (planning,) = ModelCall.objects.filter(purpose="plan_ad")
    assert picture_label in [image["label"] for image in planning.images]
    assert planning.handoff["video_transcripts"] == [gallery.transcript]
    scene = job.scenes.get(number=2)
    assert scene.needs == [{"what": "the lather", "photos": [kept.position]}]
    (checking,) = ModelCall.objects.filter(purpose="fact_check")
    assert checking.handoff["video_transcripts"] == [gallery.transcript]
    assert picture_label in [image["label"] for image in checking.images]
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
