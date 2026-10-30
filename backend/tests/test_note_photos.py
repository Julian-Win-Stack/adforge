"""Photos in scene notes (approved by Julian, Oct 29): the shop owner sends a photo with a
scene's note, the photo is kept with that scene, the producer reads it so, and code holds the
producer's turn open until the photo is in that scene's references or it asked the shop owner
about it. Through the chat, the producer faked and scripted, on the three-scene ad shown
(scenes 1 and 3 her talking, scene 2 the mug)."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from agents.models import SystemNote
from chat.models import Attachment
from gateway.fake import FakeModel, meanwhile, turn
from jobs.library import PictureLabel, Recoloured, add_to_library, library_of
from jobs.models import Job

from .conftest import (
    LABELLED,
    ScriptedProducer,
    given_to_the_producer,
    photo,
    producer_turns,
    scripted_turns,
)
from .test_pause import pause
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_fixes import shown  # noqa: F401 (a fixture)
from .test_steering_rows import GREEN, Watched, answer_of, watched  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

LID_ON = "the mug with its lid on"
NOTES = "Scene 2: show the lid on, like in my photo"


def sending_notes(
    api: APIClient, session_id: str, text: str, scenes: list[int], status: int = 201
) -> dict[str, Any]:
    """The shop owner sends the scene cards' notes `text`, with one photo for each scene
    in `scenes`, each sent with that scene's note, as the cards send them."""
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": text,
            "photos": [
                SimpleUploadedFile(f"photo{n}.png", photo(n), content_type="image/png")
                for n in range(1, len(scenes) + 1)
            ],
            "photo_scenes": scenes,
        },
        format="multipart",
    )
    assert sent.status_code == status, sent.json()
    answer: dict[str, Any] = sent.json()
    return answer


def filed(number: int, scene: int = 2) -> list[tuple[str, dict[str, Any]]]:
    """The producer's calls putting owner photo `number` into `scene`'s references."""
    item = len(library_of(Job.objects.get())) + 1
    return [
        ("add_to_library", {"photos": [{"photo": number, "shows": LID_ON}], "videos": []}),
        (
            "set_references",
            {"scene": scene, "pictures": [{"number": item, "for": "state"}], "exclude": None},
        ),
    ]


def owner_said(turn_number: int) -> list[str]:
    """What the shop owner said, as the producer was given it on its turn `turn_number`."""
    return [
        each["text"]
        for each in given_to_the_producer(turn_number)
        if each["kind"] == "said" and each["by"] == "user"
    ]


def reminders() -> list[str]:
    return list(
        SystemNote.objects.filter(kind=SystemNote.Kind.NOTE_PHOTO).values_list("text", flat=True)
    )


@pytest.fixture
def labelled(fake_model: FakeModel) -> None:
    fake_model.respond("label_photo", {**LABELLED, "colour": GREEN, "label": "The mug, lid on."})


