"""A page-video B-roll scene made by its agent from a checked starting picture.

The owner's first link builds the real library, product-only and recoloured photo inputs.
The scene agent chooses its inputs, draws, checks and films with the page-video piece.
Every external model is scripted at the gateway; the chat and cards show the actual clip.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.library import LookFrame, PagePhoto, ProductOnly, Recoloured, library_of, sent_list_of
from jobs.models import Job

from .conftest import (
    LABELLED,
    NO_CHOICES,
    PLAN,
    READABLE,
    SHAMPOO_PICKED,
    FakeFirecrawl,
    HeldSteps,
    a_video,
    facts_ok,
    handoffs,
    paid_for,
)
from .scene_script import script_scene_agents
from .test_end_to_end import clip_calls
from .test_picker_labels import label, labelled_page  # noqa: F401 (a fixture)
from .test_product_videos import picks_videos, player  # noqa: F401 (a fixture)
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = [pytest.mark.django_db(transaction=True)]

# The shampoo's page photos as the picker labels them: its front, back and left side, its cap
# off (a state) and a hand using it (in use). The back stands beside other things, so it gets
# a product-only version. All are amber.
PAGE_PHOTOS = [
    label("I8", 1, "side", "front", "The amber bottle from the front, cap on."),
    label("I101", 2, "side", "back", "The amber bottle from the back, beside a towel.", alone="no"),
    label("I102", 3, "side", "side", "The amber bottle from its left side."),
    label("I103", 4, "state", "front", "The amber bottle with its cap off, the pump showing."),
    label(
        "I104", 5, "in-use", "none", "A hand pressing the pump, the shampoo coming out.", alone="no"
    ),
]
# The back's product-only version, labelled.
BACK_ALONE = {**LABELLED, "side": "back", "colour": "amber", "label": "The amber bottle, back."}
PICKED = {**SHAMPOO_PICKED, "gallery_images": [8, 101, 102, 103, 104], "pictures": PAGE_PHOTOS}

# What the watcher sees in a page video: the pump pressed from 1 s to 4 s, and the bottle black.
PRESSED = "A hand presses the pump and shampoo comes out."


def watched(colour: str, happens: str) -> dict[str, Any]:
    return {
        "shows_this_product": True,
        "moments": [
            {"start": 1.0, "end": 4.0, "happens": happens, "on_screen": f"A hand. {happens}"}
        ],
        "look_frames": [
            {
                "second": 0.5,
                "kind": "state",
                "side": "front",
                "alone": "yes",
                "colour": colour,
                "target": "",
                "label": f"The {colour} bottle from the front, in the video.",
            }
        ],
        "colour": colour,
        "transcript": "",
    }


# Scene 2 has ten words: four seconds at the fixed pace, plus one for the final action.
LINE = "One pump lathers your whole head of hair at home."
SHOWS = "a hand presses the pump and shampoo comes out into the palm"


def a_plan(in_use: int, video: int) -> dict[str, Any]:
    """The plan: scene 2 shows the pump pressed, picks library item `in_use` for how the
    product is used, and is cut from moment 1 of video `video`; the presenter is seen."""
    return {
        **PLAN,
        "plan": {
            **PLAN["plan"],
            "product_name": "Detox Shampoo",
            "product_colour": "amber",
            "colour_photos": [1],
            "scenes": [
                {
                    **PLAN["plan"]["scenes"][0],
                    "line": "Meet the Detox Shampoo, made for your daily routine at home.",
                },
                {
                    "line": LINE,
                    "shows": SHOWS,
                    "broll_kind": "does a job",
                    "person_shown": "no face",
                    "kind": "broll",
                    "kind_why": "The pump needs a close view inside the palm.",
                    "proof": "Shampoo lands in the palm as the pump is pressed.",
                    "usage": "pressing the pump into the palm",
                    "result": "shampoo in the palm",
                    "needs": [],
                    "scene_pictures": [{"number": in_use, "for": "in-use"}],
                    "piece": {"video": video, "moment": 1},
                },
                {
                    **PLAN["plan"]["scenes"][2],
                    "line": "Get yours today and enjoy the Detox Shampoo at home.",
                },
            ],
        },
    }


def every_scene(tool: str, **arguments: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(tool, {"scene": scene, **arguments}) for scene in (1, 2, 3)]


@pytest.fixture
def page_with_a_video(
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    labelled_page: str,  # noqa: F811 (the fixture)
    picks_videos: list[dict[str, Any]],  # noqa: F811 (the fixture)
) -> str:
    """The shampoo's page with PAGE_PHOTOS and one 6-second video. Gives the page's link."""
    httpserver.expect_request("/cdn/videos/press.mp4").respond_with_data(
        a_video(64, 96, sound=False, seconds=6), content_type="video/mp4"
    )
    picks_videos.append(player(1, f"{firecrawl.shop}/cdn/videos/press.mp4"))
    return labelled_page


def make_the_ad(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    link: str,
    plan: Callable[[Job], dict[str, Any]],
    picked: dict[str, Any] = PICKED,
    *,
    photo_marks: tuple[str, ...] = ("I8", "I104", "I101", "I102"),
) -> None:
    """The shop owner's chat from the link to the finished ad: the page read and watched, its
    photos picked as `picked` says, the plan `plan` gives for the job as read, then every
    scene made and the ad put together."""

    def chatting(message: str, *answer: Turn) -> None:
        fake_model.respond("produce", *answer)
        say(message)
        if steps.held:
            fake_model.respond("produce", turn(says="That part's ready."))
        steps.run_held()

    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", picked)
    # The back's product-only version, as the labelling call labels it.
    fake_model.answer_unscripted("label_photo", lambda _: BACK_ALONE)
    chatting(
        f"Make an ad for {link}",
        turn(calls=[("read_page", {"link": link, "target_seconds": None})]),
        turn(says="I've read your page."),
    )
    fake_model.respond("plan_ad", plan(Job.objects.get()))
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    chatting(
        "Plan it",
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's your script. Shall I make it?"),
    )
    chatting(
        "Go ahead",
        turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
        turn(says="On its way."),
    )
    scene = Job.objects.get().scenes.get(number=2)
    assert scene.piece is not None
    picks = [
        {"item": item_of(scene.job, mark), "take": "Copy this view of the product."}
        for mark in photo_marks
    ]
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    script_scene_agents(fake_model)
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[("look_at_library", {"item": None, "video": scene.piece["video"], "moment": 1})]
        ),
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})]),
        turn(
            calls=[
                (
                    "film",
                    {"form": {**FILM["form"], "video_for": "Copy the pump pressing movement"}},
                )
            ]
        ),
    )
    script_scene_agents(fake_model)
    chatting("Keep going", turn(calls=clip_calls((2,))), turn(says="Making clips."))
    chatting("Put it together", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!"))


def item_of(job: Job, mark: str) -> int:
    """The library number of the page photo the picker marked `mark`."""
    position = PICKED["gallery_images"].index(int(mark.removeprefix("I"))) + 1
    return next(
        item.number
        for item in library_of(job)
        if isinstance(item.source, PagePhoto) and item.source.photo == position
    )


@pytest.fixture
def ad_from_its_sent_lists(
    fake_model: FakeModel,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,
) -> None:
    fake_model.answer_unscripted("watch_video", lambda _: watched("black", PRESSED))
    make_the_ad(
        fake_model, say, steps, page_with_a_video, lambda job: a_plan(item_of(job, "I104"), 1)
    )


def test_page_video_clip_sends_its_starting_picture_and_piece_with_product_photos_only_to_draw(
    fake_model: FakeModel,
    ad_from_its_sent_lists: None,
) -> None:
    job = Job.objects.get()
    clip = job.produced.get(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None and sent.starting_picture is not None
    assert sent.mode == "reference" and sent.pictures == []
    picture = clip.picture
    assert picture is not None and picture.sent is not None
    selected = [p for p in picture.sent["photos"] if p.get("library_number")]
    assert [p["library_number"] for p in selected] == [
        item_of(job, m) for m in ("I8", "I104", "I101", "I102")
    ]
    library = {item.number: item for item in library_of(job)}
    paints = [item for item in library.values() if isinstance(item.source, Recoloured)]
    assert len(paints) == 4
    assert all(p.colour == "black" for p in paints)
    back = item_of(job, "I101")
    assert any(
        isinstance(item.source, ProductOnly) and item.source.of == back for item in library.values()
    )
    assert all(p["file"] in {paint.file for paint in paints} for p in selected)
    asked = fake_model.broll_clips_asked[1]
    assert asked["starting_picture"] is None
    assert asked["example_pictures"] == [file_store.read(picture.file)]
    (piece,) = asked["reference_videos"]
    assert (piece["start"], piece["end"], piece["trim"]) == (1.0, 4.0, None)
    assert [(p.video, p.moment, p.start, p.end) for p in sent.pieces] == [(1, 1, 1.0, 4.0)]
    assert asked["prompt"] == sent.prompt
    assert "The clip starts exactly like Image 1." in sent.prompt
    assert "Copy the pump pressing movement" in sent.prompt
    assert asked["seconds"] == 5 and handoffs("make_broll_clip")[1]["prompt_enhancement"] is False


def test_the_judge_sees_the_starting_picture_then_drawing_sources_and_the_card_shows_it(
    ad_from_its_sent_lists: None, api: APIClient, session_id: str
) -> None:
    job = Job.objects.get()
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    assert sent.starting_picture is not None
    judged = handoffs("check_clip_references") + handoffs("check_clip_product")
    assert len([ask for ask in judged if ask["prompt_sent_to_the_video_api"] == sent.prompt]) == 2
    assert clip.ruling is not None
    assert [item["key"] for item in clip.ruling["pictures"]["references"]][:5] == [
        sent.starting_picture.file,
        *[p.file for p in sent.picture_photos],
    ]
    # The scene's card: its clip in the chat carries what it was sent.
    conversation = api.get(f"/api/sessions/{session_id}/").json()["messages"]
    (shown,) = [
        attachment["sent"]
        for message in conversation
        for attachment in message["attachments"]
        if attachment["kind"] == "video" and attachment["scene"] == 2
    ]
    assert shown["pictures"] == []
    assert shown["starting_picture"]["file"] == sent.starting_picture.file
    assert [(p["video"], p["start"], p["end"]) for p in shown["pieces"]] == [(1, 1.0, 4.0)]


def test_broll_starting_picture_is_drawn_and_checked_before_filming(
    ad_from_its_sent_lists: None,
) -> None:
    job = Job.objects.get()
    # Every scene gets its own starting picture.
    assert sorted(
        job.produced.filter(kind="starting_picture").values_list("scene__number", flat=True)
    ) == [1, 2, 3]
    paid = paid_for()
    assert "check_starting_picture" not in paid
    # The library cut-out and scene starting picture are checked; paints are not.
    assert sorted(
        purpose
        for purpose in paid
        if purpose.startswith("check_")
        and purpose not in ("check_page", "check_plan_quality")
        and "clip" not in purpose
    ) == ["check_cut_out", "check_scene_picture", "check_scene_picture", "check_scene_picture"]
    # The ad is finished without a question about pictures (US 18).
    assert job.produced.filter(kind="finished_ad").exists()


def test_two_videos_in_different_players_are_both_watched_and_the_plan_can_cut_from_either(
    fake_model: FakeModel,
    httpserver: HTTPServer,
    firecrawl: FakeFirecrawl,
    say: Callable[..., None],
    steps: HeldSteps,
    page_with_a_video: str,
    picks_videos: list[dict[str, Any]],  # noqa: F811 (the fixture)
) -> None:
    # A second video, in a player of its own, with sound and controls: the pump in amber.
    httpserver.expect_request("/cdn/videos/how-to.mp4").respond_with_data(
        a_video(96, 64, sound=True, seconds=5), content_type="video/mp4"
    )
    picks_videos.append(
        {**player(2, f"{firecrawl.shop}/cdn/videos/how-to.mp4"), "box": 7, "controls": True}
    )
    twisted = "A hand twists the cap off."

    def watching(request: Any) -> dict[str, Any]:
        seconds = request.handoff.model_dump()["seconds"]
        return watched("black", PRESSED) if seconds > 5.5 else watched("amber", twisted)

    fake_model.answer_unscripted("watch_video", watching)
    # The plan cuts scene 2 from the second video.
    make_the_ad(
        fake_model, say, steps, page_with_a_video, lambda job: a_plan(item_of(job, "I104"), 2)
    )

    job = Job.objects.get()
    assert paid_for().count("watch_video") == 2
    frames = [item.source for item in library_of(job) if isinstance(item.source, LookFrame)]
    assert sorted(frame.video for frame in frames) == [1, 2]
    # The planner was told both videos' moments.
    (planned,) = handoffs("plan_ad")
    assert [line.split(",")[0] for line in planned["moments"].splitlines() if line] == [
        "Video 1",
        "Video 2",
    ]
    # The clip is sent the second video's piece, in its own colour: the photos are amber
    # already, so nothing is recoloured.
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    assert [(p.video, p.moment) for p in sent.pieces] == [(2, 1)]
    assert sent.colour == "amber"
    assert sent.recolours == []
    asked = fake_model.broll_clips_asked[1]
    (piece,) = asked["reference_videos"]
    assert (piece["start"], piece["end"]) == (1.0, 4.0)
