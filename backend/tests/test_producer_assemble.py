"""The finished ad: every scene's whole clip, put together
with ffmpeg, driven through the chat. The producer's model and the scene models are faked at
the gateway, but the clips are real tiny videos and ffmpeg runs for real."""

import subprocess
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from gateway.fake import FakeModel, turn
from jobs import assembly
from jobs.models import Job, ProducedItem
from jobs.planning import END_CARD_SECONDS

from .conftest import (
    PLAN,
    HeldSteps,
    chat,
    colour_at,
    drawn_in,
    loudness,
    paid_for,
    recorded,
    results_of,
    served,
    shows_the_end_card,
    video,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once, when the last of them finishes."""
    if steps.held:
        fake_model.respond("produce", turn(says="Done."))
    steps.run_held()


def for_scenes(tool: str, scenes: tuple[int, ...], **arguments: Any) -> list[tuple[str, Any]]:
    return [(tool, {"scene": scene, **arguments}) for scene in scenes]


def finish(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    clipped: tuple[int, ...] = (1, 2, 3),
    music: bool = True,
) -> None:
    """Make music and finish the requested scenes through their live agents."""
    from .scene_script import script_scene_agents

    if music:
        fake_model.respond(
            "produce",
            turn(calls=[("create_music", {"mood": "light upbeat lo-fi"})]),
            turn(says="Music's ready."),
        )
        say("Make the music")
    script_scene_agents(fake_model, scenes=len(clipped))
    fake_model.respond(
        "produce",
        turn(
            calls=for_scenes(
                "start_scene_agent",
                clipped,
                instruction="Show the planned action.",
                quotes=["Make the clips"],
            )
        ),
        turn(says="Making the clips."),
    )
    say("Make the clips")
    run(fake_model, steps)
    assert Job.objects.get().scenes.filter(number__in=clipped, status="finished").count() == len(
        clipped
    )


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


def ads_shown(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    """Every message in the chat that carries a finished ad: a video of no one scene."""
    return [
        message
        for message in videos_shown(api, session_id)
        if any(attached["scene"] is None for attached in message["attachments"])
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

    assert results_of("assemble_ad")[0].startswith("Assembled the ad (version 1, 17 seconds)")
    (ad,) = ads()
    # The three 5-second clips, then the end card.
    assert (ad.version, ad.scene, ad.seconds) == (1, None, 15.0 + END_CARD_SECONDS)
    width, height, seconds = video(read(ad.file))
    # Every ad is made at the one frame size, whatever size its clips came at.
    assert (width, height) == (assembly.FRAME_WIDTH, assembly.FRAME_HEIGHT)
    assert seconds == pytest.approx(17.0, abs=0.1)
    # Each scene's own clip, in order: the fake's clips are red, lime and blue, and each
    # is looked at halfway through its scene.
    shows = [colour_at(read(ad.file), middle) for middle in (2.0, 8.0, 13.0)]
    assert shows == ["red", "lime", "blue"]
    # Shown by the producer, with no words of its own: its next message talks about it.
    # Each scene's clip was shown with its number as it was made; the ad with none.
    *clips, shown = videos_shown(api, session_id)
    assert [[each["scene"] for each in clip["attachments"]] for clip in clips] == [[1], [2], [3]]
    assert (shown["role"], shown["text"]) == ("agent", "")
    (attached,) = shown["attachments"]
    assert served(attached["url"], settings) == read(ad.file)
    assert attached["scene"] is None
    last = api.get(f"/api/sessions/{session_id}/messages/").json()[-1]
    assert (last["role"], last["text"]) == ("agent", "Here's your ad!")


def test_an_ad_ffmpeg_cant_put_together_is_handed_back_as_a_failure_and_leaves_no_ad(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ffmpeg_fails(*arguments: str) -> None:
        raise assembly.AssemblyFailed("Output file #0 does not contain any stream")

    monkeypatch.setattr(assembly, "_ffmpeg", ffmpeg_fails)
    fake_model.respond(
        "produce",
        turn(calls=[("assemble_ad", {})]),
        turn(says="I couldn't put the ad together just now."),
    )

    say("Put the ad together")

    # A stated failure: the producer is told why, and ffmpeg's words are kept for the
    # developer.
    assert results_of("assemble_ad") == [
        "Failed: the clips and music couldn't be put together into the ad, as ffmpeg failed: "
        "Output file #0 does not contain any stream. Nothing was paid for, and every clip and "
        "the music are kept. Assembling the same clips again fails the same way."
    ]
    assert any(
        "ffmpeg couldn't put the ad together" in text and "does not contain any stream" in text
        for _, text in recorded(api, session_id)
    )
    # No half-made ad is left, and none was shown; the producer carried on to tell the owner.
    assert ads() == []
    assert ads_shown(api, session_id) == []
    assert chat(api, session_id)[-1] == ("agent", "I couldn't put the ad together just now.")


def test_an_ad_that_takes_too_long_to_put_together_is_a_failure_a_later_try_may_clear(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def too_slow(command: list[str], **_: Any) -> None:
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(
        assembly,
        "subprocess",
        SimpleNamespace(run=too_slow, TimeoutExpired=subprocess.TimeoutExpired),
    )
    fake_model.respond(
        "produce",
        turn(calls=[("assemble_ad", {})]),
        turn(says="I couldn't put the ad together just now."),
    )

    say("Put the ad together")

    # Not said to fail the same way again: a slow machine may finish it next time.
    assert results_of("assemble_ad") == [
        "Failed: the clips and music couldn't be put together into the ad, as ffmpeg failed: "
        "ffprobe took over 60 seconds. Nothing was paid for, and every clip and the music are "
        "kept."
    ]
    assert any("ffprobe took over 60 seconds" in text for _, text in recorded(api, session_id))
    assert ads() == []


def test_scenes_keep_whole_clips_even_when_transcript_has_quiet_margins(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.pause_seconds = 0.25
    finish(fake_model, steps, say)
    clips = list(ProducedItem.objects.filter(kind="clip").order_by("scene__number"))

    assemble(fake_model, say)

    (ad,) = ads()
    assert ad.seconds == sum(clip.seconds or 0 for clip in clips) + END_CARD_SECONDS
    assert [(cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [
        (0, clip.seconds) for clip in clips
    ]
    assert video(read(ad.file))[2] == pytest.approx(ad.seconds, abs=0.1)


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
    assert ads_shown(api, session_id) == []


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
    assert len(ads_shown(api, session_id)) == 1
    assert results_of("assemble_ad")[-1] == (
        "The ad was already assembled from these clips, overlays, this music and end card "
        "(version 1, 17 seconds) and shown to the shop owner, so nothing was made again. "
        "Assembling costs nothing."
    )


def test_the_music_plays_under_the_voice_and_is_cut_to_the_ads_length(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)

    (ad,) = ads()
    # Music covers every whole scene clip and the end card.
    music = Job.objects.get().produced.get(kind="music")
    assert (ad.made_from, music.seconds) == (music, 15 + END_CARD_SECONDS)
    heard = read(ad.file)
    assert video(heard)[2] == pytest.approx(17.0, abs=0.1)
    # The music is heard all the way through, and the voice is clearly louder than it.
    assert loudness(heard, "music", between=(0, 4.5)) > -40
    assert loudness(heard, "music", between=(4.5, 9)) > -40
    assert loudness(heard, "voice") - loudness(heard, "music") >= 10


def test_the_ad_isnt_assembled_before_the_music_is_made(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    finish(fake_model, steps, say, music=False)

    assemble(fake_model, say)

    assert results_of("assemble_ad") == [
        "Refused: the music hasn't been made yet, and it plays under the ad's voice. Create "
        "the music, then assemble the ad. Nothing was done."
    ]
    assert ads() == []


def test_new_music_gets_a_new_version_of_the_ad_and_the_old_one_is_kept(
    api: APIClient,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
    session_id: str,
) -> None:
    assemble(fake_model, say)
    fake_model.respond(
        "produce",
        turn(calls=[("create_music", {"mood": "calm piano"})]),
        turn(says="Calmer music's ready."),
    )
    say("Make the music calmer")

    assemble(fake_model, say)

    first, second = ads()
    upbeat, calm = Job.objects.get().produced.filter(kind="music").order_by("version")
    assert (first.made_from, second.made_from) == (upbeat, calm)
    # The same clips, cut the same way: only the music changed.
    assert second.cuts == first.cuts
    assert first.file != second.file
    assert len(ads_shown(api, session_id)) == 2
    assert results_of("assemble_ad")[-1].startswith("Assembled the ad (version 2, 17 seconds)")


def test_the_captions_show_the_script_in_time_with_the_voice(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Scene 1's line is heard a little differently from how it was written.
    fake_model.respond(
        "transcribe_line",
        {"text": "Meet the Stoneware Mug from Kiln and Co for your favourite tea."},
    )
    fake_model.respond(
        "check_words",
        {"verdict": "pass", "reason": "The spoken and written company name mean the same thing."},
    )
    finish(fake_model, steps, say)

    assemble(fake_model, say)

    (ad,) = ads()
    # Captions use the written script while their times follow the heard words.
    assert " ".join(each["text"] for each in ad.captions) == " ".join(
        Job.objects.get().scenes.order_by("number").values_list("line", flat=True)
    )
    assert ad.captions[0] == {"text": "Meet the Stoneware", "start": 0.0, "end": 1.5}
    scene_two = next(each for each in ad.captions if each["text"] == "Hand-thrown, holds 350")
    assert scene_two["start"] == 5.0
    assert drawn_in(read(ad.file), 7.0) == {"bottom"}


def test_each_scenes_overlay_shows_along_the_top_while_that_scene_plays(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)

    (ad,) = ads()
    assert [cut["overlay"] for cut in ad.cuts] == ["Kiln & Co", "", "$24.00"]
    heard = read(ad.file)
    # Scenes 1 and 3 have text along the top, scene 2 has none; the captions run along the
    # bottom throughout, and the middle of the picture is left alone.
    shows = [drawn_in(heard, middle) for middle in (2.0, 8.0, 13.0)]
    assert shows == [{"top", "bottom"}, {"bottom"}, {"top", "bottom"}]
    assert [colour_at(heard, middle) for middle in (2.0, 8.0, 13.0)] == ["red", "lime", "blue"]


def test_a_changed_overlay_gets_a_new_version_of_the_ad(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)
    # No tool changes an overlay yet, so it is changed underneath the producer.
    Job.objects.get().scenes.filter(number=2).update(overlay="Dishwasher safe")

    assemble(fake_model, say)

    first, second = ads()
    assert [cut["overlay"] for cut in first.cuts] == ["Kiln & Co", "", "$24.00"]
    assert [cut["overlay"] for cut in second.cuts] == ["Kiln & Co", "Dishwasher safe", "$24.00"]
    assert drawn_in(read(second.file), 8.0) == {"top", "bottom"}


def _without_a_price() -> dict[str, Any]:
    """PLAN as the planner gives it when the shop owner asked to leave the price out: no
    line, overlay or end card says one."""
    plan = {**PLAN["plan"], "scenes": [dict(scene) for scene in PLAN["plan"]["scenes"]]}
    plan["scenes"][-1].update(
        line="Get your Stoneware Mug from Kiln & Co today for your favourite tea.", overlay=None
    )
    return {**PLAN, "plan": {**plan, "end_card_price": ""}}


def test_the_ad_ends_on_its_end_card_held_for_its_seconds_after_the_last_scene(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)

    (ad,) = ads()
    job = Job.objects.get()
    assert ad.end_card == {
        "name": "Stoneware Mug",
        "line": "Hand-thrown, holds 350 ml, dishwasher safe.",
        "price": "$24.00",
        "website": assembly.website_of(job.product_url),
        "start": 15.0,
        "end": 15.0 + END_CARD_SECONDS,
        "file": job.photos.get(shows_product_colour=True).file,
    }
    shown = read(ad.file)
    # Held from the last scene's end to the ad's end.
    assert shows_the_end_card(shown, 15.1) and shows_the_end_card(shown, 16.9)
    assert not shows_the_end_card(shown, 14.5)
    assert "the end card from 15 to 17" in results_of("assemble_ad")[0]


@pytest.mark.parametrize("the_plan", [_without_a_price()])
def test_the_end_card_shows_no_price_when_the_plan_has_none(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)

    (ad,) = ads()
    assert ad.end_card is not None
    assert (ad.end_card["line"], ad.end_card["price"]) == (
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "",
    )


def test_an_ad_made_before_end_cards_is_made_again_with_one(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)
    ProducedItem.objects.filter(kind="finished_ad").update(end_card=None)

    assemble(fake_model, say)

    old, new = ads()
    assert (old.end_card, new.end_card is not None) == (None, True)


def test_a_changed_end_card_gets_a_new_version_of_the_ad(
    fake_model: FakeModel, finished: None, say: Callable[..., None]
) -> None:
    assemble(fake_model, say)
    Job.objects.update(end_card_price="")

    assemble(fake_model, say)

    first, second = ads()
    assert (first.end_card or {})["price"] == "$24.00"
    assert (second.end_card or {})["price"] == ""
    assert second.cuts == first.cuts


def test_an_end_card_with_no_usable_photo_is_drawn_without_one_and_says_so(
    api: APIClient,
    session_id: str,
    fake_model: FakeModel,
    finished: None,
    say: Callable[..., None],
) -> None:
    # Every photo shows a stranger's face, so none may go on the card.
    Job.objects.get().photos.update(has_face=True)

    assemble(fake_model, say)

    (ad,) = ads()
    assert ad.end_card is not None and ad.end_card["file"] == ""
    assert video(read(ad.file))[2] == pytest.approx(15 + END_CARD_SECONDS, abs=0.1)
    assert any("could go on the end card" in text for _, text in recorded(api, session_id))
