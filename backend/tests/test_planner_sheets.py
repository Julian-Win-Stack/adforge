"""What the planner is shown (spec #210 point 9): the page videos' moments first, as text,
then the library as numbered sheets of pictures with each picture's number and label under
it, then what the videos say and the page's text. The planner names pictures by their library
numbers. Driven through the chat from the shop owner's first message, the picker, the watcher
and the planner faked at the gateway, the shop's page on the local web server."""

import io
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelRequest
from jobs.library import (
    LookFrame,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    add_to_library,
    library_of,
)
from jobs.models import Job
from jobs.work import keep_photo

from .conftest import (
    PLAN,
    READABLE,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    a_plan_with,
    broll,
    picture,
)
from .test_picker_labels import LABELS, labelled_page  # noqa: F401 (a fixture)
from .test_producer_page import notices
from .test_product_videos import picks_videos, player, tiny_mp4  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

Say = Callable[..., None]

# Six photos off the page: the front, the back, the left side, the cap off, in use and a
# line-up, each with the picker's label.
SIX = [8, 101, 102, 103, 104, 105]
SIX_PICKED = {
    **SHAMPOO_PICKED,
    "gallery_images": SIX,
    "pictures": [label for label in LABELS if label["picture"] in {f"I{n}" for n in SIX}],
}
# The page video, 2 s long: two moments and what is said.
WATCHED = {
    "shows_this_product": True,
    "moments": [
        {
            "start": 0.0,
            "end": 1.0,
            "happens": "The cap is twisted off.",
            "on_screen": "The bottle in one hand, the other turning the cap.",
        },
        {
            "start": 1.0,
            "end": 2.0,
            "happens": "The pump is pressed and shampoo comes out.",
            "on_screen": "A palm under the pump, the shampoo landing in it.",
        },
    ],
    "look_frames": [],
    "colour": "amber",
    "transcript": "Twist, pump, lather.",
}
# A B-roll scene of the pump pressed, sent the in-use photo and the line-up's bottle, and the
# video's second moment.
PUMPED = broll(
    {
        "line": "One press of the pump gives you all the shampoo you need for a wash.",
        "shows": "a hand presses the pump and shampoo comes out",
        "scene_pictures": [{"number": 5, "for": "in-use"}, {"number": 6, "for": "result"}],
        "piece": {"video": 1, "moment": 2},
    }
)
PLANNED = a_plan_with(
    scenes=[
        {"line": "Meet the detox shampoo."},
        PUMPED,
        {"line": "Get yours today."},
    ],
    product_name="detox shampoo",
    product_behaviour="A press of the pump gives a dab of shampoo into the palm.",
)


def planned_from_the_first_message(
    fake_model: FakeModel, say: Say, link: str, plan: dict[str, Any]
) -> list[ModelRequest[Any]]:
    """The owner's first message: the page read, its video watched, then the ad planned with
    the planner answering `plan`. Gives what the planner was asked."""
    asked: list[ModelRequest[Any]] = []

    def planning(request: ModelRequest[Any]) -> dict[str, Any]:
        asked.append(request)
        return plan

    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(calls=[("plan_ad", {})]),
        turn(says="Here's the plan."),
    )
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", SIX_PICKED)
    fake_model.respond("watch_video", WATCHED)
    fake_model.answer_unscripted("plan_ad", planning)
    say(f"Make an ad for {link}")
    return asked


def opened(data: bytes) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(data)) as shown:
        shown.load()
        return shown


