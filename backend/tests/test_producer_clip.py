"""Each scene's clip: its starting picture animated to speak its line's audio, driven through
the chat. The producer's model and the video model are faked at the gateway. The clip is
made in the background: here the work is held until a test runs it, so a test can see what
the tool handed back before the clip is made, and what the producer was told once it was."""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django import Settings
from rest_framework.test import APIClient

from adforge.file_store import read
from adforge.retry import OutsideServiceDown
from agents import tasks
from agents.producer import MakeClip
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.scenes import CLIP_MOTION_PROMPT

from .conftest import (
    NO_CHOICES,
    HeldSteps,
    WorkerStopped,
    chat,
    facts_ok,
    given_to_the_producer,
    paid_for,
    producer_turns,
    results_of,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

CLIP_STARTED = "Started scene 1's clip. It isn't made yet: you'll be told when it's ready."
# The fake voice says scene 1's 8 words at 2 a second, so its clip lasts as long.
CLIP_READY = (
    "Background step finished: scene 1's clip is ready (version 1, 4 seconds), made from "
    "starting picture version 1 and audio version 1. Scene 1 is finished. Tell the shop owner."
)

CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits a line that names it.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug as the line does.",
}


def calling(fake_model: FakeModel, tool: str, arguments: dict[str, Any] | None = None) -> None:
    """Script the producer to call `tool` for scene 1, then reply."""
    fake_model.respond(
        "produce", turn(calls=[(tool, arguments or {"scene": 1})]), turn(says="On it.")
    )


def made(kind: str) -> list[tuple[int, int]]:
    """Each item of `kind` made, as (scene, version)."""
    return [
        (item.scene.number, item.version)
        for item in Job.objects.get().produced.filter(kind=kind).order_by("id")
        if item.scene is not None
    ]


def told() -> str:
    """The last step result the producer was given, as of its last turn."""
    results: list[str] = [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ]
    return results[-1]


def refused(why: str) -> str:
    """What a refused make_clip hands back, once nothing was started or paid for."""
    assert not SceneStep.objects.filter(kind="clip").exists()
    assert "make_clip" not in paid_for()
    return f"Refused: {why} Nothing was done."


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


