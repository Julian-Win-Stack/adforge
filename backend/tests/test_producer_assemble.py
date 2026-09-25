"""The finished ad: every scene's clip, cut to where its words are said and put together
with ffmpeg, driven through the chat. The producer's model and the scene models are faked at
the gateway, but the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem

from .conftest import HeldSteps, colour_at, paid_for, results_of, served, silences, video

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits the line.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug.",
}

# The fake voice says 2 words a second, so the plan's scenes of 8, 7 and 3 words take 4,
# 3.5 and 1.5 seconds to say: 9 seconds in all.
ASSEMBLED = (
    "Assembled the ad (version 1, 9 seconds) from each scene's clip, cut to where its words "
    "are said, and showed it to the shop owner in the chat. Scene 1 plays from 0 to 4 "
    "seconds, scene 2 from 4 to 7.5 and scene 3 from 7.5 to 9. Tell the shop owner."
)


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def for_scenes(tool: str, scenes: tuple[int, ...], **arguments: Any) -> list[tuple[str, Any]]:
    return [(tool, {"scene": scene, **arguments}) for scene in scenes]


def finish(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    clipped: tuple[int, ...] = (1, 2, 3),
) -> None:
    """Make every scene's starting picture, audio and transcript, and the clips of
    `clipped`, which finishes those scenes."""
    scenes = (1, 2, 3)
    fake_model.respond(
        "produce",
        turn(
            calls=for_scenes("make_starting_picture", scenes, note=None)
            + for_scenes("make_line_audio", scenes)
        ),
        turn(says="Started every scene."),
    )
    say("Make every scene")
    fake_model.respond("choose_starting_picture", *[CHOICE] * len(scenes))
    run(fake_model, steps)
    fake_model.respond(
        "produce",
        turn(calls=for_scenes("transcribe_line_audio", scenes)),
        turn(says="Transcribing."),
    )
    say("Transcribe them")
    run(fake_model, steps)
    fake_model.respond(
        "produce", turn(calls=for_scenes("make_clip", clipped)), turn(says="Making the clips.")
    )
    say("Make the clips")
    run(fake_model, steps)


@pytest.fixture
def finished(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose three scenes are finished: each has its clip."""
    finish(fake_model, steps, say)


def assemble(fake_model: FakeModel, say: Callable[..., None]) -> None:
    fake_model.respond("produce", turn(calls=[("assemble_ad", {})]), turn(says="Here's your ad!"))
    say("Put the ad together")


def ads() -> list[ProducedItem]:
    return list(Job.objects.get().produced.filter(kind="finished_ad").order_by("version"))


