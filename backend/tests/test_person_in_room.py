"""The ad's person-in-room picture and its room alone, driven through the chat from the shop
owner's first message: once the ad is planned, one picture of the presenter standing straight
and still in the drawn room, nothing else happening, and one of the room alone, with nothing
of the scenes in it, for the scenes nobody is seen in (spec #210 point 12; spec 2 item 53).
They are kept and shown like other produced pictures; nothing about the starting pictures
changes. The producer's model and every outside service are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
from gateway.gateway import edit_picture
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs.models import Job, SceneStep
from jobs.work import person_and_room

from .conftest import (
    NO_CHOICES,
    PLAN,
    READABLE,
    HeldSteps,
    broll,
    facts_ok,
    handoffs,
    paid_for,
)
from .test_end_to_end import SHOWING, TALKING, TURNED, chat_and_run, every_scene

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# Scene 2 shows the presenter's face; scene 3 shows only a hand.
PLANNED: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            {"line": "Meet the Stoneware Mug.", "overlay": "Kiln & Co"},
            broll(
                {
                    "line": "Hand-thrown, holds 350 ml, and dishwasher safe too.",
                    "shows": "the presenter smiles as she lifts the mug to her lips",
                    "person_shown": "has face",
                }
            ),
            broll({"line": "Every mug is hand-thrown, so each is its own.", "shows": TURNED}),
            {"line": "Yours today for just $24.00, from Kiln & Co.", "overlay": "$24.00"},
        ],
    },
}


def paid_file(purpose: str) -> str:
    """The picture the one call for `purpose` drew."""
    output = ModelCall.objects.get(purpose=purpose).output
    assert output is not None
    file: str = output["file"]
    return file


@pytest.fixture
def room_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The picture model's safety filter refuses to draw the room alone."""

    def editing(*, purpose: str, **asked: Any) -> str:
        if purpose == "make_room_alone":
            raise BlockedBySafetyFilter("refused")
        return edit_picture(purpose=purpose, **asked)

    monkeypatch.setattr("jobs.work.edit_picture", editing)


@pytest.fixture
def pictures_made(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], steps: HeldSteps
) -> None:
    """A chat that took the mug's ad, with a B-roll scene the presenter is seen in and one
    nobody is, from the shop owner's first message to every scene's starting picture."""

    def chatting(message: str, *answer: Any) -> None:
        chat_and_run(fake_model, say, steps, message, *answer)

    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLANNED)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))
    chatting(
        f"Make a 15 second ad for {product_page_url}",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 15})]),
        turn(calls=[("plan_ad", {})]),
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's your script. Shall I make it?"),
    )
    fake_model.respond("choose_starting_picture", TALKING, TALKING)
    fake_model.respond("choose_broll_picture", SHOWING, SHOWING)
    chatting(
        "Looks good, go ahead",
        turn(calls=every_scene("make_starting_picture", scenes=4, note=None)),
        turn(says="Every scene's picture is on its way."),
    )
    assert list(SceneStep.objects.values_list("status", "reason")) == [("finished", "")] * 4


def test_the_ad_gets_one_person_in_room_picture_and_one_room_alone(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    assert job.produced.filter(kind="person_in_room").count() == 1
    assert job.produced.filter(kind="room_alone").count() == 1
    # Each drawn once for the ad, though two B-roll scenes were made.
    assert [p for p in paid_for() if p in ("make_room_alone", "make_person_in_room")] == [
        "make_room_alone",
        "make_person_in_room",
    ]


def test_the_room_alone_is_drawn_from_the_drawn_room_with_only_the_room_in_it(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_room_alone")
    assert asked["pictures"] == [job.produced.get(kind="before_picture").file]
    assert (
        "Keep the room itself as it is: its walls, floor, surfaces and fittings. Take out every "
        "other thing, and every mark, stain or mess on what stays, so nothing of any scene is "
        "left."
    ) in asked["prompt"]
    room = job.produced.get(kind="room_alone")
    assert room.file == paid_file("make_room_alone")


def test_the_person_in_room_is_drawn_from_the_portrait_and_the_room_standing_still(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_person_in_room")
    # The portrait, so the presenter is the same person, then the room she stands in.
    assert asked["pictures"] == [
        job.produced.get(kind="portrait").file,
        job.produced.get(kind="room_alone").file,
    ]
    assert (
        "The person stands straight and still in the room, hands at their sides, and nothing "
        "else happens."
    ) in asked["prompt"]
    person = job.produced.get(kind="person_in_room")
    assert person.file == paid_file("make_person_in_room")


def test_both_pictures_are_shown_in_the_chat_once(pictures_made: None) -> None:
    job = Job.objects.get()
    for kind in ("person_in_room", "room_alone"):
        shown = Attachment.objects.filter(file=job.produced.get(kind=kind).file)
        assert [(each.kind, each.scene) for each in shown] == [("picture", None)]


def test_the_starting_pictures_are_drawn_as_before(pictures_made: None) -> None:
    job = Job.objects.get()
    new = {job.produced.get(kind=kind).file for kind in ("person_in_room", "room_alone")}
    sent = [picture for asked in handoffs("make_starting_picture") for picture in asked["pictures"]]
    assert set(sent) & new == set()
    # Each B-roll scene's picture still ends with the before-picture.
    before = job.produced.get(kind="before_picture").file
    broll = [asked for asked in handoffs("make_starting_picture") if before in asked["pictures"]]
    assert [asked["pictures"][-1] for asked in broll] == [before, before]


def test_pictures_drawn_before_the_worker_stopped_are_not_paid_for_again(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    drawn = {kind: job.produced.get(kind=kind).file for kind in ("room_alone", "person_in_room")}
    # As when the worker stopped once each was paid for, before it was kept.
    for kind in ("person_in_room", "room_alone"):
        job.produced.filter(kind=kind).delete()
    step = SceneStep.objects.filter(scene__number=2, kind="starting_picture").get()

    person, room = person_and_room(step)

    assert {"room_alone": room.file, "person_in_room": person.file} == drawn
    assert [p for p in paid_for() if p in ("make_room_alone", "make_person_in_room")] == [
        "make_room_alone",
        "make_person_in_room",
    ]
    # Shown once, as before the worker stopped.
    assert Attachment.objects.filter(file__in=drawn.values()).count() == 2


def test_each_picture_is_kept_with_what_it_was_drawn_from(pictures_made: None) -> None:
    job = Job.objects.get()
    room = job.produced.get(kind="room_alone")
    assert room.made_from == job.produced.get(kind="before_picture")
    assert job.produced.get(kind="person_in_room").made_from == room


def test_a_room_alone_that_cant_be_drawn_leaves_every_starting_picture_made_and_says_so(
    room_refused: None, pictures_made: None
) -> None:
    # The fixture checked that every scene's step finished, its picture kept and shown.
    job = Job.objects.get()
    assert job.produced.filter(kind="starting_picture").count() == 4
    assert not job.produced.filter(kind__in=["room_alone", "person_in_room"]).exists()
    # Said in red, and kept with the job's warnings, each time it was tried: once per B-roll
    # scene's picture.
    said = "The picture of the ad's place on its own couldn't be drawn"
    told = [
        text
        for text in Message.objects.filter(role="notice", level="problem").values_list(
            "text", flat=True
        )
        if said in text
    ]
    assert len(told) == 2
    assert [w["text"] for w in job.warnings if said in w["text"]] == told
