"""The planner and the fact check read the product's videos: what each video says, and the
pictures cut from them, which the planner sees in the library as look frames. Through the
chat, with each video's transcript, its video pictures and their look frames set on the job by
the test where the page read would keep them."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.library import LookFrame, PictureLabel, add_to_library, library_of
from jobs.library_sheets import tile_text
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


def look_frame(job: Job, photo: ProductPhoto) -> int:
    """The video picture `photo` entered in the library as a look frame, as the watcher keeps
    it. Gives its library number."""
    assert photo.from_video is not None and photo.at_second is not None
    return add_to_library(
        job,
        photo.file,
        PictureLabel(kind="state", side="none", alone="yes", colour="green", label=photo.note),
        LookFrame(video=photo.from_video.position, second=photo.at_second),
    ).number


def video_picture(
    job: Job, position: int, from_video: ProductVideo, at_second: float, note: str
) -> ProductPhoto:
    """A picture cut from `from_video`, kept as a product photo with the reader's note and
    entered in the library as a look frame."""
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
    look_frame(job, photo)
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


def test_the_planner_is_told_what_each_video_says_and_shown_its_pictures_marked_on_the_sheet(
    fake_model: FakeModel, mug_with_videos: Job, say: Callable[..., None]
) -> None:
    plan_it(fake_model, say, PLAN)

    (call,) = ModelCall.objects.filter(purpose="plan_ad")
    # One per video with a file, in the videos' order; a video with no file says nothing.
    assert call.handoff["video_transcripts"] == [SAYS, "music only"]
    assert call.handoff["library_pictures"] == 3
    assert [image["label"] for image in call.images] == [
        "Library pictures 1 to 3 (each numbered and labelled under it)"
    ]
    # The picture cut from the video is written under on the sheet as from the video.
    *_, frame = library_of(mug_with_videos)
    assert tile_text(frame) == f"3. {PICTURE_NOTE} (from the product's video 1 at 1.8 s)"


def test_a_job_without_videos_tells_the_planner_its_videos_say_nothing(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    plan_it(fake_model, say, PLAN)

    assert handoffs("plan_ad")[0]["video_transcripts"] == []


def test_the_fact_check_takes_what_the_video_says_as_the_shops_words_and_sees_its_picture(
    fake_model: FakeModel, mug_with_videos: Job, say: Callable[..., None]
) -> None:
    # Scene 2's line is what the video says, and it shows the inside only the video showed:
    # the plan names the picture cut from the video as its scene picture, by library number.
    *_, frame = library_of(mug_with_videos)
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
                        "needs": [{"what": "the mug's inside", "photos": [frame.number]}],
                        "scene_pictures": [{"number": frame.number, "for": "content"}],
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
    # The colour photo and scene 2's scene picture, the picture by where it was cut (the
    # fact check sees the pictures the plan names as scene pictures, spec #210 point 11).
    assert [image["label"] for image in call.images] == ["Photo 1", PICTURE_LABEL]
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")


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
    frame = look_frame(job, kept)

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
                        "needs": [{"what": "the lather", "photos": [frame]}],
                        "scene_pictures": [{"number": frame, "for": "in-use"}],
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
    assert planning.handoff["library_pictures"] == frame
    assert planning.handoff["video_transcripts"] == [gallery.transcript]
    # The plan names the look frame by its library number; its need is kept as the photo.
    scene = job.scenes.get(number=2)
    assert scene.needs == [{"what": "the lather", "photos": [kept.position]}]
    (checking,) = ModelCall.objects.filter(purpose="fact_check")
    assert checking.handoff["video_transcripts"] == [gallery.transcript]
    assert picture_label in [image["label"] for image in checking.images]
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