def videos_shown(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    """Every message in the chat that carries a video, with who sent it."""
    return [
        message
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if any(attached["kind"] == "video" for attached in message["attachments"])
    ]


def test_the_scenes_are_assembled_into_one_ad_that_plays_in_the_chat(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
    session_id: str,
    settings: Settings,
) -> None:
    assemble(fake_model, say)

    assert results_of("assemble_ad") == [ASSEMBLED]
    (ad,) = ads()
    assert (ad.version, ad.scene, ad.seconds) == (1, None, 9.0)
    width, height, seconds = video(read(ad.file))
    assert (width, height) == (72, 128)
    assert seconds == pytest.approx(9.0, abs=0.1)
    # Each scene's own clip, in order: the fake's clips are red, lime and blue, and each
    # is looked at halfway through its scene.
    shows = [colour_at(read(ad.file), middle) for middle in (2.0, 5.75, 8.25)]
    assert shows == ["red", "lime", "blue"]
    # Shown by the producer, with no words of its own: its next message talks about it.
    (shown,) = videos_shown(api, session_id)
    assert (shown["role"], shown["text"]) == ("agent", "")
    (attached,) = shown["attachments"]
    assert served(attached["url"], settings) == read(ad.file)
    last = api.get(f"/api/sessions/{session_id}/messages/").json()[-1]
    assert (last["role"], last["text"]) == ("agent", "Here's your ad!")


def test_the_dead_air_between_scenes_is_cut_on_the_word_timings(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A second of silence before and after every line: each clip is 2 seconds longer than
    # its words, so the clips together last 15 seconds.
    fake_model.pause_seconds = 1.0
    finish(fake_model, steps, say)
    clips = list(ProducedItem.objects.filter(kind="clip").order_by("scene__number"))
    assert [clip.seconds for clip in clips] == [6.0, 5.5, 3.5]

    assemble(fake_model, say)

    (ad,) = ads()
    # Each scene is kept from its first word to its last, with a tenth of a second either
    # side so no sound is clipped: 9 seconds of words and 0.6 of margin.
    assert ad.seconds == 9.6
    assert video(read(ad.file))[2] == pytest.approx(9.6, abs=0.1)
    # The words are heard all the way through: only the margins are quiet, a fifth of a
    # second between scenes, too short to be dead air.
    assert silences(read(ad.file)) == []
    shows = [colour_at(read(ad.file), middle) for middle in (2.1, 6.05, 8.75)]
    assert shows == ["red", "lime", "blue"]
    assert ad.cuts == [
        {
            "scene": 1,
            "clip": clips[0].pk,
            "clip_start": 0.9,
            "clip_end": 5.1,
            "start": 0.0,
            "end": 4.2,
        },
        {
            "scene": 2,
            "clip": clips[1].pk,
            "clip_start": 0.9,
            "clip_end": 4.6,
            "start": 4.2,
            "end": 7.9,
        },
        {
            "scene": 3,
            "clip": clips[2].pk,
            "clip_start": 0.9,
            "clip_end": 2.6,
            "start": 7.9,
            "end": 9.6,
        },
    ]


def test_the_ad_isnt_assembled_while_a_scene_is_unfinished(
    api: APIClient,
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    finish(fake_model, steps, say, clipped=(1,))

    assemble(fake_model, say)

    assert results_of("assemble_ad") == [
        "Refused: scenes 2 and 3 aren't finished: a scene is finished once its clip is made. "
        "Finish them, then assemble the ad. Nothing was done."
    ]
    assert ads() == []
    assert videos_shown(api, session_id) == []


def test_the_ad_isnt_assembled_while_a_clip_is_being_made(
    fake_model: FakeModel, finished: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Scene 1 gets a new starting picture, and its new clip is started but not made yet.
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": "Hold it higher."})]),
        turn(says="Making a new picture."),
    )
    say("Make scene 1's picture again, holding it higher")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)
    fake_model.respond(
        "produce", turn(calls=[("make_clip", {"scene": 1})]), turn(says="Making the clip.")
    )
    say("Make its clip")
    assert len(steps.held) == 1

    assemble(fake_model, say)

    assert results_of("assemble_ad") == [
        "Refused: scene 1's clip is still being made. You'll be told when it's ready; assemble "
        "the ad then. Nothing was done."
    ]
    assert ads() == []


def test_the_ad_isnt_assembled_before_it_is_planned(
    fake_model: FakeModel, page_read: str, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)

    assert results_of("assemble_ad") == [
        "Refused: the ad hasn't been planned yet, and it is assembled from its scenes. Nothing "
        "was done."
    ]


def test_an_ad_already_assembled_is_handed_back_and_charges_nothing(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
    session_id: str,
) -> None:
    assemble(fake_model, say)
    paid = paid_for()

    assemble(fake_model, say)

    assert paid_for() == paid
    assert [ad.version for ad in ads()] == [1]
    assert len(videos_shown(api, session_id)) == 1
    assert results_of("assemble_ad")[-1] == (
        "The ad was already assembled from these clips (version 1, 9 seconds) and shown to the "
        "shop owner, so nothing was made again. Assembling costs nothing."
    )


def test_a_scene_made_again_gets_a_new_version_of_the_ad_and_the_old_one_is_kept(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    steps: HeldSteps,
    say: Callable[..., None],
    session_id: str,
) -> None:
    assemble(fake_model, say)
    first_clip = ProducedItem.objects.get(kind="clip", scene__number=1)
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": 1, "note": "Hold it higher."})]),
        turn(says="Making a new picture."),
    )
    say("Make scene 1's picture again, holding it higher")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)
    fake_model.respond(
        "produce", turn(calls=[("make_clip", {"scene": 1})]), turn(says="Making the clip.")
    )
    say("Make its clip")
    run(fake_model, steps)

    assemble(fake_model, say)

    first, second = ads()
    assert (first.version, second.version) == (1, 2)
    assert first.file != second.file
    assert first.cuts[0]["clip"] == first_clip.pk
    new_clip = ProducedItem.objects.filter(kind="clip", scene__number=1).latest("version")
    assert second.cuts[0]["clip"] == new_clip.pk != first_clip.pk
    assert len(videos_shown(api, session_id)) == 2