def test_the_planner_gets_the_moments_first_then_the_library_on_numbered_sheets(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    labelled_page: str,  # noqa: F811 (the fixture)
    say: Say,
    picks_videos: list[dict[str, Any]],  # noqa: F811 (a fixture)
    tiny_mp4: bytes,  # noqa: F811 (a fixture)
) -> None:
    httpserver.expect_request("/cdn/videos/how-to.mp4").respond_with_data(
        tiny_mp4, content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/how-to.mp4"))

    (request,) = planned_from_the_first_message(fake_model, say, labelled_page, PLANNED)

    handoff = request.handoff.model_dump()
    # The moments come first, each with its video, number, seconds, what happens and what
    # is on screen; what the video says comes after the library's count.
    assert list(handoff)[0] == "moments"
    assert handoff["moments"] == (
        "Video 1, moment 1, 0-1 s: The cap is twisted off. On screen: The bottle in one hand, "
        "the other turning the cap.\n"
        "Video 1, moment 2, 1-2 s: The pump is pressed and shampoo comes out. On screen: A "
        "palm under the pump, the shampoo landing in it."
    )
    assert handoff["video_transcripts"] == ["Twist, pump, lather."]
    assert list(handoff).index("video_transcripts") > list(handoff).index("library_pictures")
    assert (handoff["library_pictures"], handoff["pictures_not_shown"]) == (6, 0)
    # Two sheets, four pictures and then two, each named by the numbers on it.
    assert [image.label for image in request.images] == [
        "Library pictures 1 to 4 (each numbered and labelled under it)",
        "Library pictures 5 to 6 (each numbered and labelled under it)",
    ]
    first, second = (opened(image.data) for image in request.images)
    # Two across: the second sheet is one row of two, the first two rows.
    assert first.width == second.width
    assert first.height > second.height
    # Each picture's label is in a black bar under it: the sheet's top edge is the picture's
    # fitted backdrop, not a bar.
    assert first.getpixel((first.width // 4, 12)) != (0, 0, 0)
    # The plan names pictures by those numbers, and they are the library's.
    job_library = {item.number: item for item in library_of(Job.objects.get())}
    assert sorted(job_library) == [1, 2, 3, 4, 5, 6]
    (scene,) = Job.objects.get().scenes.filter(number=2)
    assert [job_library[pick["number"]].label for pick in scene.scene_pictures] == [
        "A hand pressing the pump, the shampoo coming out.",
        "Three bottles in a row on a shelf.",
    ]
    assert scene.piece == {"video": 1, "moment": 2}


def test_a_library_bigger_than_the_sheets_hold_is_shown_up_to_200_and_the_rest_is_counted(
    api: APIClient,
    fake_model: FakeModel,
    page_read: str,
    session_id: str,
    say: Say,
) -> None:
    # The mug's 2 page photos and 208 more pictures: 210 in the library.
    job = Job.objects.get()
    front = job.photos.get(position=1)
    for _ in range(208):
        add_to_library(
            job,
            front.file,
            PictureLabel(kind="side", side="front", alone="yes", colour="green", label="The mug."),
            PagePhoto(photo=1),
        )
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", PLAN)

    say("Plan it")

    (call,) = ModelCall.objects.filter(purpose="plan_ad")
    # 50 sheets of 4: pictures 1 to 200.
    assert len(call.images) == 50
    assert call.images[-1]["label"] == (
        "Library pictures 197 to 200 (each numbered and labelled under it)"
    )
    # The plan's record holds how many were not shown, and so does the chat, in red.
    assert (call.handoff["library_pictures"], call.handoff["pictures_not_shown"]) == (210, 10)
    told = (
        "The product's library has 210 pictures, more than the 200 the planner can be shown, "
        "so 10 of them are not shown: pictures 201 to 210 are left out when the ad is planned "
        "or a scene is rewritten."
    )
    assert ("problem", told) in notices(api, session_id)
    assert told in [warning["text"] for warning in Job.objects.get().warnings]


def test_needs_and_colour_photos_named_by_library_number_are_kept_as_the_photos_they_are(
    api: APIClient,
    fake_model: FakeModel,
    page_read: str,
    session_id: str,
    say: Say,
) -> None:
    # The mug's 2 page photos are pictures 1 and 2; picture 3 is a look frame the job kept no
    # product photo for; picture 4 is the shop owner's photo, the job's photo 3.
    job = Job.objects.get()
    mug = PictureLabel(kind="side", side="front", alone="yes", colour="green", label="The mug.")
    add_to_library(job, job.photos.get(position=1).file, mug, LookFrame(video=1, second=1.0))
    owners = keep_photo(job, 3, picture(90, 90, (143, 170, 140)), "image/png", has_face=False)
    add_to_library(job, owners.file, mug, OwnerPhoto(original=owners.file))
    poured = broll(
        {
            "line": "Pour in your tea and watch the steam curl up from the glaze.",
            "shows": "hot tea poured into the mug, steam rising",
            "needs": [
                {"what": "the handle", "photos": [3, 4]},
                {"what": "the glaze", "photos": [3]},
            ],
        }
    )
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond(
        "plan_ad",
        a_plan_with(
            scenes=[PLAN["plan"]["scenes"][0], poured, PLAN["plan"]["scenes"][2]],
            colour_photos=[4],
        ),
    )

    say("Plan it")

    job = Job.objects.get()
    assert job.scenes.get(number=2).needs == [{"what": "the handle", "photos": [3]}]
    assert list(
        job.photos.filter(shows_product_colour=True).values_list("position", flat=True)
    ) == [3]
    assert (
        "problem",
        "Library picture 3 has no product photo a starting picture can be made from, so the "
        "scenes that need it are made without it.",
    ) in notices(api, session_id)
