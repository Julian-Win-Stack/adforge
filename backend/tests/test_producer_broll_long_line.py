"""A B-roll line whose real audio is too long for any clip, driven through the chat: the
audio step shortens it, fact checks it again and has the producer make its audio again,
before any clip is paid for, without telling the shop owner. A line still too long after 3
shortenings, or whose shorter line fails the fact check, becomes a talking scene, and the
chat says why. The producer's model and the scene models are faked at the gateway, but the
clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from agents import tasks
from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import HeldSteps, WorkerStopped, facts_ok, handoffs, paid_for
from .test_producer_broll import (
    BROLL_CHOICE,
    LINE_2,
    SHOWS,
    TALKING_CHOICE,
    calling,
    checked,  # noqa: F401 (a fixture)
    clips_asked,
    run,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# Scene 2's line, LINE_2, has 8 words. At this pace its audio takes 15.5 seconds: too long
# for Boreal-H3, which makes clips of at most 15.
TOO_SLOW = 8 / 15.5

# 4 words: 7.75 seconds at that pace, which an 8-second clip covers.
SHORTER = "Hand-thrown and dishwasher safe."

# Three shorter lines that still take 15.5 seconds to say, 8 words each.
STILL_LONG = [
    "Hand-thrown, holds 350 ml, and dishwasher safe, too.",
    "Thrown by hand, holds 350 ml, dishwasher safe.",
    "Handmade, it holds 350 ml, and dishwasher safe.",
]

SHORTENED = "Scene 2's line was shortened to fit its clip. Make its audio again."
NOW_TALKING = (
    "Scene 2 is now a talking scene. Make its starting picture again, then its audio and its clip."
)
NOTICE = (
    "Scene 2 couldn't be made as a product shot because its line is too long for a clip, so "
    "it will be said to camera instead."
)


def notices(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """Every notice in the chat since the script was checked, oldest first: its level and
    what it says. Reading the page may have posted some before."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    checked_at = next(
        i for i, message in enumerate(messages) if message["text"] == "The script is checked."
    )
    return [
        (message["level"], message["text"])
        for message in messages[checked_at:]
        if message["role"] == "notice"
    ]


def warnings() -> list[str]:
    """What the job's warnings say, since the page was read."""
    return [
        warning["text"]
        for warning in Job.objects.get().warnings
        if not warning["text"].startswith("Firecrawl")
    ]


def audio_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> str:
    """Make scene 2's audio. Gives what the producer was told when it finished."""
    calling(fake_model, say, ("make_line_audio", {"scene": 2}))
    run(fake_model, steps)
    step = SceneStep.objects.filter(kind="line_audio").last()
    assert step is not None
    return step.result


def since_planning() -> list[str]:
    """What was paid for since the script was checked, oldest first."""
    paid = paid_for()
    return paid[paid.index("fact_check") + 1 :]


@pytest.fixture
def too_long(fake_model: FakeModel, checked: None) -> None:  # noqa: F811
    """Scene 2's line, checked, takes the voice 15.5 seconds to say."""
    fake_model.words_per_second = TOO_SLOW


# --- Shortened, then made again ----------------------------------------------------------------


def test_a_line_too_long_for_its_clip_is_shortened_and_the_producer_told_to_make_its_audio_again(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER})
    fake_model.respond("fact_check", facts_ok(2))

    told = audio_of_scene_2(fake_model, steps, say)

    assert told == (
        "Background step finished: scene 2's line's audio takes 15.5 seconds to say, too long "
        f"for any clip. {SHORTENED}"
    )
    scene = Scene.objects.get(number=2)
    assert (scene.line, scene.shows, scene.fact_checked) == (SHORTER, SHOWS, True)


def test_the_line_is_shortened_to_14_seconds_at_its_real_pace(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER})
    fake_model.respond("fact_check", facts_ok(2))

    audio_of_scene_2(fake_model, steps, say)

    # 8 words took 15.5 seconds: 7 fit in 14.
    ((shortened, words),) = [(h["scene"], h["most_words"]) for h in handoffs("shorten_line")]
    assert (shortened, words) == (2, 7)


def test_the_shorter_line_is_fact_checked_again_with_what_the_scene_shows(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER})
    fake_model.respond("fact_check", facts_ok(2))

    audio_of_scene_2(fake_model, steps, say)

    assert handoffs("fact_check")[-1]["lines"] == [
        {"scene": 2, "line": SHORTER, "shows": SHOWS, "usage": None, "result": None}
    ]


def test_the_shop_owner_isnt_told_a_line_was_shortened(
    fake_model: FakeModel,
    too_long: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER})
    fake_model.respond("fact_check", facts_ok(2))

    told = audio_of_scene_2(fake_model, steps, say)

    assert "shop owner" not in told
    assert notices(api, session_id) == []
    assert warnings() == []


