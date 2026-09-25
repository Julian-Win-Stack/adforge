"""Creating the ad's background music, driven through the chat. The producer's model is
faked at the gateway to call create_music, and each test checks what the tool handed back,
what was stored and what was paid for."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db.models.signals import pre_save
from rest_framework.test import APIClient

from adforge import file_store
from adforge.retry import OutsideServiceDown
from agents.models import ToolCall
from agents.tasks import restart_dead_producers
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem

from .conftest import FACTS_OK, NO_CHOICES, handoffs, paid_for, results_of
from .test_producer_restarts import WorkerStopped, the_producer_died, the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)

# The script's 18 words, at the fake voice's 2 words a second, take 9 seconds: 5 more to spare.
MUSIC_SECONDS = 14
MUSIC_PROMPT = (
    "Light upbeat lo-fi. Background music for a short video ad, played under a person "
    "speaking. Instrumental only, no vocals, no singing."
)


def make_music(fake_model: FakeModel, say: Callable[..., None], mood: str) -> None:
    fake_model.respond(
        "produce", turn(calls=[("create_music", {"mood": mood})]), turn(says="Music's ready.")
    )
    say("Make the music")


def test_the_music_is_made_as_long_as_the_script_takes_to_say_with_some_to_spare(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    session_id: str,
    say: Callable[..., None],
) -> None:
    shown_before = api.get(f"/api/sessions/{session_id}/messages/").json()

    make_music(fake_model, say, "  Light upbeat\nlo-fi ")

    assert handoffs("make_music") == [{"prompt": MUSIC_PROMPT, "seconds": MUSIC_SECONDS}]
    music = Job.objects.get().produced.get(kind="music")
    assert (music.version, music.scene, music.seconds, music.text) == (
        1,
        None,
        MUSIC_SECONDS,
        MUSIC_PROMPT,
    )
    assert file_store.read(music.file) == fake_model.music[0]
    (paid,) = ModelCall.objects.filter(purpose="make_music")
    # Sonilo is billed by the second of music made, at $0.0025 a second.
    assert (paid.audio_seconds, paid.cost_usd) == (MUSIC_SECONDS, Decimal("0.035"))
    assert results_of("create_music") == [
        "Made the music (version 1, 14 seconds): Light upbeat lo-fi, instrumental. It isn't "
        "shown to the shop owner: they hear it in the finished ad."
    ]
    # The shop owner sees the portrait, the voice and the finished ad, never the music alone.
    shown = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert [m["attachments"] for m in shown if m["attachments"]] == [
        m["attachments"] for m in shown_before if m["attachments"]
    ]


def test_music_asked_for_again_in_the_same_mood_is_handed_back_without_paying_again(
    fake_model: FakeModel, checked: None, say: Callable[..., None]
) -> None:
    make_music(fake_model, say, "light upbeat lo-fi")

    make_music(fake_model, say, "Light upbeat lo-fi")

    assert paid_for().count("make_music") == 1
    assert Job.objects.get().produced.filter(kind="music").count() == 1
    assert results_of("create_music")[1] == (
        "The music was already made in this mood for this script (version 1, 14 seconds), "
        "so nothing was made or paid for again. Making it cost $0.035."
    )


def test_music_in_another_mood_is_a_new_version_and_the_old_one_is_kept(
    fake_model: FakeModel, checked: None, say: Callable[..., None]
) -> None:
    make_music(fake_model, say, "light upbeat lo-fi")
    first = Job.objects.get().produced.get(kind="music")

    make_music(fake_model, say, "calm acoustic guitar")

    assert paid_for().count("make_music") == 2
    first.refresh_from_db()
    second = Job.objects.get().produced.get(kind="music", version=2)
    assert first.text == MUSIC_PROMPT
    assert second.text.startswith("Calm acoustic guitar. ")
    assert first.file != second.file


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("nothing", "the ad hasn't been planned yet, and the music is as long as its script."),
        (
            "planned",
            "scene 1's line hasn't passed the fact check, and nothing is made until every "
            "line has. Run the planning checks first.",
        ),
    ],
)
def test_music_is_refused_until_the_planning_checks_have_passed(
    request: pytest.FixtureRequest,
    fake_model: FakeModel,
    session_id: str,
    say: Callable[..., None],
    setup: str,
    reason: str,
) -> None:
    if setup != "nothing":
        request.getfixturevalue(setup)

    make_music(fake_model, say, "light upbeat lo-fi")

    assert results_of("create_music") == [f"Refused: {reason} Nothing was done."]
    assert "make_music" not in paid_for()


def test_music_is_refused_until_the_person_is_made_since_it_lasts_as_long_as_they_speak(
    fake_model: FakeModel, planned: None, say: Callable[..., None]
) -> None:
    # With no target length, the checks pass without the person's voice.
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Check the script")

    make_music(fake_model, say, "light upbeat lo-fi")

    assert results_of("create_music") == [
        "Refused: the person hasn't been made yet, and the music lasts as long as their voice "
        "takes to say the script. Create the person first. Nothing was done."
    ]
    assert "make_music" not in paid_for()


def test_music_that_couldnt_be_made_is_handed_back_as_a_failure_and_nothing_is_kept(
    fake_model: FakeModel, checked: None, say: Callable[..., None]
) -> None:
    fake_model.respond("make_music", *[OutsideServiceDown("fal answered 503") for _ in range(3)])

    make_music(fake_model, say, "light upbeat lo-fi")

    (result,) = results_of("create_music")
    assert result.startswith("Failed: ")
    assert "still down after 3 tries" in result
    assert not Job.objects.get().produced.filter(kind="music").exists()


def test_music_paid_for_but_not_kept_when_the_worker_stopped_is_reused(
    fake_model: FakeModel, checked: None, session_id: str, say: Callable[..., None]
) -> None:
    fake_model.respond("produce", turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]))
    not_kept = the_worker_stops(
        pre_save, ProducedItem, when=lambda item: item.kind == ProducedItem.Kind.MUSIC
    )
    with not_kept, pytest.raises(WorkerStopped):
        say("Make the music")
    (paid,) = ModelCall.objects.filter(purpose="make_music").values_list("output", flat=True)
    assert paid is not None
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Music's ready."))

    restart_dead_producers()

    assert paid_for().count("make_music") == 1
    assert Job.objects.get().produced.get(kind="music").file == paid["file"]
    assert ToolCall.objects.get(tool="create_music").result.startswith("Made the music")
