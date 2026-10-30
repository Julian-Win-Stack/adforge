"""A B-roll clip made from its sent list, never from a starting picture (spec #210 points
13-19, US 1-7, 18, 38): the spec's first flow, from the shop owner's first message to the
finished ad. A page with photos of three sides, one state, one in-use photo and a video; the
library built with the labels, the product-only version and the video's moments; the plan
names a moment and a scene picture; cut-and-send builds the clip's sent list, the first-send
writer its prompt, the clip judge is shown the same list, and the scene card shows it.

Every model is faked at the gateway, the shop is served on this machine through the stand-in
Firecrawl, the fake video API is read for exactly what it was sent, and ffmpeg runs for real
on tiny files."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient

from adforge import file_store
from gateway.fake import FakeModel, turn
from gateway.types import Turn
from jobs.library import LookFrame, PagePhoto, ProductOnly, library_of, sent_list_of
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
from .test_end_to_end import TALKING
from .test_picker_labels import label, labelled_page  # noqa: F401 (a fixture)
from .test_product_videos import picks_videos, player  # noqa: F401 (a fixture)
from .test_recolour import RECOLOUR_OK

pytestmark = pytest.mark.django_db(transaction=True)

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


# Scene 2's 8 words take the fake voice 4 seconds: its clip is asked for 5.
LINE = "One pump lathers your whole head of hair."
SHOWS = "a hand presses the pump and shampoo comes out into the palm"
SENTENCE = "A hand presses the pump in the bathroom and shampoo pools in the palm."
WRITTEN_LABELS = [
    "the black bottle from the front, cap on",
    "a hand pressing the pump, for how it is held",
    "the black bottle from the back",
    "the black bottle from its left side",
]


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
                {"line": "Meet the Detox Shampoo."},
                {
                    "line": LINE,
                    "shows": SHOWS,
                    "broll_kind": "does a job",
                    "person_shown": "has face",
                    "usage": "pressing the pump into the palm",
                    "result": "shampoo in the palm",
                    "needs": [],
                    "scene_pictures": [{"number": in_use, "for": "in-use"}],
                    "piece": {"video": video, "moment": 1},
                },
                {"line": "Get yours today."},
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
) -> None:
    """The shop owner's chat from the link to the finished ad: the page read and watched, the
    plan `plan` gives for the job as read, then every scene made and the ad put together."""

    def chatting(message: str, *answer: Turn) -> None:
        fake_model.respond("produce", *answer)
        say(message)
        if steps.held:
            fake_model.respond("produce", turn(says="That part's ready."))
        steps.run_held()

    fake_model.respond("check_page", READABLE)
    fake_model.respond("pick_photos", PICKED)
    # The back's product-only version, as the labelling call labels it.
    fake_model.answer_unscripted("label_photo", lambda _: BACK_ALONE)
    # The talking scenes' starting pictures.
    fake_model.respond("choose_starting_picture", TALKING, TALKING)
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
        turn(
            calls=every_scene("make_starting_picture", note=None) + every_scene("make_line_audio")
        ),
        turn(says="On its way."),
    )
    chatting("Keep going", turn(calls=every_scene("transcribe_line_audio")), turn(says="Done."))
    chatting("Keep going", turn(calls=every_scene("make_clip")), turn(says="Making clips."))
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
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
    fake_model.respond("write_first_send_prompt", {"labels": WRITTEN_LABELS, "sentence": SENTENCE})
    make_the_ad(
        fake_model, say, steps, page_with_a_video, lambda job: a_plan(item_of(job, "I104"), 1)
    )


def test_the_clip_is_sent_the_front_its_scene_picture_the_presenter_the_other_sides_and_a_piece(
    fake_model: FakeModel, ad_from_its_sent_lists: None
) -> None:
    job = Job.objects.get()
    library = {item.number: item for item in library_of(job)}
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    # The video is black, so every library picture sent is its black recolour, made from the
    # video's look frame; the back goes as its product-only version.
    front, in_use, back, side = (item_of(job, mark) for mark in ("I8", "I104", "I101", "I102"))
    (back_alone,) = (
        item.number
        for item in library.values()
        if isinstance(item.source, ProductOnly) and item.source.of == back
    )
    (frame,) = (number for number, item in library.items() if isinstance(item.source, LookFrame))
    assert [(r.of, r.colour, r.outcome) for r in sent.recolours] == [
        (front, "black", "done"),
        (in_use, "black", "done"),
        (back_alone, "black", "done"),
        (side, "black", "done"),
    ]
    made = {r.of: r.made for r in sent.recolours}
    person = job.produced.get(kind="person_in_room")
    assert [(p.image, p.tier, p.library_number, p.drawn, p.for_) for p in sent.pictures] == [
        (1, 1, made[front], None, None),
        (2, 2, made[in_use], None, "in-use"),
        (3, 3, None, "person_in_room", None),
        (4, 4, made[back_alone], None, None),
        (5, 4, made[side], None, None),
    ]
    assert all(library[r.made].source.colour == "black" for r in sent.recolours if r.made)  # type: ignore[union-attr]
    # The state photo isn't sent, nor is anything left out.
    assert item_of(job, "I103") not in {p.library_number for p in sent.pictures}
    assert sent.left_out == []
    # The fake video API got exactly those pictures, then the piece cut inside the moment's
    # seconds, and the prompt: the speed line, one label per item and one sentence.
    (asked,) = fake_model.broll_clips_asked
    assert asked["starting_picture"] is None
    assert asked["example_pictures"] == [file_store.read(p.file) for p in sent.pictures]
    assert asked["example_pictures"][2] == file_store.read(person.file)
    (piece,) = asked["reference_videos"]
    assert (piece["start"], piece["end"], piece["trim"]) == (1.0, 4.0, None)
    assert [(p.video, p.moment, p.start, p.end) for p in sent.pieces] == [(1, 1, 1.0, 4.0)]
    assert asked["prompt"] == sent.prompt
    assert asked["prompt"].splitlines() == [
        "Film this in real-time speed.",
        "Image 1 is the black bottle from the front, cap on.",
        "Image 2 is a hand pressing the pump, for how it is held.",
        "Image 3 is the woman and the room: she is the person in the clip and it is the "
        "background.",
        "Image 4 is the black bottle from the back.",
        "Image 5 is the black bottle from its left side.",
        "Video 1 is only for the movement, not the look.",
        SENTENCE,
    ]
    assert asked["seconds"] == 5


def test_the_judge_is_shown_the_same_list_and_the_scene_card_shows_it(
    ad_from_its_sent_lists: None, api: APIClient, session_id: str
) -> None:
    job = Job.objects.get()
    (clip,) = job.produced.filter(kind="clip", scene__number=2)
    sent = sent_list_of(clip)
    assert sent is not None
    # Every ask of the judge was handed the prompt sent and a line per picture and piece.
    for ask in handoffs("check_clip_references") + handoffs("check_clip_product"):
        assert ask["prompt_sent_to_the_video_api"] == sent.prompt
        assert [line.split(":")[0] for line in ask["references"]] == [
            *(f"Image {n}" for n in range(1, 6)),
            "Video 1",
        ]
    assert clip.ruling is not None
    assert [item["key"] for item in clip.ruling["pictures"]["references"]][:5] == [
        p.file for p in sent.pictures
    ]
    # The scene's card: its clip in the chat carries what it was sent.
    conversation = api.get(f"/api/sessions/{session_id}/").json()["messages"]
    (shown,) = [
        attachment["sent"]
        for message in conversation
        for attachment in message["attachments"]
        if attachment["kind"] == "video" and attachment["scene"] == 2
    ]
    assert [p["label"] for p in shown["pictures"]] == [p.label for p in sent.pictures]
    assert [(p["video"], p["start"], p["end"]) for p in shown["pieces"]] == [(1, 1.0, 4.0)]


def test_no_starting_picture_is_drawn_or_checked_for_the_b_roll_scene(
    ad_from_its_sent_lists: None,
) -> None:
    job = Job.objects.get()
    # The talking scenes' starting pictures only.
    assert sorted(
        job.produced.filter(kind="starting_picture").values_list("scene__number", flat=True)
    ) == [1, 3]
    paid = paid_for()
    assert "check_starting_picture" not in paid
    # The only pictures checked are the library's cut-out and its recolours.
    assert sorted(
        purpose
        for purpose in paid
        if purpose.startswith("check_") and purpose not in ("check_page",) and "clip" not in purpose
    ) == [
        "check_cut_out",
        *["check_recolour"] * 4,
    ]
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
    fake_model.answer_unscripted("check_recolour", lambda _: RECOLOUR_OK)
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
    (asked,) = fake_model.broll_clips_asked
    (piece,) = asked["reference_videos"]
    assert (piece["start"], piece["end"]) == (1.0, 4.0)