def test_a_photo_sent_with_a_scene_note_is_kept_with_its_scene_and_read_so(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("produce", *scripted_turns(*filed(1), reply="Scene 2 uses your photo."))
    first = producer_turns() + 1

    sent = sending_notes(api, session_id, NOTES, [2])

    # Kept with the scene of the note it came with, and the chat is told so.
    (attachment,) = Attachment.objects.filter(message__role="user")
    assert attachment.note_scene == 2
    assert [each["note_scene"] for each in sent["message"]["attachments"]] == [2]
    # The producer read which scene the photo is for.
    assert owner_said(first)[-1] == (
        f"{NOTES}\n\n[Attached 1 picture: owner photo 1, sent with the Scene 2 note]"
    )
    # Put in scene 2's references, its turn ended at once.
    assert answer_of("set_references").startswith("Scene 2's references: pictures ")
    assert reminders() == []
    attachment.refresh_from_db()
    assert attachment.settled_at is not None
    assert ("agent", "Scene 2 uses your photo.") in watched.owner_saw()


def test_a_reply_leaving_the_photo_unused_is_held_until_it_is_in_the_scene(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(says="Got it, I'll keep that in mind."),
        *scripted_turns(*filed(1), reply="Scene 2 uses your photo now."),
    )

    sending_notes(api, session_id, NOTES, [2])

    # The reply that left it out didn't end the turn: the app told the producer why.
    (reminder,) = reminders()
    assert reminder == (
        "Owner photo 1, sent with the Scene 2 note, isn't in scene 2's references yet. Put it "
        "there (add_to_library, then set_references), or ask the shop owner about it with "
        "ask_owner, naming it in the question's photos. Your turn can't end before."
    )
    assert f"From the app, not the shop owner: {reminder}" in str(
        given_to_the_producer(producer_turns() - 2)
    )
    assert answer_of("set_references").startswith("Scene 2's references: pictures ")
    assert [said for said in watched.owner_saw() if said[0] == "agent"] == [
        ("agent", "Got it, I'll keep that in mind."),
        ("agent", "Scene 2 uses your photo now."),
    ]
    # No red notice: it was used.
    assert ("notice", UNUSED) not in watched.owner_saw()


def test_a_photo_put_in_another_scene_is_held_and_asking_the_owner_ends_the_turn(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    # Sent with scene 1's note (her talking), put in scene 2's references instead.
    fake_model.respond(
        "produce",
        *scripted_turns(*filed(1, scene=2), reply="Done."),
        turn(
            calls=[
                (
                    "ask_owner",
                    {"question": "Scene 1 is you talking: where should it go?", "photos": [1]},
                )
            ]
        ),
    )

    sending_notes(api, session_id, "Scene 1: hold the mug like this", [1])

    (reminder,) = reminders()
    assert reminder.startswith(
        "Owner photo 1, sent with the Scene 1 note, isn't in scene 1's references yet."
    )
    assert ("agent", "Scene 1 is you talking: where should it go?") in watched.owner_saw()

    # Their answer is the producer's to act on: the photo holds its turn no more.
    producer.told("Leave it out then.", "OK, scene 1 stays as it is.")
    assert len(reminders()) == 1


def test_two_photos_each_with_its_own_scene_are_held_until_both_are_used(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        *scripted_turns(*filed(2, scene=2), reply="Scene 2 uses your photo."),
        turn(
            calls=[
                (
                    "ask_owner",
                    {"question": "What should scene 1 take from your photo?", "photos": [1]},
                )
            ]
        ),
    )
    first = producer_turns() + 1

    # Sent as a form, whose line ends come as two characters each.
    sending_notes(api, session_id, "Scene 1: like this\r\nScene 2: lid on, like this", [1, 2])

    assert owner_said(first)[-1].startswith("Scene 1: like this\nScene 2: lid on, like this\n\n")
    assert owner_said(first)[-1].endswith(
        "[Attached 2 pictures: owner photo 1, sent with the Scene 1 note; owner photo 2, sent "
        "with the Scene 2 note]"
    )
    # Only the photo still unused is named.
    (reminder,) = reminders()
    assert reminder.startswith("Owner photo 1, sent with the Scene 1 note, isn't in scene 1's")
    assert "owner photo 2" not in reminder.lower()


def test_another_photo_in_the_scene_doesnt_count_for_one_left_out(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    # Two photos with scene 2's note; only the first is put in its references.
    fake_model.respond(
        "produce",
        *scripted_turns(*filed(1, scene=2), reply="Scene 2 uses your photo."),
        turn(
            calls=[("ask_owner", {"question": "What should scene 2 take from your second photo?"})]
        ),
    )

    sending_notes(api, session_id, "Scene 2: lid on, and the handle like this", [2, 2])

    (reminder,) = reminders()
    assert reminder.startswith(
        "Owner photo 2, sent with the Scene 2 note, isn't in scene 2's references yet."
    )
    assert "Owner photo 1" not in reminder


def test_a_question_about_something_else_doesnt_let_the_photo_go(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("ask_owner", {"question": "Should the voice be faster too?"})]),
    )
    sending_notes(api, session_id, NOTES, [2])
    assert reminders() == []

    producer.told(
        "Yes, faster.",
        "OK, I'll make the voice faster.",
        *filed(1),
    )

    (reminder,) = reminders()
    assert reminder.startswith("Owner photo 1, sent with the Scene 2 note, isn't in scene 2's")
    assert answer_of("set_references").startswith("Scene 2's references: pictures ")


def test_a_version_of_the_photo_in_the_scene_counts_as_the_photo(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    job = Job.objects.get()
    photo_item = len(library_of(job)) + 1

    def recoloured() -> None:
        # The clean picture of the photo, recoloured, as the recolour step keeps one.
        clean = library_of(job)[photo_item - 1]
        label = PictureLabel.model_validate({**LABELLED, "colour": "red"})
        add_to_library(job, clean.file, label, Recoloured(of=photo_item, colour="red"))

    fake_model.respond(
        "produce",
        turn(calls=[("add_to_library", {"photos": [{"photo": 1, "shows": LID_ON}], "videos": []})]),
        meanwhile(
            recoloured,
            turn(
                calls=[
                    (
                        "set_references",
                        {
                            "scene": 2,
                            "pictures": [{"number": photo_item + 1, "for": "state"}],
                            "exclude": None,
                        },
                    )
                ]
            ),
        ),
        turn(says="Scene 2 uses your mug in red."),
    )

    sending_notes(api, session_id, NOTES, [2])

    assert answer_of("set_references").startswith(
        f"Scene 2's references: pictures {photo_item + 1}"
    )
    assert reminders() == []


UNUSED = (
    "The photo you sent with your Scene 2 note wasn't used in scene 2. Send it again with your "
    "note, or tell me what to do with it."
)


def test_a_photo_still_unused_after_two_reminders_is_told_to_the_owner_and_the_turn_ends(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        turn(says="Noted."),
        turn(says="Noted, really."),
        turn(says="Noted, I promise."),
    )

    sending_notes(api, session_id, NOTES, [2])

    assert len(reminders()) == 2
    assert ("notice", UNUSED) in watched.owner_saw()
    job = Job.objects.get()
    assert UNUSED in str(job.warnings)
    (attachment,) = Attachment.objects.filter(message__role="user")
    attachment.refresh_from_db()
    assert attachment.settled_at is not None


def test_a_photo_whose_scene_was_removed_holds_nothing(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce", *scripted_turns(("remove_scene", {"scene": 2}), reply="Scene 2 is gone.")
    )

    sending_notes(api, session_id, "Scene 2: drop this scene, it looks like my photo", [2])

    assert reminders() == []
    assert ("agent", "Scene 2 is gone.") in watched.owner_saw()


def test_a_photo_whose_scene_moved_is_held_for_the_scene_where_it_is_now(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    labelled: None,
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond(
        "produce",
        *scripted_turns(("reorder_scenes", {"order": [1, 3, 2]}), reply="Moved it to the end."),
        *scripted_turns(*filed(1, scene=3), reply="Scene 3 uses your photo."),
    )

    sending_notes(api, session_id, "Scene 2: put this one last, lid on like my photo", [2])

    (reminder,) = reminders()
    assert reminder.startswith(
        "Owner photo 1, sent with the Scene 2 note, isn't in scene 3's references yet (scene 2 "
        "is scene 3 now)."
    )
    assert answer_of("set_references").startswith("Scene 3's references: pictures ")
    # The chat still labels it with the scene its note named.
    (attachment,) = Attachment.objects.filter(message__role="user")
    assert attachment.note_scene == 2


def test_while_paused_a_photo_holds_nothing(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    # The shop owner presses pause while the producer reads their note.
    fake_model.respond(
        "produce",
        meanwhile(lambda: pause(api, session_id), turn(says="I'll use it.")),
        turn(says="Paused."),
    )

    sending_notes(api, session_id, NOTES, [2])

    assert reminders() == []
    assert [said for said in watched.owner_saw() if said[0] == "agent"][-1] == ("agent", "Paused.")


def test_a_newer_photo_gets_every_reminder_of_its_own(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
    api: APIClient,
    session_id: str,
) -> None:
    # The first photo got one reminder, then the turn ended on a question about another thing.
    fake_model.respond(
        "produce",
        turn(says="Noted."),
        turn(calls=[("ask_owner", {"question": "Should the voice be faster too?"})]),
    )
    sending_notes(api, session_id, NOTES, [2])
    assert len(reminders()) == 1

    fake_model.respond(
        "produce",
        turn(says="Noted."),
        turn(says="Noted, really."),
        turn(says="Noted, I promise."),
    )
    sending_notes(api, session_id, "Scene 2: and this one too", [2])

    assert len(reminders()) == 3
    assert ("notice", UNUSED_TWO) in watched.owner_saw()


UNUSED_TWO = (
    "The 2 photos you sent with your Scene 2 note weren't used in scene 2. Send them again "
    "with your note, or tell me what to do with them."
)


def test_a_photo_sent_in_the_chat_box_with_no_scene_holds_nothing(
    fake_model: FakeModel,
    producer: ScriptedProducer,
    watched: Watched,  # noqa: F811
) -> None:
    producer.told("Here's a photo.", "Nice photo!", photos=[("p.png", photo(1))])

    assert reminders() == []
    (attachment,) = Attachment.objects.filter(message__role="user")
    assert attachment.note_scene is None
    assert (
        owner_said(producer_turns())[-1] == "Here's a photo.\n\n[Attached 1 picture: owner photo 1]"
    )


@pytest.mark.parametrize(
    ("scenes", "photos", "text", "refused"),
    [
        ([2, 2, 2, 2], 4, NOTES, "Up to 3 photos can go with one scene's note: scene 2 has 4."),
        ([2], 2, NOTES, "Say which scene's note each photo goes with."),
        ([0], 1, NOTES, "Ensure this value is greater than or equal to 1."),
        ([5], 1, NOTES, "The ad has no scene 5: send the note again."),
        ([2], 1, " ", "Say in the note what to take from its photo."),
    ],
)
def test_photos_sent_with_notes_wrongly_are_refused(
    api: APIClient, session_id: str, scenes: list[int], photos: int, text: str, refused: str
) -> None:
    sent = api.post(
        f"/api/sessions/{session_id}/messages/",
        {
            "text": text,
            "photos": [
                SimpleUploadedFile(f"p{n}.png", photo(n), content_type="image/png")
                for n in range(photos)
            ],
            "photo_scenes": scenes,
        },
        format="multipart",
    )

    assert sent.status_code == 400
    assert refused in str(sent.json())
    assert not Attachment.objects.exists()