@pytest.fixture
def pictured(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 has its starting picture."""
    calling(fake_model, "make_starting_picture", {"scene": 1, "note": None})
    say("Make scene 1's picture")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)


@pytest.fixture
def spoken(
    pictured: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """A chat whose scene 1 has its starting picture and its audio, not yet transcribed."""
    calling(fake_model, "make_line_audio")
    say("Make scene 1's audio")
    run(fake_model, steps)


@pytest.fixture
def ready(spoken: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 1 has its starting picture, and its audio, transcribed."""
    calling(fake_model, "transcribe_line_audio")
    say("Transcribe scene 1's audio")
    run(fake_model, steps)


@pytest.fixture
def clipped(ready: None, fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 1 has its clip."""
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    run(fake_model, steps)


# --- The whole scene, and the clip in the background -----------------------------------------


def test_a_whole_scene_is_made_and_each_step_is_paid_for_once(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(
            calls=[
                ("make_starting_picture", {"scene": 1, "note": None}),
                ("make_line_audio", {"scene": 1}),
            ]
        ),
        turn(says="I've started scene 1's picture and audio."),
    )
    say("Make scene 1")
    paid_before = len(paid_for())

    fake_model.respond("choose_starting_picture", CHOICE)
    fake_model.respond(
        "produce",
        turn(says="The picture is ready."),
        # Told the audio is ready, the producer has it transcribed; told what was heard, it
        # has the clip made.
        turn(calls=[("transcribe_line_audio", {"scene": 1})]),
        turn(says="The audio is ready, and I'm checking what was heard."),
        turn(calls=[("make_clip", {"scene": 1})]),
        turn(says="It was heard as written, so I'm making the clip."),
        turn(says="Scene 1 is finished!"),
    )
    steps.run_held()

    assert paid_for()[paid_before:] == [
        "choose_starting_picture",
        "make_starting_picture",
        "speak_line",
        "transcribe_line",
        "make_clip",
        "collect_clip",
    ]
    assert results_of("make_clip") == [CLIP_STARTED]
    assert made("clip") == [(1, 1)]
    assert Scene.objects.get(number=1).status == "finished"
    assert Scene.objects.get(number=2).status == "planned"
    assert told() == CLIP_READY


def test_the_clip_is_made_in_the_background_from_the_picture_and_the_audio_heard(
    api: APIClient,
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    calling(fake_model, "make_clip")
    messages_before = len(api.get(f"/api/sessions/{session_id}/messages/").json())

    say("Make scene 1's clip")

    # The tool handed back before the clip was made.
    assert results_of("make_clip") == [CLIP_STARTED]
    assert made("clip") == []
    assert Scene.objects.get(number=1).status == "planned"

    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind="clip")
    picture = ProducedItem.objects.get(kind="starting_picture")
    transcript = ProducedItem.objects.get(kind="transcript")
    # The same audio that was heard, and the clip lasts as long as it.
    assert (clip.picture, clip.made_from, clip.seconds) == (picture, transcript.made_from, 4.0)
    assert read(clip.file) == fake_model.clips["video-1"]
    assert Scene.objects.get(number=1).status == "finished"
    assert told() == CLIP_READY
    # The clip isn't shown: the shop owner sees only the producer's replies.
    after = api.get(f"/api/sessions/{session_id}/messages/").json()[messages_before:]
    assert [m["attachments"] for m in after] == [[], [], []]
    # The video model was asked once, and its records are charged to the tool call.
    submitted = ModelCall.objects.get(purpose="make_clip")
    assert submitted.handoff == {
        "picture": picture.file,
        "audio": transcript.made_from.file if transcript.made_from else None,
        "motion_prompt": CLIP_MOTION_PROMPT,
    }
    assert submitted.tool_call == SceneStep.objects.get(kind="clip").tool_call


def test_the_tool_takes_only_the_scene_so_the_clip_is_made_from_what_was_checked() -> None:
    assert list(MakeClip.model_json_schema()["properties"]) == ["scene"]


# --- What the tool refuses -------------------------------------------------------------------


def test_a_line_that_hasnt_passed_the_fact_check_gets_no_clip(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=1).update(fact_checked=False)
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's line hasn't passed the fact check, and nothing is made for a line "
            "until it has. Run the planning checks first."
        )
    ]


def test_a_clip_isnt_made_before_its_picture(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused("scene 1 has no starting picture yet. Make its starting picture first.")
    ]


def test_a_clip_isnt_made_before_its_audio(
    fake_model: FakeModel, pictured: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused("scene 1 has no audio yet. Make the line's audio first.")
    ]


def test_a_clip_isnt_made_from_audio_that_hasnt_been_heard(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's audio (version 1) hasn't been transcribed yet, and a clip is only made "
            "from audio that was heard saying the line. Transcribe it first."
        )
    ]


def test_a_clip_isnt_made_while_what_it_is_made_from_is_still_being_made(
    fake_model: FakeModel, spoken: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("transcribe_line_audio", {"scene": 1})]),
        turn(calls=[("make_clip", {"scene": 1})]),
        turn(says="I'll make the clip once it's heard."),
    )

    say("Transcribe scene 1's audio and make its clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's transcript is still being made. You'll be told when it's ready; make "
            "the clip then."
        )
    ]


def test_a_clip_already_being_made_isnt_started_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_clip", {"scene": 1})]),
        turn(calls=[("make_clip", {"scene": 1})]),
        turn(says="On it."),
    )

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        CLIP_STARTED,
        "Refused: scene 1's clip is already being made. You'll be told when it's ready. "
        "Nothing was done.",
    ]
    assert SceneStep.objects.filter(kind="clip").count() == 1


def test_a_changed_line_gets_no_clip_until_its_picture_and_audio_are_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=1).update(line="Say hello to the Stoneware Mug.")
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's starting picture was made for an earlier line, and the line has changed "
            "since. Make its starting picture again first."
        )
    ]


def test_audio_in_an_earlier_voice_gets_no_clip(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    voice = ProducedItem.objects.get(kind="voice")
    ProducedItem.objects.create(
        job=voice.job, kind="voice", version=2, voice_id="fake-voice-2", words_per_second=2.0
    )
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's audio was made in an earlier voice, and the person has changed since. "
            "Make the line's audio again first."
        )
    ]


# --- Versions, and nothing paid for twice ---------------------------------------------------


def test_a_clip_already_made_is_handed_back_and_charges_nothing(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    paid = paid_for()
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")

    assert paid_for() == paid
    assert steps.held == []
    # 4 seconds of video at $0.035 a second.
    assert results_of("make_clip")[-1] == (
        "Scene 1's clip was already made from this starting picture and audio (version 1), "
        "so nothing was made or paid for again. Making it cost $0.14. Scene 1 is finished."
    )


def test_a_new_starting_picture_gets_a_new_clip_and_the_old_clip_is_kept(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    first = ProducedItem.objects.get(kind="clip")
    # Made twice more, so the picture, the clip and the audio each have their own version.
    for note in ["Smiling more.", "Smiling even more."]:
        calling(fake_model, "make_starting_picture", {"scene": 1, "note": note})
        say(f"Make scene 1's picture again: {note}")
        fake_model.respond("choose_starting_picture", CHOICE)
        run(fake_model, steps)
    calling(fake_model, "make_clip")

    say("Make scene 1's clip again")
    run(fake_model, steps)

    assert made("clip") == [(1, 1), (1, 2)]
    second = ProducedItem.objects.get(kind="clip", version=2)
    assert second.picture == ProducedItem.objects.get(kind="starting_picture", version=3)
    assert second.made_from == first.made_from
    assert (read(first.file), read(second.file)) == (
        fake_model.clips["video-1"],
        fake_model.clips["video-2"],
    )
    assert paid_for().count("make_clip") == 2
    assert told() == (
        "Background step finished: scene 1's clip is ready (version 2, 4 seconds), made from "
        "starting picture version 3 and audio version 1. Scene 1 is finished. Tell the shop "
        "owner."
    )


def the_worker_stops(*_: object, **__: object) -> None:
    raise WorkerStopped


def test_a_clip_asked_for_before_the_worker_stopped_isnt_asked_for_again(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    (step_id,) = steps.held

    # The worker stops once the clip is asked for, as it waits for it.
    with monkeypatch.context() as stopping:
        stopping.setattr(work, "collect_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert fake_model.clips_submitted == ["video-1"]
    assert paid_for().count("make_clip") == 1
    assert SceneStep.objects.get(pk=step_id).status == "finished"
    assert read(ProducedItem.objects.get(kind="clip").file) == fake_model.clips["video-1"]


def test_a_clip_fetched_before_the_worker_stopped_isnt_fetched_again(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    (step_id,) = steps.held

    # The worker stops once the clip is fetched, as it is being kept.
    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert fake_model.clips_submitted == fake_model.clips_downloaded == ["video-1"]
    assert paid_for().count("collect_clip") == 1
    assert made("clip") == [(1, 1)]
    assert Scene.objects.get(number=1).status == "finished"


# --- When the video model fails ---------------------------------------------------------------


def clip_step_failed() -> SceneStep:
    step = SceneStep.objects.get(kind="clip")
    assert step.status == "failed"
    assert made("clip") == []
    assert Scene.objects.get(number=1).status == "planned"
    assert told() == (
        "Background step failed: scene 1's clip couldn't be made: "
        f"{step.reason} Tell the shop owner what went wrong."
    )
    return step


def test_a_clip_the_video_service_is_down_for_fails_its_step_and_the_producer_is_told(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("make_clip", *[OutsideServiceDown("HeyGen answered 503")] * 3)

    run(fake_model, steps)

    step = clip_step_failed()
    assert step.reason.startswith("an outside service stayed down after several tries")
    tries = ModelCall.objects.filter(purpose="make_clip")
    assert [(t.attempt, t.outcome) for t in tries] == [(1, "failed"), (2, "failed"), (3, "failed")]


def test_a_clip_the_video_model_couldnt_make_isnt_asked_for_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", {"state": "failed", "error": "No face was found."})

    run(fake_model, steps)

    step = clip_step_failed()
    assert step.reason == "the video model couldn't make the clip (No face was found.)."
    # Asking again would pay again, so it isn't retried.
    assert fake_model.clips_submitted == ["video-1"]
    assert ModelCall.objects.filter(purpose="collect_clip").count() == 1


def test_a_slow_clip_is_waited_for_and_the_shop_owner_told_it_is_still_being_made(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Settings,
    api: APIClient,
    session_id: str,
) -> None:
    # Every look counts as slow, so it's told of at the first, and only then.
    settings.CLIP_SLOW_AFTER_SECONDS = 0
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", {"state": "working"}, {"state": "working"})

    run(fake_model, steps)

    assert chat(api, session_id)[-3:] == [
        ("agent", "On it."),
        (
            "agent",
            "Scene 1's clip is taking longer than usual. It's still being made, and I'll "
            "tell you when it's ready.",
        ),
        ("agent", "Done."),
    ]
    assert told() == CLIP_READY
    assert fake_model.clips_submitted == ["video-1"]


def test_a_clip_given_up_on_while_heygen_was_down_is_waited_for_again_rather_than_paid_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", *[OutsideServiceDown("HeyGen answered 503")] * 3)
    run(fake_model, steps)
    clip_step_failed()
    calling(fake_model, "make_clip")

    say("Make scene 1's clip again")
    run(fake_model, steps)

    # The clip first asked for, and paid for, is the one kept.
    assert fake_model.clips_submitted == ["video-1"]
    assert paid_for().count("make_clip") == 1
    assert made("clip") == [(1, 1)]
    assert read(ProducedItem.objects.get(kind="clip").file) == fake_model.clips["video-1"]
    assert Scene.objects.get(number=1).status == "finished"


def test_a_clip_given_up_on_isnt_waited_for_once_its_picture_is_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", *[OutsideServiceDown("HeyGen answered 503")] * 3)
    run(fake_model, steps)
    calling(fake_model, "make_starting_picture", {"scene": 1, "note": "Smiling more."})
    say("Make scene 1's picture again, smiling more")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)
    calling(fake_model, "make_clip")

    say("Make scene 1's clip")
    run(fake_model, steps)

    # The clip given up on shows the old picture, so the new one is asked for.
    assert fake_model.clips_submitted == ["video-1", "video-2"]
    clip = ProducedItem.objects.get(kind="clip")
    assert (clip.picture.version if clip.picture else None, read(clip.file)) == (
        2,
        fake_model.clips["video-2"],
    )


def test_a_clip_the_video_model_couldnt_make_is_asked_for_afresh_when_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", {"state": "failed", "error": "No face was found."})
    run(fake_model, steps)
    clip_step_failed()
    calling(fake_model, "make_clip")

    say("Make scene 1's clip again")
    run(fake_model, steps)

    # Waiting on the one that failed would only fail again.
    assert fake_model.clips_submitted == ["video-1", "video-2"]
    assert read(ProducedItem.objects.get(kind="clip").file) == fake_model.clips["video-2"]


# --- A line that changes once its clip is made ---------------------------------------------


def test_a_finished_scene_whose_line_is_shortened_is_planned_again(
    fake_model: FakeModel, clipped: None, say: Callable[..., None]
) -> None:
    # The owner has since asked for a 5-second ad: the 9-second script runs over.
    Job.objects.update(target_seconds=5)
    fake_model.respond(
        "produce", turn(calls=[("run_planning_checks", NO_CHOICES)]), turn(says="Shorten it?")
    )
    say("Make it 5 seconds")
    fake_model.respond(
        "produce",
        turn(calls=[("run_planning_checks", {**NO_CHOICES, "length_choice": "shorten"})]),
        turn(says="Shortened."),
    )
    fake_model.respond(
        "shorten_script", {"lines": ["Meet this Stoneware Mug.", "Yours for $24.00, today."]}
    )
    fake_model.respond("fact_check", facts_ok(1, 2))

    say("Shorten it")

    # Its clip says the old line, so the scene needs a new one.
    scene = Scene.objects.get(number=1)
    assert (scene.line, scene.status) == ("Meet this Stoneware Mug.", "planned")
    assert made("clip") == [(1, 1)]
