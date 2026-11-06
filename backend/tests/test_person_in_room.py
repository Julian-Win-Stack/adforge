"""The ad's person-in-room picture and its room alone, driven through the chat from the shop
owner's first message: once the ad is planned, one picture of the presenter relaxed in the
before-picture's room, nothing else happening, drawn in one go from her portrait and words
(spec #401), and one of the room alone taken out of it, with nothing of the scenes in it, for
shots whose face stays out of frame (spec #210 point 12; spec 2 item 53).
They are kept and shown like other produced pictures, and drawn when the first scene clip
is made, before its scene agent draws a starting picture. The
producer's model and every outside service are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from agents.models import ToolCall
from chat.models import Attachment, Message
from gateway.fake import FakeModel, turn
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
    recorded,
)
from .scene_script import script_scene_agents
from .test_end_to_end import TURNED, chat_and_run

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

# Scene 2 shows the presenter's face; scene 3 shows only a hand.
PLANNED: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            {**PLAN["plan"]["scenes"][0]},
            broll(
                {
                    "line": "Hand-thrown, holds 350 ml, and dishwasher safe for everyday drinks.",
                    "kind": "talking",
                    "shows": "the presenter smiles as she lifts the mug to her lips",
                    "person_shown": "has face",
                }
            ),
            broll({"line": "Every mug is hand-thrown, so each one is its own.", "shows": TURNED}),
            {**PLAN["plan"]["scenes"][2]},
        ],
    },
}


def agent_calls(quote: str = "Keep going") -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "start_scene_agent",
            {"scene": n, "instruction": "Film the planned scene.", "quotes": [quote]},
        )
        for n in range(1, 5)
    ]


def paid_file(purpose: str) -> str:
    """The picture the one call for `purpose` drew."""
    output = ModelCall.objects.get(purpose=purpose).output
    assert output is not None
    file: str = output["file"]
    return file


@pytest.fixture
def room_refused(fake_model: FakeModel) -> None:
    """The external picture model refuses each scene's shared-room draw."""
    fake_model.respond("make_room_alone", *[BlockedBySafetyFilter("refused")] * 4)


def clips_made(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    clips: list[tuple[str, dict[str, Any]]],
    owner_note: str = "Keep going",
    *,
    with_person: bool = True,
) -> None:
    """A chat that took the mug's ad, with a talking scene and a close-up where
    her face stays out of frame, from the shop owner's first message to every scene's
    clip, made by the producer's `clips` calls."""

    def chatting(message: str, *answer: Any) -> None:
        chat_and_run(fake_model, say, steps, message, *answer)

    fake_model.respond("check_page", READABLE)
    fake_model.respond("plan_ad", PLANNED)
    fake_model.respond("fact_check", facts_ok(1, 2, 3, 4))
    chatting(
        f"Make a 30 second ad for {product_page_url}",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": 30})]),
        turn(calls=[("plan_ad", {})]),
        *([turn(calls=[("create_person", {})])] if with_person else []),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="Here's your script. Shall I make it?"),
    )
    script_scene_agents(fake_model, scenes=4)
    chatting(
        owner_note,
        turn(calls=clips),
        turn(says="Making the clips."),
    )


@pytest.fixture
def pictures_made(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], steps: HeldSteps
) -> None:
    """A chat that took the mug's ad, with a talking scene and a close-up where
    her face stays out of frame, from the shop owner's first message to every scene's clip."""
    clips_made(fake_model, product_page_url, say, steps, agent_calls())