def test_no_clip_is_paid_for_until_the_audio_fits(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER})
    fake_model.respond("fact_check", facts_ok(2))
    calling(
        fake_model,
        say,
        ("make_starting_picture", {"scene": 2, "note": None}),
        ("make_line_audio", {"scene": 2}),
    )
    fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    run(fake_model, steps)
    # The producer, told to make its audio again, does, then transcribes it and makes its
    # clip from the starting picture it has.
    audio_of_scene_2(fake_model, steps, say)
    calling(fake_model, say, ("transcribe_line_audio", {"scene": 2}))
    run(fake_model, steps)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    assert since_planning() == [
        "choose_broll_picture",
        "make_starting_picture",
        # Checked before any clip is paid for.
        "check_starting_picture",
        "speak_line",
        "shorten_line",
        "fact_check",
        "speak_line",
        "transcribe_line",
        "make_broll_clip",
        "collect_broll_clip",
    ]
    # The shorter line takes 7.75 seconds to say.
    assert clips_asked("seconds") == [8]
    assert Scene.objects.get(number=2).status == "finished"


def test_a_line_shortened_before_the_worker_stopped_isnt_paid_for_again(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond("shorten_line", {"line": SHORTER}, {"line": STILL_LONG[0]})
    # The worker stops as the shorter line is fact checked.
    fake_model.respond("fact_check", WorkerStopped(), facts_ok(2))
    calling(fake_model, say, ("make_line_audio", {"scene": 2}))
    (step_id,) = steps.held
    with pytest.raises(WorkerStopped):
        steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert [p for p in since_planning() if p in ("speak_line", "shorten_line")] == [
        "speak_line",
        "shorten_line",
    ]
    assert Scene.objects.get(number=2).line == SHORTER
    assert SceneStep.objects.get(pk=step_id).result.endswith(SHORTENED)


# --- Becoming a talking scene ------------------------------------------------------------------


@pytest.fixture
def shortened_three_times(
    fake_model: FakeModel, too_long: None, steps: HeldSteps, say: Callable[..., None]
) -> str:
    """Scene 2's audio made four times, its line shortened three times and still too long.
    Gives what the producer was told the last time."""
    for line in STILL_LONG:
        fake_model.respond("shorten_line", {"line": line})
        fake_model.respond("fact_check", facts_ok(2))
    for _ in STILL_LONG:
        assert audio_of_scene_2(fake_model, steps, say).endswith(SHORTENED)
    return audio_of_scene_2(fake_model, steps, say)


def test_a_line_still_too_long_after_three_shortenings_becomes_a_talking_scene(
    shortened_three_times: str,
) -> None:
    assert shortened_three_times == (
        "Background step finished: scene 2's line's audio takes 15.5 seconds to say, too long "
        f"for any clip. {NOW_TALKING}"
    )
    assert paid_for().count("shorten_line") == 3
    scene = Scene.objects.get(number=2)
    # Its last line, which passed the fact check, said to camera.
    assert (scene.line, scene.shows) == (STILL_LONG[-1], "")


def test_the_chat_says_why_a_scene_became_a_talking_scene(
    shortened_three_times: str, api: APIClient, session_id: str
) -> None:
    assert notices(api, session_id) == [("info", NOTICE)]
    assert warnings() == [NOTICE]


def test_a_shorter_line_that_fails_the_fact_check_becomes_a_talking_scene(
    fake_model: FakeModel,
    too_long: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    fake_model.respond("shorten_line", {"line": "Hand-thrown, holds 500 ml."})
    fake_model.respond(
        "fact_check",
        {
            "decision": "checked",
            "reason": "The size in scene 2 isn't the page's.",
            "question": None,
            "lines": [
                {
                    "scene": 2,
                    "verdict": "wrong",
                    "wrong": "line",
                    "problem": "The mug holds 350 ml, not 500 ml.",
                    "page_says": "Holds 350 ml.",
                }
            ],
        },
    )

    told = audio_of_scene_2(fake_model, steps, say)

    assert told.endswith(NOW_TALKING)
    scene = Scene.objects.get(number=2)
    # The line that passed the fact check, said to camera; nobody is asked.
    assert (scene.line, scene.shows, scene.fact_checked) == (LINE_2, "", True)
    assert notices(api, session_id) == [("info", NOTICE)]


def test_the_producer_remakes_a_scene_that_became_talking_and_the_ad_finishes(
    fake_model: FakeModel,
    shortened_three_times: str,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.words_per_second = 2.0
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in (1, 2, 3)],
        *[("make_line_audio", {"scene": scene}) for scene in (1, 2, 3)],
    )
    fake_model.respond("choose_starting_picture", *[TALKING_CHOICE] * 3)
    run(fake_model, steps)
    # Scene 2's last audio was made for its line as it stands, so it is kept, not made again.
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))

    ad = Job.objects.get().produced.get(kind="finished_ad")
    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3]
    # Scene 2 was made as the person talking: no clip was asked of the B-roll video model.
    assert "make_broll_clip" not in paid_for()
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 1
