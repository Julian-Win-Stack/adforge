"""The photo picker shown what the screenshot can't draw, driven through the chat: pictures a
scroll box or carousel slide hides, every photo in the shop's record of the product, and the
page's video players, each in labelled grids beside the screenshot."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, ModelRequest
from jobs.models import Job

from .conftest import (
    READABLE,
    SHAMPOO_PHOTO_PATH,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    paid_for,
    photo,
)
from .test_producer_page import notices, read_page_result, reading

pytestmark = pytest.mark.django_db(transaction=True)

SCREENSHOT_PARTS = [
    "Part 1 of 3 (from 0 px down the page):",
    "Part 2 of 3 (from 1200 px down the page):",
    "Part 3 of 3 (from 2400 px down the page):",
]
OFFICIAL_R1 = "Official photos: R1 (each labelled in its black bar)"
# The gallery's box on the marked page: its big photo I8 is drawn, its other slides aren't.
GALLERY_BOX = {
    "id": 2,
    "x": 260,
    "y": 167,
    "w": 1534,
    "h": 942,
    "tag": "div",
    "cls": "slider",
    "aria": "",
}


def marks_of(firecrawl: FakeFirecrawl) -> dict[str, Any]:
    returns = firecrawl.marked["data"]["actions"]["javascriptReturns"]
    marks: dict[str, Any] = json.loads(returns[3]["value"])
    return marks


def mark(firecrawl: FakeFirecrawl, marks: dict[str, Any]) -> None:
    returns = firecrawl.marked["data"]["actions"]["javascriptReturns"]
    returns[3]["value"] = json.dumps(marks)


def hidden_picture(firecrawl: FakeFirecrawl, n: int, **more: Any) -> dict[str, Any]:
    """A picture the page script numbered but couldn't draw: a slide the gallery's box cuts
    off, served from the local shop."""
    return {
        "n": n,
        "src": f"{firecrawl.shop}/cdn/hidden/{n}.png",
        "srcset": "",
        "alt": f"Slide {n}",
        "href": "",
        "x": 1800,
        "y": 167,
        "nw": 1500,
        "nh": 2000,
        "w": 763,
        "h": 942,
        "vis": 0,
        "shown": False,
        "box": 2,
        **more,
    }


def serve_hidden(httpserver: HTTPServer, *numbers: int) -> None:
    for n in numbers:
        httpserver.expect_request(f"/cdn/hidden/{n}.png").respond_with_data(
            photo(100 + n, 300, 400), content_type="image/png"
        )


def with_hidden_gallery(firecrawl: FakeFirecrawl, httpserver: HTTPServer) -> None:
    """The shampoo's gallery as a carousel: I8 drawn in box 2, and two hidden slides, I40 and
    I41, in the same box."""
    marks = marks_of(firecrawl)
    for picture in marks["imgs"]:
        if picture["n"] == 8:
            picture.update(vis=1, shown=True, box=2)
    marks["imgs"] += [hidden_picture(firecrawl, 40), hidden_picture(firecrawl, 41, x=2600)]
    marks["boxes"] = [GALLERY_BOX]
    mark(firecrawl, marks)
    serve_hidden(httpserver, 40, 41)


def picker_call() -> ModelCall:
    (picker,) = ModelCall.objects.filter(purpose="pick_photos")
    return picker


def test_hidden_gallery_pictures_reach_the_picker_in_a_grid_and_a_picked_one_is_kept(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    with_hidden_gallery(firecrawl, httpserver)
    # Two more hidden pictures: an icon too small to show, and one in a closed menu.
    marks = marks_of(firecrawl)
    marks["imgs"] += [
        hidden_picture(firecrawl, 42, w=60, h=400),
        hidden_picture(firecrawl, 44, w=400, h=60),
        # Its picture is gone from the shop.
        hidden_picture(firecrawl, 45),
        hidden_picture(firecrawl, 43, y=5000, w=400, h=300, box=0),
    ]
    mark(firecrawl, marks)
    serve_hidden(httpserver, 42, 43, 44)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 40]})

    say(f"Make an ad for {shampoo_page_url}")

    picker = picker_call()
    assert [image["label"] for image in picker.images] == [
        *SCREENSHOT_PARTS,
        OFFICIAL_R1,
        "Hidden pictures: I40 (hidden), I41 (hidden), I43 (hidden) (each labelled in its "
        "black bar)",
    ]
    assert picker.handoff["hidden_pictures"] == (
        "I40 (hidden): 0% visible, 763x942 px at 167 px down the page, cut off by box 2, box "
        "shared with drawn pictures I8\n"
        "I41 (hidden): 0% visible, 763x942 px at 167 px down the page, cut off by box 2, box "
        "shared with drawn pictures I8\n"
        "I43 (hidden): 0% visible, 400x300 px at 5000 px down the page, not displayed, no drawn "
        "picture shares its box"
    )
    assert read_page_result().endswith("Kept 2 product photos.")
    shop = firecrawl.shop
    assert [photo.source_url for photo in Job.objects.get().photos.all()] == [
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478",
        f"{shop}/cdn/hidden/40.png",
    ]
    kept = Job.objects.get().photos.last()
    assert kept is not None and file_store.read(kept.file) == photo(140, 300, 400)
    assert paid_for().count("pick_photos") == 1


def record_with_photos(firecrawl: FakeFirecrawl, httpserver: HTTPServer, count: int) -> None:
    """The shop's record of the shampoo with `count` official photos, R1 the photo the page
    shows as I8, the rest photos of their own, each served from the local shop."""
    record = firecrawl.product["data"]["product"]
    first = record["variants"][0]["images"][0]["url"]
    others = [f"{firecrawl.shop}/cdn/record/{k}.png" for k in range(2, count + 1)]
    for variant in record["variants"]:
        variant["images"] = [{"url": first}, *({"url": url} for url in others)]
    for k in range(2, count + 1):
        httpserver.expect_request(f"/cdn/record/{k}.png").respond_with_data(
            photo(200 + k, 300, 400), content_type="image/png"
        )


def test_every_record_photo_is_offered_and_a_picked_one_is_kept_once(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    # The record's fifth photo comes back as a web page: it isn't shown.
    httpserver.expect_request("/cdn/record/5.png").respond_with_data(
        "<html>Gone</html>", content_type="text/html"
    )
    record_with_photos(firecrawl, httpserver, 9)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    # R1 is the same photo as I8, so only R7 is kept from the record.
    fake_model.respond(
        "pick_photos",
        # R0 and R10 aren't in the record.
        {**SHAMPOO_PICKED, "gallery_images": [8], "record_images": [0, 1, 7, 10]},
    )

    say(f"Make an ad for {shampoo_page_url}")

    picker = picker_call()
    assert [image["label"] for image in picker.images][3:] == [
        "Official photos: R1, R2, R3, R4 (each labelled in its black bar)",
        "Official photos: R6, R7, R8, R9 (each labelled in its black bar)",
    ]
    assert picker.handoff["official_photos"].splitlines()[3:] == [
        f"R4: {firecrawl.shop}/cdn/record/4.png",
        f"R6: {firecrawl.shop}/cdn/record/6.png",
        f"R7: {firecrawl.shop}/cdn/record/7.png",
        f"R8: {firecrawl.shop}/cdn/record/8.png",
        f"R9: {firecrawl.shop}/cdn/record/9.png",
    ]
    shop = firecrawl.shop
    assert [photo.source_url for photo in Job.objects.get().photos.all()] == [
        f"{shop}{SHAMPOO_PHOTO_PATH}?v=1783623478",
        f"{shop}/cdn/record/7.png",
    ]
    # The picker notes faces only on the page's pictures: the record's photo gets its own call.
    assert paid_for().count("note_face") == 1


TINY_MP4_PATH = "/cdn/videos/demo.mp4"


def tiny_mp4(colour: str = "0x3366cc") -> bytes:
    """A one-second video, all one colour."""
    import subprocess
    import tempfile
    from pathlib import Path

    from django.conf import settings

    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "demo.mp4"
        subprocess.run(
            [settings.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i"]
            + [f"color=c={colour}:size=96x96:rate=10", "-t", "1", "-pix_fmt", "yuv420p", str(file)],
            check=True,
            timeout=60,
        )
        return file.read_bytes()


def player(n: int, kind: str, src: str, **more: Any) -> dict[str, Any]:
    return {
        "n": n,
        "kind": kind,
        "src": src,
        "poster": "",
        "x": 260,
        "y": 167,
        "w": 763,
        "h": 942,
        "vis": 1,
        "shown": True,
        "box": 2,
        "href": "",
        "aria": "",
        "autoplay": False,
        "loop": False,
        "muted": False,
        "controls": True,
        **more,
    }


def test_video_players_reach_the_picker_by_their_first_frames_and_picked_ones_are_kept(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = firecrawl.shop
    monkeypatch.setattr(
        "jobs.picker_grids.YOUTUBE_THUMBNAIL", f"{shop}/youtube/{{id}}/hqdefault.jpg"
    )
    httpserver.expect_request("/cdn/videos/poster.jpg").respond_with_data(
        photo(301, 300, 400), content_type="image/png"
    )
    httpserver.expect_request("/youtube/abcDEF123/hqdefault.jpg").respond_with_data(
        photo(302, 300, 400), content_type="image/png"
    )
    httpserver.expect_request(TINY_MP4_PATH).respond_with_data(tiny_mp4(), content_type="video/mp4")
    marks = marks_of(firecrawl)
    for picture in marks["imgs"]:
        if picture["n"] == 8:
            picture.update(vis=1, shown=True, box=2)
    marks["boxes"] = [GALLERY_BOX]
    marks["vids"] = [
        player(1, "video", f"{shop}/cdn/videos/promo.mp4", poster=f"{shop}/cdn/videos/poster.jpg"),
        player(2, "youtube", "https://www.youtube.com/embed/abcDEF123", box=0, y=3000, shown=False),
        player(3, "video", f"{shop}{TINY_MP4_PATH}", muted=True, shown=False, vis=0),
        player(4, "vimeo", "https://player.vimeo.com/video/123", box=0),
        # The same video again in a drawn player: listed once, as the drawn one.
        player(5, "video", f"{shop}{TINY_MP4_PATH}", box=0),
    ]
    mark(firecrawl, marks)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond(
        "pick_photos",
        {**SHAMPOO_PICKED, "gallery_videos": [1, 5, 3, 9], "video_notes": "V2 is a review."},
    )

    say(f"Make an ad for {shampoo_page_url}")

    picker = picker_call()
    assert [image["label"] for image in picker.images][3:] == [
        OFFICIAL_R1,
        "Video players: V1, V2, V5 (each labelled in its black bar)",
    ]
    assert picker.handoff["video_players"] == (
        "V1: video, 763x942 px at 167 px down the page, drawn, box 2 shared with pictures I8; "
        "controls; picture: poster\n"
        "V2: youtube, 763x942 px at 3000 px down the page, hidden, box 0 (no drawn picture "
        "shares it); controls; picture: youtube thumbnail\n"
        "V4: vimeo, 763x942 px at 167 px down the page, drawn, box 0 (no drawn picture shares "
        "it); controls; NO PICTURE (embedded player)\n"
        "V5: video, 763x942 px at 167 px down the page, drawn, box 0 (no drawn picture shares "
        "it); controls; picture: first frame"
    )
    # The picked players are kept as the job's product videos. V3, a copy of V5 it wasn't
    # shown, and V9, which isn't on the page, are dropped.
    job = Job.objects.get()
    assert [video.source_url for video in job.videos.order_by("position")] == [
        f"{shop}/cdn/videos/promo.mp4",
        f"{shop}{TINY_MP4_PATH}",
    ]


def test_a_page_with_many_hidden_pictures_record_photos_and_videos_stays_under_50_images(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    shop = firecrawl.shop
    record_with_photos(firecrawl, httpserver, 30)
    marks = marks_of(firecrawl)
    # 60 hidden pictures, the first 20 smaller than the rest, and 25 video players.
    marks["imgs"] += [
        hidden_picture(firecrawl, n, w=300 if n < 120 else 600, h=400) for n in range(100, 160)
    ]
    # 10 drawn pictures share the hidden pictures' box: their lines name the first 8.
    marks["imgs"] += [
        hidden_picture(firecrawl, n, vis=1, shown=True, w=150, h=150) for n in range(200, 210)
    ]
    marks["vids"] = [
        player(n, "video", f"{shop}/cdn/v/{n}.mp4", poster=f"{shop}/cdn/p/{n}.png")
        for n in range(1, 26)
    ]
    mark(firecrawl, marks)
    serve_hidden(httpserver, *range(100, 160))
    for n in range(1, 26):
        httpserver.expect_request(f"/cdn/p/{n}.png").respond_with_data(
            photo(400 + n, 300, 400), content_type="image/png"
        )
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 159]})

    say(f"Make an ad for {shampoo_page_url}")

    labels = [image["label"] for image in picker_call().images]
    assert len(labels) <= 50
    assert sum(label.startswith("Official photos") for label in labels) == 6
    assert sum(label.startswith("Video players") for label in labels) == 5
    assert labels[-1] == "Video players: V17, V18, V19, V20 (each labelled in its black bar)"
    # The 40 biggest hidden pictures, in page order: the 20 smaller ones are left out.
    hidden = [label for label in labels if label.startswith("Hidden pictures")]
    assert len(hidden) == 5
    assert hidden[0].startswith("Hidden pictures: I120 (hidden), I121 (hidden),")
    assert (
        picker_call()
        .handoff["hidden_pictures"]
        .splitlines()[0]
        .endswith("box shared with drawn pictures I200, I201, I202, I203, I204, I205, I206, I207")
    )
    assert read_page_result().endswith("Kept 2 product photos.")


def test_a_page_read_again_shows_the_picker_the_same_grids_and_pays_once(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    from adforge.retry import OutsideServiceDown

    with_hidden_gallery(firecrawl, httpserver)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    # The first read stops when the copy call fails, after the picker has answered.
    fake_model.respond("copy_page_text", *[OutsideServiceDown("503 from the provider")] * 3)
    fake_model.respond("pick_photos", {**SHAMPOO_PICKED, "gallery_images": [8, 40]})
    say(f"Make an ad for {shampoo_page_url}")
    asked = len(httpserver.log)

    reading(fake_model, shampoo_page_url)
    say("Try again")

    assert paid_for().count("pick_photos") == 1
    assert read_page_result_of_last().endswith("Kept 2 product photos.")
    # The hidden pictures were fetched for the grid once; again only to be kept.
    fetched = [request.path for request, _ in httpserver.log[asked:]]
    assert fetched.count("/cdn/hidden/41.png") == 0


def read_page_result_of_last() -> str:
    from .conftest import results_of

    return results_of("read_page")[-1]


def test_a_video_whose_frame_takes_too_long_is_listed_without_a_picture(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jobs.picker_grids.FRAME_SECONDS", 0.000_001)
    # A video of its own, so no earlier test's ffmpeg run is replayed for it.
    httpserver.expect_request(TINY_MP4_PATH).respond_with_data(
        tiny_mp4("0xcc6633"), content_type="video/mp4"
    )
    marks = marks_of(firecrawl)
    marks["vids"] = [player(1, "video", f"{firecrawl.shop}{TINY_MP4_PATH}", box=0)]
    mark(firecrawl, marks)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    say(f"Make an ad for {shampoo_page_url}")

    assert (
        picker_call().handoff["video_players"].endswith("NO PICTURE (its frame couldn't be read)")
    )
    assert "Kept 1 product photo." in read_page_result()


def test_a_grid_the_models_safety_filter_blocks_is_left_out_and_the_rest_are_picked_from(
    api: APIClient,
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    session_id: str,
    say: Callable[..., None],
) -> None:
    with_hidden_gallery(firecrawl, httpserver)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)
    hidden_grid = "Hidden pictures: I40 (hidden), I41 (hidden) (each labelled in its black bar)"

    def blocked_by_the_hidden_grid(request: ModelRequest[Any]) -> dict[str, Any]:
        if hidden_grid in [image.label for image in request.images]:
            raise BlockedBySafetyFilter("content_policy_violation")
        return {**SHAMPOO_PICKED, "gallery_images": [8], "record_images": [1]}

    fake_model.answer_unscripted("pick_photos", blocked_by_the_hidden_grid)

    say(f"Make an ad for {shampoo_page_url}")

    # Shown everything, then without the last grid, which the filter lets through.
    first, second = ModelCall.objects.filter(purpose="pick_photos").order_by("created_at")
    assert [image["label"] for image in first.images] == [
        *SCREENSHOT_PARTS,
        OFFICIAL_R1,
        hidden_grid,
    ]
    assert [image["label"] for image in second.images] == [*SCREENSHOT_PARTS, OFFICIAL_R1]
    # Nor is the picker told of the pictures it isn't shown.
    assert second.handoff["hidden_pictures"] == "(none: every numbered picture was drawn)"
    assert second.handoff["official_photos"] == first.handoff["official_photos"]
    assert (first.outcome, second.outcome) == ("failed", "succeeded")
    assert "Kept 1 product photo." in read_page_result()
    assert (
        "info",
        "The AI model's safety filter refused to look at some of the pictures beside the "
        "page's screenshot (hidden pictures I40 and I41), so this product's photos were "
        "picked without them.",
    ) in notices(api, session_id)


def test_when_every_grid_alone_is_blocked_the_photos_are_picked_off_the_screenshot_alone(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    shampoo_page_url: str,
    say: Callable[..., None],
) -> None:
    with_hidden_gallery(firecrawl, httpserver)
    reading(fake_model, shampoo_page_url)
    fake_model.respond("check_page", READABLE)

    def blocked_by_any_grid(request: ModelRequest[Any]) -> dict[str, Any]:
        if len(request.images) > len(SCREENSHOT_PARTS):
            raise BlockedBySafetyFilter("content_policy_violation")
        return {**SHAMPOO_PICKED, "gallery_images": [8]}

    fake_model.answer_unscripted("pick_photos", blocked_by_any_grid)

    say(f"Make an ad for {shampoo_page_url}")

    # Everything, without the hidden grid, without the official photos' grid, then neither.
    asked = ModelCall.objects.filter(purpose="pick_photos").order_by("created_at")
    assert [len(call.images) for call in asked] == [5, 4, 4, 3]
    assert asked.last().handoff["official_photos"] == "(none)"  # type: ignore[union-attr]
    assert "Kept 1 product photo." in read_page_result()
