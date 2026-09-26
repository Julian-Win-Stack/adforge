"""A scene that shows the product while the person's voice says its line, rather than the
person saying it to camera: its starting picture, its clip and the finished ad, driven
through the chat. The producer's model and the scene models are faked at the gateway, but
the clips are real tiny videos and ffmpeg runs for real."""

import math
from collections.abc import Callable
from typing import Any

import pytest

from adforge.file_store import read
from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.scenes import CLIP_MOTION_PROMPT, NOTHING_MADE_UP

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    WorkerStopped,
    colour_at,
    drawn_in,
    given_to_the_producer,
    handoffs,
    loudness,
    paid_for,
    producer_turns,
    results_of,
    video,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

SHOWS = "Hot tea poured into the mug on a workbench."

# The mug plan, with its second scene showing the mug rather than the person saying it.
BROLL_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            {**scene, "shows": SHOWS if number == 2 else None}
            for number, scene in enumerate(PLAN["plan"]["scenes"], start=1)
        ],
    },
}

TALKING_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits the line.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug.",
}

MOTION = "Steam rises as the tea fills the mug; the camera holds still."

BROLL_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze as the tea is poured.",
    "prompt": "The mug on a workbench in a sunny workshop, tea being poured into it.",
    "prompt_reason": "It shows the pour the line describes, in the person's workshop.",
    "motion_prompt": MOTION,
    "motion_prompt_reason": "The pour and the steam are what moves in the scene.",
}

# Scene 2's 7 words take the fake voice, at 2 a second, 3.5 seconds to say.
SCENE_2_SECONDS = 3.5


@pytest.fixture
def checked(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """A chat whose ad is planned with scene 2 showing the mug, has its person, and whose
    three scenes passed the fact check."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", BROLL_PLAN)
    say("Plan it")
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    assert Scene.objects.get(number=2).shows == SHOWS


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def calling(
    fake_model: FakeModel, say: Callable[..., None], *calls: tuple[str, dict[str, Any]]
) -> None:
    """Have the producer make these calls, then reply."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say("Go on")


def made_ready(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scenes: tuple[int, ...]
) -> None:
    """Make each of `scenes`' starting picture, audio and transcript."""
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in scenes],
        *[("make_line_audio", {"scene": scene}) for scene in scenes],
    )
    for scene in scenes:
        if scene == 2:
            fake_model.respond("choose_broll_picture", BROLL_CHOICE)
        else:
            fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)


