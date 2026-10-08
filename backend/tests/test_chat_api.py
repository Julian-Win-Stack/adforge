"""The chat, tested the way the browser uses it: start a session, send a message, attach a
photo, poll for what is new, rename, and come back to the whole conversation."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.utils import timezone
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge import file_store
from agents.models import ToolCall
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.fake import FakeModel, turn
from jobs.models import Job, Scene, SceneStep

from .conftest import MUG_FRONT, MUG_SIDE, picture, served

# Each request commits on its own, as on the real server. The default wraps the whole test
# in one transaction, which would hide code that only works inside one.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def start_session(api: APIClient) -> Callable[..., str]:
    """Start a session through the API and give back its id."""

    def start() -> str:
        response = api.post("/api/sessions/", {}, format="json")
        assert response.status_code == 201, response.json()
        session_id: str = response.json()["id"]
        return session_id

    return start


@pytest.fixture
def send(api: APIClient, fake_model: FakeModel) -> Callable[..., Any]:
    """Send a user's message, with photos when there are any, as the browser does. These
    tests are about the chat itself, so the producer it starts says nothing back."""

    def sending(session_id: str, text: str = "", *photos: tuple[str, bytes]) -> Any:
        fake_model.respond("produce", turn())
        if photos:
            data: dict[str, object] = {
                "text": text,
                "photos": [
                    SimpleUploadedFile(name, content, content_type="image/png")
                    for name, content in photos
                ],
            }
            return api.post(f"/api/sessions/{session_id}/messages/", data, format="multipart")
        return api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")

    return sending


def test_a_new_session_is_named_from_the_users_first_message(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    assert api.get(f"/api/sessions/{session_id}/").json()["name"] == ""

    sent = send(session_id, "Make me a 15 second ad for https://shop.example/products/mug")

    assert sent.status_code == 201, sent.json()
    # The name comes back with the message, so the sidebar shows it without asking again.
    assert sent.json()["session"]["name"] == (
        "Make me a 15 second ad for https://shop.example/products/mug"
    )
    assert sent.json()["message"] == {
        "seq": 1,
        "role": "user",
        "level": "",
        "text": "Make me a 15 second ad for https://shop.example/products/mug",
        "created_at": sent.json()["message"]["created_at"],
        "attachments": [],
    }


def test_a_session_started_with_only_photos_is_named_by_the_first_words_the_user_types(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    session = Session.objects.get(pk=session_id)

    assert send(session_id, "", ("front.png", MUG_FRONT)).json()["session"]["name"] == ""
    messages.add(session, role=Message.Role.AGENT, text="Nice mug. What should the ad be?")
    assert api.get(f"/api/sessions/{session_id}/").json()["name"] == ""

    named = send(session_id, "A 15 second ad for it")

    assert named.json()["session"]["name"] == "A 15 second ad for it"
    assert send(session_id, "Make it warmer").json()["session"]["name"] == "A 15 second ad for it"


def test_a_long_first_message_is_cut_short_for_the_name(
    start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()

    sent = send(session_id, "Make me an ad for my mug. " * 10)

    name = sent.json()["session"]["name"]
    assert len(name) <= messages.NAME_LENGTH
    assert name.startswith("Make me an ad for my mug.")
    # Only the first message names the session.
    send(session_id, "Actually, make it 20 seconds")
    assert Session.objects.get(pk=session_id).name == name


def test_a_session_the_user_named_keeps_its_name(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    renamed = api.patch(f"/api/sessions/{session_id}/", {"name": "Mug ad"}, format="json")

    assert renamed.status_code == 200
    sent = send(session_id, "Make me an ad for my mug")

    assert sent.json()["session"]["name"] == "Mug ad"


def test_a_session_can_be_renamed_after_it_has_been_talked_to(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")

    renamed = api.patch(f"/api/sessions/{session_id}/", {"name": "Kiln & Co mug"}, format="json")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Kiln & Co mug"
    assert api.get("/api/sessions/").json()[0]["name"] == "Kiln & Co mug"


def test_sessions_are_listed_newest_first(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    first = start_session()
    send(first, "An ad for my mug")
    second = start_session()
    send(second, "An ad for my kettle")

    listed = api.get("/api/sessions/").json()

    assert [session["name"] for session in listed] == ["An ad for my kettle", "An ad for my mug"]
    assert [session["id"] for session in listed] == [second, first]


def test_a_reopened_session_gives_back_its_whole_conversation(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")
    session = Session.objects.get(pk=session_id)
    messages.add(session, role=Message.Role.AGENT, text="How long should it be?")
    send(session_id, "15 seconds")

    reopened = api.get(f"/api/sessions/{session_id}/").json()

    assert reopened["name"] == "Make me an ad for my mug"
    assert [
        (message["seq"], message["role"], message["text"]) for message in reopened["messages"]
    ] == [
        (1, "user", "Make me an ad for my mug"),
        (2, "agent", "How long should it be?"),
        (3, "user", "15 seconds"),
    ]


def test_a_photo_attached_to_a_message_is_kept_through_the_file_store(
    start_session: Callable[..., str], send: Callable[..., Any], settings: Settings
) -> None:
    session_id = start_session()

    sent = send(session_id, "Use these photos", ("front.png", MUG_FRONT), ("side.png", MUG_SIDE))

    assert sent.status_code == 201, sent.json()
    attachments = sent.json()["message"]["attachments"]
    assert [(one["position"], one["kind"]) for one in attachments] == [
        (1, "picture"),
        (2, "picture"),
    ]
    kept = Attachment.objects.order_by("position")
    assert [file_store.read(one.file) for one in kept] == [MUG_FRONT, MUG_SIDE]
    # The browser is given a link to each one, and the file is really there.
    assert [served(one["url"], settings) for one in attachments] == [MUG_FRONT, MUG_SIDE]


def test_a_message_has_to_say_or_carry_something(
    start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()

    sent = send(session_id, "")

    assert sent.status_code == 400
    assert sent.json() == {"text": ["Type something, or attach a photo."]}
    assert Message.objects.count() == 0


@pytest.mark.parametrize(
    ("unusable", "refused"),
    [
        pytest.param(
            SimpleUploadedFile("notes.txt", b"not a photo", content_type="text/plain"),
            "notes.txt isn't a PNG, JPEG, WebP or GIF image.",
            id="a file that isn't an image",
        ),
        pytest.param(
            SimpleUploadedFile(
                "side.tiff", picture(10, 10, (0, 0, 0), "TIFF"), content_type="image/tiff"
            ),
            "side.tiff isn't a PNG, JPEG, WebP or GIF image.",
            id="a TIFF",
        ),
        pytest.param(
            SimpleUploadedFile("side.heic", b"ftypheic", content_type="image/heic"),
            "side.heic isn't a PNG, JPEG, WebP or GIF image.",
            id="a HEIC",
        ),
        pytest.param(
            SimpleUploadedFile(
                "huge.png", b"\x89PNG" + bytes(15_000_001), content_type="image/png"
            ),
            "huge.png is over 15 MB.",
            id="a photo over 15 MB",
        ),
    ],
)
def test_a_file_that_cant_be_kept_as_a_photo_is_refused_by_name(
    api: APIClient, start_session: Callable[..., str], unusable: SimpleUploadedFile, refused: str
) -> None:
    session_id = start_session()

    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": "Here they are",
            "photos": [
                SimpleUploadedFile("front.png", MUG_FRONT, content_type="image/png"),
                unusable,
            ],
        },
        format="multipart",
    )

    assert sent.status_code == 400
    assert sent.json() == {"photos": [refused]}
    # Not even the photo that could be used is kept.
    assert Message.objects.count() == 0
    assert Attachment.objects.count() == 0


def test_each_poll_asks_only_for_what_has_happened_since_the_last_one(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")
    session = Session.objects.get(pk=session_id)

    seen = api.get(f"/api/sessions/{session_id}/messages/?after=0").json()
    assert [message["seq"] for message in seen] == [1]

    messages.add(session, role=Message.Role.AGENT, text="Reading the product page")
    messages.add(session, role=Message.Role.AGENT, text="The page has what the ad needs")

    since = api.get(f"/api/sessions/{session_id}/messages/?after=1").json()
    assert [(message["seq"], message["text"]) for message in since] == [
        (2, "Reading the product page"),
        (3, "The page has what the ad needs"),
    ]
    # Nothing new since then, so the poll comes back empty rather than repeating itself.
    assert api.get(f"/api/sessions/{session_id}/messages/?after=3").json() == []


def test_two_messages_can_never_share_a_place_in_the_conversation(
    start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")
    session = Session.objects.get(pk=session_id)

    with pytest.raises(IntegrityError, match='unique constraint "one_message_per_seq"'):
        Message.objects.create(session=session, seq=1, role=Message.Role.AGENT, text="Hello")


def test_an_agent_message_can_carry_a_picture_a_sound_and_a_video(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")
    session = Session.objects.get(pk=session_id)

    portrait = file_store.save("portrait.png", MUG_FRONT)
    voice = file_store.save("voice.mp3", b"ID3 a spoken line")
    advert = file_store.save("ad.mp4", b"a finished ad")
    messages.add(
        session,
        role=Message.Role.AGENT,
        text="Here's your presenter",
        carrying=[
            messages.AttachedFile(Attachment.Kind.PICTURE, portrait),
            messages.AttachedFile(Attachment.Kind.SOUND, voice),
            messages.AttachedFile(Attachment.Kind.VIDEO, advert),
        ],
    )

    polled = api.get(f"/api/sessions/{session_id}/messages/?after=1").json()

    assert polled[0]["role"] == "agent"
    assert polled[0]["attachments"] == [
        {"position": 1, "kind": "picture", "url": "/media/portrait.png"},
        {"position": 2, "kind": "sound", "url": "/media/voice.mp3"},
        {"position": 3, "kind": "video", "url": "/media/ad.mp4"},
    ]


def test_a_message_sent_while_the_work_is_in_flight_is_taken_straight_away(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    session_id = start_session()
    send(session_id, "Make me an ad for my mug")
    session = Session.objects.get(pk=session_id)
    Job.objects.create(
        session=session, product_url="https://shop.example/products/mug", status=Job.Status.PLANNED
    )

    interrupting = send(session_id, "Actually, make it 20 seconds")

    # Taken and given its place in the conversation, not refused because work is running.
    assert interrupting.status_code == 201, interrupting.json()
    assert interrupting.json()["message"]["seq"] == 2
    assert api.get(f"/api/sessions/{session_id}/messages/?after=1").json()[0]["text"] == (
        "Actually, make it 20 seconds"
    )


def test_a_session_holds_its_jobs_and_a_job_points_at_the_one_it_varies() -> None:
    session = Session.objects.create()
    first = Job.objects.create(session=session, product_url="https://shop.example/products/mug")
    variant = Job.objects.create(
        session=session, product_url="https://shop.example/products/mug", variant_of=first
    )

    assert list(session.jobs.order_by("created_at")) == [first, variant]
    assert list(first.variants.all()) == [variant]
    assert variant.variant_of == first


def test_talking_to_a_session_that_isnt_there_says_so(api: APIClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert api.get(f"/api/sessions/{missing}/").status_code == 404
    assert api.patch(f"/api/sessions/{missing}/", {"name": "Mug"}).status_code == 404
    assert api.get(f"/api/sessions/{missing}/messages/").status_code == 404
    assert api.post(f"/api/sessions/{missing}/messages/", {"text": "Hi"}).status_code == 404


# What the user is told when another session is making an ad.
ANOTHER_SESSION_IS_BUSY = "Another ad is being made right now. Please try again once it's finished."


def test_a_message_is_refused_while_another_sessions_producer_is_working(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    working = start_session()
    Session.objects.filter(pk=working).update(producer_running=True)
    waiting = start_session()

    refused = send(waiting, "Make me an ad for my mug")

    assert refused.status_code == 409
    assert refused.json() == {"detail": ANOTHER_SESSION_IS_BUSY}
    # Not kept, and nothing started.
    assert api.get(f"/api/sessions/{waiting}/messages/").json() == []
    assert not Session.objects.get(pk=waiting).producer_running


def test_the_busy_session_itself_can_always_be_talked_to(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    working = start_session()
    Session.objects.filter(pk=working).update(producer_running=True)

    interrupting = send(working, "Actually, make it 20 seconds")

    assert interrupting.status_code == 201, interrupting.json()
    assert [m["text"] for m in api.get(f"/api/sessions/{working}/messages/").json()] == [
        "Actually, make it 20 seconds"
    ]


def _running_step(session_id: str, seen_ago: timedelta) -> None:
    """A scene step running in the session, last seen `seen_ago`."""
    session = Session.objects.get(pk=session_id)
    job = Job.objects.create(session=session, product_url="https://shop.example/products/mug")
    scene = Scene.objects.create(job=job, number=1, line="It holds 350 ml.")
    call = ToolCall.objects.create(
        session=session, agent="producer", tool="make_starting_picture", call_id="c1", arguments={}
    )
    SceneStep.objects.create(
        scene=scene,
        kind=SceneStep.Kind.STARTING_PICTURE,
        tool_call=call,
        line=scene.line,
        seen_at=timezone.now() - seen_ago,
    )


def test_a_message_is_refused_while_another_session_has_a_scene_step_working(
    api: APIClient, start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    working = start_session()
    _running_step(working, seen_ago=timedelta(seconds=30))
    waiting = start_session()

    refused = send(waiting, "Make me an ad for my mug")

    assert refused.status_code == 409
    assert refused.json() == {"detail": ANOTHER_SESSION_IS_BUSY}
    assert api.get(f"/api/sessions/{waiting}/messages/").json() == []


def test_a_scene_step_whose_worker_died_no_longer_keeps_its_session_busy(
    start_session: Callable[..., str], send: Callable[..., Any]
) -> None:
    crashed = start_session()
    # Still marked running, but not seen for longer than a working step ever goes unseen.
    _running_step(crashed, seen_ago=timedelta(seconds=121))
    waiting = start_session()

    taken = send(waiting, "Make me an ad for my mug")

    assert taken.status_code == 201, taken.json()
