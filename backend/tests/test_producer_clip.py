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
from agents import loop, tasks
from agents.money import dollars
from agents.producer import PRODUCER
from chat.models import Message, Session
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import Fix, Job, ProducedItem, Scene, SceneStep
from jobs.planning import ChatMessage
from jobs.scenes import talking_motion_prompt

from .conftest import (
    NO_CHOICES,
    HeldSteps,
    WorkerStopped,
    a_plan_with,
    chat,
    facts_ok,
    given_to_the_producer,
    handoffs,
    paid_for,
    producer_turns,
    results_of,
    said_yes_to_a_fix,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

CLIP_STARTED = "Started scene 1's clip. It isn't made yet: you'll be told when it's ready."
# The fake voice says scene 1's 8 words at 2 a second, so its clip lasts as long.
CLIP_READY = (
    "Background step finished: scene 1's clip is ready (version 1, 4 seconds), made from "
    "starting picture version 1 and audio version 1. It cost $0.20. Scene 1 is finished. Tell "
    "the shop owner."
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
    assert "make_talking_clip" not in paid_for()
    return f"Refused: {why} Nothing was done."


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def clip_signals(api: APIClient, session_id: str) -> list[tuple[str, str, str, Any]]:
    """Each message in the chat that says a scene's clip started or ended, as (role, level,
    text, step)."""
    return [
        (message["role"], message["level"], message["text"], message["step"])
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        if message["step"] is not None and message["step"]["kind"] == "clip"
    ]


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
        "make_talking_clip",
        "collect_talking_clip",
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
    # The chat can tell the clip is being made.
    started = {"scene": 1, "kind": "clip", "state": "started"}
    assert clip_signals(api, session_id) == [("notice", "info", "Making scene 1's clip.", started)]

    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind="clip")
    picture = ProducedItem.objects.get(kind="starting_picture")
    transcript = ProducedItem.objects.get(kind="transcript")
    # The same audio that was heard, and the clip lasts as long as it.
    assert (clip.picture, clip.made_from, clip.seconds) == (picture, transcript.made_from, 4.0)
    assert read(clip.file) == fake_model.clips["video-1"]
    assert Scene.objects.get(number=1).status == "finished"
    assert told() == CLIP_READY
    # The clip is shown in the chat with its scene number, on a message with no words.
    after = api.get(f"/api/sessions/{session_id}/messages/").json()[messages_before:]
    shown = [m for m in after if m["attachments"]]
    assert [(m["role"], m["text"]) for m in shown] == [("agent", "")]
    assert shown[0]["attachments"] == [
        {"position": 1, "kind": "video", "url": f"/media/{clip.file}", "scene": 1}
    ]
    # The clip's own message says the step that started has ended with it.
    assert clip_signals(api, session_id)[1:] == [
        ("agent", "", "", {"scene": 1, "kind": "clip", "state": "made"})
    ]
    # The video model was asked once, and its records are charged to the tool call.
    submitted = ModelCall.objects.get(purpose="make_talking_clip")
    assert submitted.handoff == {
        "picture": picture.file,
        "audio": transcript.made_from.file if transcript.made_from else None,
        "seconds": 4.0,
        # The mug is handheld, so the motion holds the handheld pose its picture was made in.
        "motion_prompt": (
            "The person talks to the camera naturally, like a casual phone video. The person "
            "holds the product at chest height, beside their face, with any label facing the "
            "camera, so the top and bottom of the frame stay clear. Minimal hand movement."
        ),
    }
    assert submitted.tool_call == SceneStep.objects.get(kind="clip").tool_call


@pytest.mark.parametrize(
    ("product_size", "pose"),
    [
        pytest.param(
            "large",
            "The product stands on the floor at its real size, and the person stands beside "
            "it with one hand resting on it, framed so both fit, with space above the head "
            "and below the product, so the top and bottom of the frame stay clear.",
            id="large",
        ),
        pytest.param(
            "tiny",
            "The person holds the product up close to the camera between finger and thumb, "
            "so it fills a good part of the frame, with any label facing the camera, and "
            "the top and bottom of the frame stay clear.",
            id="tiny",
        ),
        pytest.param(
            "handheld",
            "The person holds the product at chest height, beside their face, with any label "
            "facing the camera, so the top and bottom of the frame stay clear.",
            id="handheld",
        ),
        # A job planned before the plan said the size: posed as handheld, as every job was.
        pytest.param(
            "",
            "The person holds the product at chest height, beside their face, with any label "
            "facing the camera, so the top and bottom of the frame stay clear.",
            id="no size stored",
        ),
    ],
)
def test_each_product_size_gives_the_talking_clip_the_pose_its_picture_was_made_in(
    product_size: str, pose: str
) -> None:
    """The video model is told the same pose the picture was made in, so it doesn't invent
    limbs to hold a chair beside a face that the picture shows standing on the floor. The
    chat test above uses the handheld mug, whose prompt is the one a job with no size gets:
    the test below proves the handoff carries the prompt for the job's own size."""
    assert talking_motion_prompt(product_size) == (
        f"The person talks to the camera naturally, like a casual phone video. {pose} "
        "Minimal hand movement."
    )


@pytest.mark.parametrize(
    "the_plan", [a_plan_with(product_size="large")], indirect=True, ids=["large"]
)
def test_a_talking_clips_motion_prompt_carries_the_pose_its_picture_was_planned_around(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")

    run(fake_model, steps)

    pose = (
        "The product stands on the floor at its real size, and the person stands beside it "
        "with one hand resting on it, framed so both fit, with space above the head and "
        "below the product, so the top and bottom of the frame stay clear."
    )
    (chose,) = handoffs("choose_starting_picture")
    assert chose["pose"] == pose
    submitted = ModelCall.objects.get(purpose="make_talking_clip")
    assert submitted.handoff["motion_prompt"] == (
        f"The person talks to the camera naturally, like a casual phone video. {pose} "
        "Minimal hand movement."
    )


# --- What the tool refuses -------------------------------------------------------------------
# A line that hasn't passed the fact check is refused by every scene tool alike: see
# test_producer_line_audio.py.


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


def test_a_clip_isnt_made_while_the_lines_audio_is_still_being_made(
    fake_model: FakeModel, pictured: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[("make_line_audio", {"scene": 1})]),
        turn(calls=[("make_clip", {"scene": 1})]),
        turn(says="I'll make the clip once the audio is ready."),
    )

    say("Make scene 1's audio and its clip")

    assert results_of("make_clip") == [
        refused(
            "scene 1's line's audio is still being made. You'll be told when it's ready; make "
            "the clip then."
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
            "scene 1's starting picture was made for an earlier line, or for what the scene "
            "showed before, and the scene has changed since. Make its starting picture again "
            "first."
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
    # 4 seconds of talking video at $0.05 a second.
    assert results_of("make_clip")[-1] == (
        "Scene 1's clip was already made from this starting picture and audio (version 1), "
        "so nothing was made or paid for again. Making it cost $0.20. Scene 1 is finished."
    )


def test_a_new_starting_picture_gets_a_new_clip_and_the_old_clip_is_kept(
    api: APIClient,
    fake_model: FakeModel,
    clipped: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    first = ProducedItem.objects.get(kind="clip")
    # Made twice more, so the picture, the clip and the audio each have their own version.
    for note in ["Smiling more.", "Smiling even more."]:
        said_yes_to_a_fix(1, note, "picture")
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
    assert paid_for().count("make_talking_clip") == 2
    # Both versions were shown, the newer later, each with its scene number.
    shown = [
        (message["seq"], attached["url"], attached["scene"])
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
        for attached in message["attachments"]
        if attached["kind"] == "video"
    ]
    assert [(url, scene) for _, url, scene in shown] == [
        (f"/media/{first.file}", 1),
        (f"/media/{second.file}", 1),
    ]
    assert shown[0][0] < shown[1][0]
    assert told() == (
        "Background step finished: scene 1's clip is ready (version 2, 4 seconds), made from "
        "starting picture version 3 and audio version 1. It cost $0.20. Scene 1 is finished. "
        "Scene 1's fix "
        f"has cost {dollars(Fix.objects.last().cost_usd())} so far. Tell the shop owner. The "  # type: ignore[union-attr]
        "ad will be assembled, and shown to the shop owner, once scenes 2 and 3 are finished."
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
    assert paid_for().count("make_talking_clip") == 1
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
    assert paid_for().count("collect_talking_clip") == 1
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
    api: APIClient,
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    session_id: str,
    say: Callable[..., None],
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("make_clip", *[OutsideServiceDown("fal answered 503")] * 3)

    run(fake_model, steps)

    step = clip_step_failed()
    assert step.reason.startswith("an outside service stayed down after several tries")
    tries = ModelCall.objects.filter(purpose="make_talking_clip")
    assert [(t.attempt, t.outcome) for t in tries] == [(1, "failed"), (2, "failed"), (3, "failed")]
    # The chat can tell the clip is no longer being made.
    assert clip_signals(api, session_id)[1:] == [
        (
            "notice",
            "problem",
            "Scene 1's clip couldn't be made.",
            {"scene": 1, "kind": "clip", "state": "not_made"},
        )
    ]


def test_what_the_chat_is_shown_of_each_scene_is_never_given_to_a_model(
    api: APIClient, fake_model: FakeModel, clipped: None, session_id: str
) -> None:
    session = Session.objects.get(pk=session_id)
    job = Job.objects.get()
    given_to_the_producer_now, _, _ = loop._conversation(PRODUCER, session)
    given_to_the_planners_now = work._conversation(job)
    # The chat was told of scene 1's picture and clip as each started and was made.
    shown = Message.objects.filter(step_scene__isnull=False)
    assert shown.count() == 4

    shown.delete()

    # The producer is given exactly what it was given without them.
    assert loop._conversation(PRODUCER, session)[0] == given_to_the_producer_now
    # The models that plan the ad read the picture and the clip as the producer's own
    # message with a file, as they read the portrait and the voice, and no notice.
    assert [each for each in given_to_the_planners_now if each not in work._conversation(job)] == [
        ChatMessage(by="producer", text="[Attached 1 picture]"),
        ChatMessage(by="producer", text="[Attached 1 video]"),
    ]


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
    assert ModelCall.objects.filter(purpose="collect_talking_clip").count() == 1


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

    assert chat(api, session_id)[-4:] == [
        ("agent", "On it."),
        (
            "agent",
            "Scene 1's clip is taking longer than usual. It's still being made, and I'll "
            "tell you when it's ready.",
        ),
        # The clip itself, shown with no words.
        ("agent", ""),
        ("agent", "Done."),
    ]
    assert told() == CLIP_READY
    assert fake_model.clips_submitted == ["video-1"]


def test_a_clip_given_up_on_while_heygen_was_down_is_waited_for_again_rather_than_paid_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", *[OutsideServiceDown("fal answered 503")] * 3)
    run(fake_model, steps)
    clip_step_failed()
    calling(fake_model, "make_clip")

    say("Make scene 1's clip again")
    run(fake_model, steps)

    # The clip first asked for, and paid for, is the one kept.
    assert fake_model.clips_submitted == ["video-1"]
    assert paid_for().count("make_talking_clip") == 1
    assert made("clip") == [(1, 1)]
    assert read(ProducedItem.objects.get(kind="clip").file) == fake_model.clips["video-1"]
    assert Scene.objects.get(number=1).status == "finished"


def test_a_clip_given_up_on_isnt_waited_for_once_its_picture_is_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, "make_clip")
    say("Make scene 1's clip")
    fake_model.respond("collect_clip", *[OutsideServiceDown("fal answered 503")] * 3)
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


def test_a_clip_given_up_on_is_waited_for_again_though_an_earlier_clip_was_fetched(
    fake_model: FakeModel, clipped: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The first clip, video-1, was fetched. The clip of a new picture is then given up on.
    said_yes_to_a_fix(1, "Smiling more.", "picture")
    calling(fake_model, "make_starting_picture", {"scene": 1, "note": "Smiling more."})
    say("Make scene 1's picture again, smiling more")
    fake_model.respond("choose_starting_picture", CHOICE)
    run(fake_model, steps)
    calling(fake_model, "make_clip")
    say("Make scene 1's clip again")
    fake_model.respond("collect_clip", *[OutsideServiceDown("fal answered 503")] * 3)
    run(fake_model, steps)
    calling(fake_model, "make_clip")

    say("Make scene 1's clip once more")
    run(fake_model, steps)

    # video-2, already paid for, is waited for rather than paid for again.
    assert fake_model.clips_submitted == ["video-1", "video-2"]


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
        "shorten_script",
        {
            "lines": [
                {"scene": 1, "line": "Meet this Stoneware Mug."},
                {"scene": 3, "line": "Yours for $24.00, today."},
            ]
        },
    )
    fake_model.respond("fact_check", facts_ok(1, 2))

    say("Shorten it")

    # Its clip says the old line, so the scene needs a new one.
    scene = Scene.objects.get(number=1)
    assert (scene.line, scene.status) == ("Meet this Stoneware Mug.", "planned")
    assert made("clip") == [(1, 1)]


def test_a_line_said_in_under_a_second_gets_a_clip_of_the_shortest_length_the_model_makes(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # One word, which the fake voice says in half a second.
    Scene.objects.filter(number=1).update(line="Welcome.")
    fake_model.respond("choose_starting_picture", CHOICE)
    tools: list[tuple[str, dict[str, Any]]] = [
        ("make_starting_picture", {"scene": 1, "note": None}),
        ("make_line_audio", {"scene": 1}),
        ("transcribe_line_audio", {"scene": 1}),
        ("make_clip", {"scene": 1}),
    ]
    for tool, arguments in tools:
        calling(fake_model, tool, arguments)
        say(f"Go on: {tool}")
        run(fake_model, steps)

    # The video model makes clips of at least a second.
    assert [handoff["seconds"] for handoff in handoffs("make_talking_clip")] == [1]