@pytest.fixture
def ready(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 2 has its starting picture, and its audio, transcribed."""
    made_ready(fake_model, steps, say, (2,))


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)


def told() -> str:
    """The last step result the producer was given, as of its last turn."""
    results: list[str] = [
        each["text"]
        for each in given_to_the_producer(producer_turns())
        if each["kind"] == "step_finished"
    ]
    return results[-1]


# --- The whole ad ------------------------------------------------------------------------------


def test_an_ad_with_a_scene_that_shows_the_product_is_made_and_assembled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)

    calling(fake_model, say, ("assemble_ad", {}))

    # Scene 2's clip is asked for with no sound, for its line's whole length in seconds;
    # the others speak their audio.
    assert fake_model.clips_asked == [
        (4.0, True, CLIP_MOTION_PROMPT),
        (math.ceil(SCENE_2_SECONDS), False, f"{MOTION} {NOTHING_MADE_UP}"),
        (1.5, True, CLIP_MOTION_PROMPT),
    ]
    ad = Job.objects.get().produced.get(kind="finished_ad")
    # Scene 2 plays in turn, as long as its line, and its words are captioned and heard.
    assert [(cut["scene"], cut["start"], cut["end"]) for cut in ad.cuts] == [
        (1, 0.0, 4.0),
        (2, 4.0, 7.5),
        (3, 7.5, 9.0),
    ]
    assert [c["text"] for c in ad.captions if 4.0 <= c["start"] < 7.5] == [
        "Hand-thrown, holds 350",
        "ml, and",
        "dishwasher safe.",
    ]
    made = read(ad.file)
    assert colour_at(made, 5.75) == "lime"
    assert drawn_in(made, 5.75) == {"bottom"}
    assert loudness(made, "voice", between=(4.5, 7.0)) > -40


# --- The starting picture ----------------------------------------------------------------------


def test_the_picture_is_planned_from_what_the_scene_shows_and_asked_for_with_nothing_made_up(
    fake_model: FakeModel, ready: None
) -> None:
    (planned,) = handoffs("choose_broll_picture")
    assert (planned["scene"], planned["line"], planned["shows"]) == (
        2,
        "Hand-thrown, holds 350 ml, and dishwasher safe.",
        SHOWS,
    )
    assert "choose_starting_picture" not in paid_for()
    # Whatever the model wrote, the picture model is told to make nothing up.
    (asked,) = handoffs("make_starting_picture")
    assert asked["prompt"] == f"{BROLL_CHOICE['prompt']} {NOTHING_MADE_UP}"
    step = SceneStep.objects.get(kind="starting_picture")
    assert (step.shows, step.motion_prompt) == (SHOWS, MOTION)


def test_a_picture_made_before_the_scene_changed_what_it_shows_gets_no_clip(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The shop owner chose to have the person say scene 2 instead.
    scene = Scene.objects.get(number=2)
    scene.change_line(scene.line, shows="")
    scene.save()

    clip_of_scene_2(fake_model, steps, say)

    assert list(ModelCall.objects.filter(purpose="make_clip")) == []
    assert results_of("make_clip") == [
        "Refused: scene 2's starting picture was made for an earlier line, or for what the "
        "scene showed before, and the scene has changed since. Make its starting picture "
        "again first. Nothing was done."
    ]


def test_a_scene_that_no_longer_shows_the_product_gets_a_picture_of_the_person_talking(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    scene = Scene.objects.get(number=2)
    scene.change_line(scene.line, shows="")
    scene.save()
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)

    # Made again as the person saying it, from the talking scene's instructions.
    assert paid_for().count("choose_starting_picture") == 1
    assert [step.shows for step in SceneStep.objects.filter(kind="starting_picture")] == [
        SHOWS,
        "",
    ]


# --- The clip ----------------------------------------------------------------------------------


def test_the_clip_is_asked_for_with_no_sound_then_kept_with_the_voice_over_it(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    picture = ProducedItem.objects.get(kind="starting_picture")
    audio = ProducedItem.objects.get(kind="line_audio")
    assert ModelCall.objects.get(purpose="make_clip").handoff == {
        "picture": picture.file,
        "audio": None,
        "seconds": math.ceil(SCENE_2_SECONDS),
        "motion_prompt": f"{MOTION} {NOTHING_MADE_UP}",
    }
    clip = ProducedItem.objects.get(kind="clip")
    kept = read(clip.file)
    # The silent clip the video model made, with the line's audio laid over it and cut to
    # its length.
    assert (clip.seconds, clip.made_from, clip.picture) == (SCENE_2_SECONDS, audio, picture)
    assert video(kept)[2] == pytest.approx(SCENE_2_SECONDS, abs=0.1)
    assert loudness(fake_model.clips["video-1"], "voice") < -80
    assert loudness(kept, "voice") > -40
    assert colour_at(kept, 1.0) == "red"
    assert Scene.objects.get(number=2).status == "finished"


def test_a_clip_shorter_than_the_line_fails_its_step_and_is_asked_for_afresh(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.clips_short_by = 1.0
    clip_of_scene_2(fake_model, steps, say)

    step = SceneStep.objects.get(kind="clip")
    assert step.status == "failed"
    assert step.reason == (
        "the video model couldn't make the clip (it came back 3 seconds long, shorter than "
        "the line's 3.5 seconds of audio)."
    )
    assert told() == (
        "Background step failed: scene 2's clip couldn't be made: "
        f"{step.reason} Tell the shop owner what went wrong."
    )
    assert not ProducedItem.objects.filter(kind="clip").exists()
    assert Scene.objects.get(number=2).status == "planned"

    fake_model.clips_short_by = 0
    clip_of_scene_2(fake_model, steps, say)

    # Waiting on the short one would only fail again.
    assert fake_model.clips_submitted == ["video-1", "video-2"]
    assert Scene.objects.get(number=2).status == "finished"


def the_worker_stops(*_: object, **__: object) -> None:
    raise WorkerStopped


def test_a_clip_fetched_before_the_worker_stopped_is_kept_without_paying_again(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held

    # The worker stops once the clip is fetched and the voice laid over it, as it is kept.
    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)

    assert fake_model.clips_submitted == fake_model.clips_downloaded == ["video-1"]
    assert (paid_for().count("make_clip"), paid_for().count("collect_clip")) == (1, 1)
    clip = ProducedItem.objects.get(kind="clip")
    assert loudness(read(clip.file), "voice") > -40
    assert Scene.objects.get(number=2).status == "finished"