def test_the_ad_gets_one_person_in_room_picture_and_one_room_alone(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    assert job.produced.filter(kind="person_in_room").count() == 1
    assert job.produced.filter(kind="room_alone").count() == 1
    # Each drawn once for the ad, though all four scenes were made: her in the room first.
    assert [p for p in paid_for() if p in ("make_room_alone", "make_person_in_room")] == [
        "make_person_in_room",
        "make_room_alone",
    ]


def test_the_person_in_room_is_drawn_in_one_go_from_the_portrait_and_words(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_person_in_room")
    # Only the portrait, as her face: no finished room picture is merged with it.
    assert asked["pictures"] == [job.produced.get(kind="portrait").file]
    prompt = asked["prompt"]
    assert "Take only their face and hair from the picture" in prompt
    # The room from the before-picture's words, her and her clothes from the plan's.
    assert job.produced.get(kind="before_picture").text in prompt
    assert job.person_looks in prompt
    for realism in (
        "lived-in room",
        "lit only by its own lights",
        "no ring light",
        "no studio light",
        "no beauty filter",
        "no background blur",
        "relaxed, natural pose",
    ):
        assert realism in prompt
    assert "hands at their sides" not in prompt
    assert "straight and still" not in prompt
    person = job.produced.get(kind="person_in_room")
    assert person.file == paid_file("make_person_in_room")


def test_the_room_alone_is_her_picture_with_her_taken_out_and_only_the_room_in_it(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_room_alone")
    assert asked["pictures"] == [job.produced.get(kind="person_in_room").file]
    assert "with the person taken out" in asked["prompt"]
    assert (
        "Keep the room itself as it is: its walls, floor, surfaces and fittings. Take out every "
        "other thing, and every mark, stain or mess on what stays, so nothing of any scene is "
        "left."
    ) in asked["prompt"]
    room = job.produced.get(kind="room_alone")
    assert room.file == paid_file("make_room_alone")


def test_no_picture_call_takes_a_portrait_and_a_room_together(pictures_made: None) -> None:
    job = Job.objects.get()
    portrait = job.produced.get(kind="portrait").file
    rooms = set(
        job.produced.filter(kind__in=["room_alone", "before_picture"]).values_list(
            "file", flat=True
        )
    )
    for call in ModelCall.objects.exclude(handoff__pictures=None):
        pictures = set(call.handoff.get("pictures") or [])
        assert not (portrait in pictures and pictures & rooms), call.purpose


def test_both_pictures_are_shown_in_the_chat_once(pictures_made: None) -> None:
    job = Job.objects.get()
    for kind in ("person_in_room", "room_alone"):
        shown = Attachment.objects.filter(file=job.produced.get(kind=kind).file)
        assert [(each.kind, each.scene) for each in shown] == [("picture", None)]


def test_each_scene_picture_is_drawn_from_its_own_shared_place_then_sent_alone(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    person, room = (job.produced.get(kind=k) for k in ("person_in_room", "room_alone"))
    for scene_number, shared in ((2, person), (3, room)):
        clip = job.produced.get(kind="clip", scene__number=scene_number)
        assert clip.picture is not None and clip.picture.sent is not None
        assert clip.picture.sent["photos"][-1]["file"] == shared.file
        assert clip.sent is not None and clip.sent["pictures"] == []
        assert clip.sent["starting_picture"]["file"] == clip.picture.file
    assert job.produced.filter(kind="starting_picture", scene__number__in=[2, 3]).count() == 2


def test_pictures_drawn_before_the_worker_stopped_are_not_paid_for_again(
    pictures_made: None,
) -> None:
    job = Job.objects.get()
    drawn = {kind: job.produced.get(kind=kind).file for kind in ("room_alone", "person_in_room")}
    # As when the worker stopped once each was paid for, before it was kept.
    for kind in ("room_alone", "person_in_room"):
        job.produced.filter(kind=kind).delete()
    step = SceneStep.objects.filter(scene__number=2, kind="clip").get()

    person, room = person_and_room(step, step.person_shown)
    assert person is not None

    assert {"room_alone": room.file, "person_in_room": person.file} == drawn
    assert [p for p in paid_for() if p in ("make_room_alone", "make_person_in_room")] == [
        "make_person_in_room",
        "make_room_alone",
    ]
    # Shown once, as before the worker stopped.
    assert Attachment.objects.filter(file__in=drawn.values()).count() == 2


def test_each_picture_is_kept_with_what_it_was_drawn_from(pictures_made: None) -> None:
    job = Job.objects.get()
    person = job.produced.get(kind="person_in_room")
    assert person.made_from == job.produced.get(kind="before_picture")
    assert job.produced.get(kind="room_alone").made_from == person


def test_a_shared_room_that_cannot_be_drawn_fails_only_the_scene_agents_and_records_it(
    room_refused: None,
    pictures_made: None,
    api: APIClient,
    session_id: str,
) -> None:
    job = Job.objects.get()
    assert not job.produced.filter(kind="clip").exists()
    assert not job.produced.filter(kind="room_alone").exists()
    assert SceneStep.objects.filter(kind="scene_agent", status="failed").count() == 4
    said = "The picture of the ad's place on its own couldn't be drawn"
    told = [(level, text) for level, text in recorded(api, session_id) if said in text]
    assert [level for level, _text in told] == ["problem"] * 4
    assert not Message.objects.filter(text__contains=said).exists()


# The owner's instruction reaches scene 3 before its first picture is drawn.
NOTE = "Turn the mug slowly so its glaze catches the light."
NOTED_CLIPS = [
    ("start_scene_agent", {"scene": 3, "instruction": NOTE, "quotes": [NOTE]}),
    *[call for call in agent_calls(quote=NOTE) if call[1]["scene"] != 3],
]


def test_a_first_clip_note_is_read_with_the_shared_room_before_drawing(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
) -> None:
    clips_made(fake_model, product_page_url, say, steps, NOTED_CLIPS, owner_note=NOTE)
    job = Job.objects.get()
    room = job.produced.get(kind="room_alone")
    request = next(
        r
        for r in fake_model.turns_taken
        if r.purpose == "scene_agent" and NOTE in str(r.handoff.conversation)
    )
    assert NOTE in str(request.handoff.conversation)
    assert room.file in [image.key for image in request.images]
    clip = job.produced.get(kind="clip", scene__number=3)
    assert clip.picture is not None and clip.picture.sent is not None
    assert clip.picture.sent["photos"][-1]["file"] == room.file
    assert paid_for().count("make_room_alone") == 1
    assert "rewrite_clip_prompt" not in paid_for()


def test_a_shared_room_failure_for_a_first_note_is_attempted_once_per_scene(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    room_refused: None,
    api: APIClient,
    session_id: str,
) -> None:
    clips_made(fake_model, product_page_url, say, steps, NOTED_CLIPS, owner_note=NOTE)
    job = Job.objects.get()
    assert not job.produced.filter(kind="clip", scene__number__in=[2, 3]).exists()
    assert paid_for().count("make_room_alone") == 4
    said = "The picture of the ad's place on its own couldn't be drawn"
    assert len([text for _level, text in recorded(api, session_id) if said in text]) == 4


def test_an_ad_with_no_presenter_draws_its_room_alone_from_the_before_picture(
    fake_model: FakeModel, product_page_url: str, say: Callable[..., None], steps: HeldSteps
) -> None:
    # The chat starts no scene agent before the presenter is made, so the step that needs
    # the room is made here as one would be.
    clips_made(fake_model, product_page_url, say, steps, [], with_person=False)
    job = Job.objects.get()
    step = SceneStep.objects.create(
        scene=job.scenes.get(number=3),
        kind="clip",
        person_shown="no face",
        tool_call=ToolCall.objects.filter(tool="plan_ad").get(),
    )

    person, room = person_and_room(step, step.person_shown)

    assert person is None
    assert not job.produced.filter(kind__in=["portrait", "person_in_room"]).exists()
    assert "make_person_in_room" not in paid_for()
    (asked,) = handoffs("make_room_alone")
    before = job.produced.get(kind="before_picture")
    assert asked["pictures"] == [before.file]
    assert "with the person taken out" not in asked["prompt"]
    assert room == job.produced.get(kind="room_alone")
    assert room.made_from == before
