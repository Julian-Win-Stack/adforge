"""A scene that shows the product while the person's voice says its line, rather than the
person saying it to camera: its starting picture, its clip and the finished ad, driven
through the chat. The producer's model and the scene models are faked at the gateway, but
the clips are real tiny videos and ffmpeg runs for real."""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from adforge.file_store import read
from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelReply, ModelRequest
from jobs import work
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    WorkerStopped,
    broll,
    colour_at,
    drawn_in,
    handoffs,
    loudness,
    paid_for,
    results_of,
    silences,
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
            broll({**scene, "shows": SHOWS if number == 2 else None})
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

# Scene 2's line: its 7 words take the fake voice, at 2 a second, 3.5 seconds to say.
LINE_2 = "Hand-thrown, holds 350 ml, and dishwasher safe."


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


@pytest.fixture
def instructed(fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """What each model the fake stands in for was told to do, by its purpose, in turn."""
    told: defaultdict[str, list[str]] = defaultdict(list)
    answer = fake_model.complete

    def complete(request: ModelRequest[Any]) -> ModelReply[Any]:
        told[request.purpose].append(request.instructions)
        return answer(request)

    monkeypatch.setattr(fake_model, "complete", complete)
    return told


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


def picture_of(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scene: int
) -> None:
    """Make `scene`'s starting picture: scene 2 shows the mug, the others the person."""
    calling(fake_model, say, ("make_starting_picture", {"scene": scene, "note": None}))
    if scene == 2:
        fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    else:
        fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)


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


def clips_asked(field: str) -> list[Any]:
    """`field` of what the video model was asked for, for each clip, oldest first."""
    return [handoff[field] for handoff in handoffs("make_broll_clip")]


def kept_clips() -> list[bytes]:
    """Each clip kept, oldest first."""
    return [read(clip.file) for clip in ProducedItem.objects.filter(kind="clip").order_by("id")]


# --- The whole ad ------------------------------------------------------------------------------


@pytest.fixture
def assembled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    """The finished ad, made through the chat, with scene 2 showing the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


def assemble_through_the_chat(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def test_a_broll_scene_planned_before_it_had_labels_still_makes_its_ad(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A job planned before the plan gave each B-roll scene its kind, person, usage, result
    # and needs has them blank.
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])

    ad = assemble_through_the_chat(fake_model, steps, say)

    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3]
    assert len(clips_asked("motion_prompt")) == 1


def test_a_scene_that_shows_the_product_plays_in_its_turn(assembled: ProducedItem) -> None:
    assert [(cut["scene"], cut["start"], cut["end"]) for cut in assembled.cuts] == [
        (1, 0.0, 4.0),
        (2, 4.0, 7.5),
        (3, 7.5, 9.0),
    ]
    # Scene 2's clip, the second the video model made, is lime.
    assert colour_at(read(assembled.file), 5.75) == "lime"


def test_a_scene_that_shows_the_product_plays_its_whole_clip(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A second of silence before and after every line, as a real voice leaves.
    fake_model.pause_seconds = 1.0

    ad = assemble_through_the_chat(fake_model, steps, say)

    # Scene 2's clip lasts as long as its audio, 3.5 seconds of words and 2 of silence, and
    # all of it is kept, so its motion plays out. The talking scenes around it are cut to
    # their words.
    assert [(cut["scene"], cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [
        (1, 0.9, 5.1),
        (2, 0.0, 5.5),
        (3, 0.9, 3.1),
    ]
    assert colour_at(read(ad.file), 9.5) == "lime"


def test_a_scene_that_shows_the_product_has_its_words_captioned(assembled: ProducedItem) -> None:
    assert [c["text"] for c in assembled.captions if 4.0 <= c["start"] < 7.5] == [
        "Hand-thrown, holds 350",
        "ml, and",
        "dishwasher safe.",
    ]
    assert drawn_in(read(assembled.file), 5.75) == {"bottom"}


def test_a_scene_that_shows_the_product_has_its_voice_heard(assembled: ProducedItem) -> None:
    assert loudness(read(assembled.file), "voice", between=(4.5, 7.0)) > -40


# --- The starting picture ----------------------------------------------------------------------


def test_the_picture_is_planned_from_what_the_scene_shows(ready: None) -> None:
    (planned,) = handoffs("choose_broll_picture")
    assert (planned["scene"], planned["line"], planned["shows"]) == (
        2,
        "Hand-thrown, holds 350 ml, and dishwasher safe.",
        "Hot tea poured into the mug on a workbench.",
    )


@pytest.mark.parametrize(
    ("scene", "planner", "what_it_shows"),
    [
        pytest.param(
            1,
            "choose_starting_picture",
            "You plan the starting picture for one scene of a short vertical video ad. In the "
            "scene, the person in the portrait holds the product and says the scene's line to "
            "camera. The picture is made by a picture model from two pictures: the portrait "
            "first, then one product photo. A video model then animates it to say the line, so "
            "it is the scene's first frame.",
            id="the person talking",
        ),
        pytest.param(
            2,
            "choose_broll_picture",
            "You plan the starting picture for one scene of a short vertical video ad. The scene "
            "doesn't show the person talking to camera: it shows what the scene's \"shows\" "
            "describes, while the person's voice says the scene's line over it. The picture is "
            "made by a picture model from two pictures: the portrait first, then one product "
            "photo. A video model then animates it, with no sound, so it is the scene's first "
            "frame.",
            id="the product",
        ),
    ],
)
def test_the_model_planning_a_picture_is_told_what_the_scene_shows(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    scene: int,
    planner: str,
    what_it_shows: str,
) -> None:
    picture_of(fake_model, steps, say, scene)

    (told,) = instructed[planner]
    assert told.splitlines()[0] == what_it_shows


def test_the_model_planning_the_picture_is_told_to_make_nothing_up(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    picture_of(fake_model, steps, say, 2)

    (told,) = instructed["choose_broll_picture"]
    assert [rule for rule in told.splitlines() if rule.startswith("Make nothing up")] == [
        'Make nothing up, in either prompt. Show only what "shows" describes: no result, use, '
        "feature, texture, colour or amount it doesn't state, nothing that makes the product "
        "look bigger, better or more effective than described, and no part of the product the "
        "photos don't show."
    ]


def test_the_model_planning_the_picture_is_shown_the_portrait_and_the_photos_in_the_ads_colour(
    ready: None,
) -> None:
    job = Job.objects.get()
    # Photo 2 shows the mug in cream, and the ad's colour is sage green.
    assert ModelCall.objects.get(purpose="choose_broll_picture").images == [
        {"label": "The portrait", "key": job.produced.get(kind="portrait").file},
        {"label": "Photo 1", "key": job.photos.get(position=1).file},
    ]


def test_the_picture_is_asked_for_with_nothing_made_up(ready: None) -> None:
    (asked,) = handoffs("make_starting_picture")
    # Whatever the model wrote, the tool adds that nothing is to be made up.
    assert asked["prompt"] == (
        "The mug on a workbench in a sunny workshop, tea being poured into it. Show only what "
        "is described; add or change nothing about the product."
    )


def test_a_picture_planned_before_the_worker_stopped_isnt_paid_for_again(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    # A second plan, were the first wrongly paid for again.
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    # The worker stops once the plan is paid for, as the picture is made.
    fake_model.respond("make_starting_picture", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert [p for p in paid_for() if p in ("choose_broll_picture", "make_starting_picture")] == [
        "choose_broll_picture",
        "make_starting_picture",
    ]


def test_a_picture_made_before_the_scene_changed_what_it_shows_gets_no_clip(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The shop owner chose to have the person say scene 2 instead.
    scene = Scene.objects.get(number=2)
    scene.change_line(scene.line, shows="")
    scene.save()

    clip_of_scene_2(fake_model, steps, say)

    assert list(ModelCall.objects.filter(purpose="make_broll_clip")) == []
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

    # Planned again as the person saying it: the talking prompt, with nothing added.
    assert handoffs("make_starting_picture")[-1]["prompt"] == (
        "She holds the mug up beside her face, handle out, in her sunny workshop."
    )


# --- The clip ----------------------------------------------------------------------------------


def test_the_clip_is_asked_of_boreal_h3_on_creatify_from_its_starting_picture(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    starting_picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert (clips_asked("starting_picture"), clips_asked("example_pictures")) == (
        [starting_picture.file],
        [[]],
    )
    assert ModelCall.objects.filter(purpose="make_broll_clip").get().model == "creatify/boreal-h3"


@pytest.mark.parametrize(
    ("said_in", "asked_for"),
    [
        # Boreal-H3 makes 5 to 15 whole seconds: the fewest that cover the line, at least 5.
        pytest.param(3.5, 5, id="said in 3.5 seconds"),
        pytest.param(4.2, 5, id="said in 4.2 seconds"),
        pytest.param(5.0, 5, id="said in exactly 5 seconds"),
        pytest.param(6.3, 7, id="said in 6.3 seconds"),
        pytest.param(15.0, 15, id="said in exactly 15 seconds"),
    ],
)
def test_the_clip_is_asked_for_as_the_fewest_whole_seconds_that_cover_its_line(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    said_in: float,
    asked_for: int,
) -> None:
    # Scene 2's 7 words, said at whatever pace takes this long.
    fake_model.words_per_second = 7 / said_in
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("seconds") == [asked_for]
    assert [clip.seconds for clip in ProducedItem.objects.filter(kind="clip")] == [said_in]


def test_a_line_too_long_for_boreal_h3_fails_its_clip_before_anything_is_paid_for(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.words_per_second = 7 / 15.5
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert list(ModelCall.objects.filter(purpose="make_broll_clip")) == []
    step = SceneStep.objects.get(kind="clip")
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (its line takes 15.5 seconds to say, and the "
        "B-roll video model makes clips of at most 15 seconds).",
    )


def test_a_clip_creatify_rejects_fails_its_step_with_creatifys_reason(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.respond(
        "collect_clip",
        {"state": "failed", "error": "The prompt was flagged by the content checker."},
    )

    clip_of_scene_2(fake_model, steps, say)

    # What the producer is told, to pass on to the shop owner.
    step = SceneStep.objects.get(kind="clip")
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (The prompt was flagged by the content checker.).",
    )


def test_the_talking_scenes_clips_are_still_asked_of_heygen(assembled: ProducedItem) -> None:
    assert [
        (call.purpose, call.model)
        for call in ModelCall.objects.filter(purpose__endswith="_clip").order_by("created_at")
        if call.purpose.startswith("make_")
    ] == [
        ("make_talking_clip", "heygen/avatar-iv"),
        ("make_broll_clip", "creatify/boreal-h3"),
        ("make_talking_clip", "heygen/avatar-iv"),
    ]
    # Each says its line, as before.
    assert [sorted(handoff) for handoff in handoffs("make_talking_clip")] == [
        ["audio", "motion_prompt", "picture", "seconds"]
    ] * 2


def test_the_clip_is_asked_to_move_as_planned_with_nothing_made_up(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    # As it is: Boreal-H3 is sent no sections.
    assert clips_asked("prompt") == [
        "Steam rises as the tea fills the mug; the camera holds still. Show only what is "
        "described; add or change nothing about the product."
    ]


def test_the_kept_clip_carries_its_lines_voice(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    # The video model's clip is silent; the voice is heard all the way through the one kept.
    assert [silences(clip) for clip in kept_clips()] == [[]]


@pytest.mark.parametrize(
    ("line", "said_in"),
    [
        pytest.param(LINE_2, 3.5, id="cut from a longer clip"),
        # 4 words at the fake voice's 2 a second: the clip comes back just as long.
        pytest.param("Tea fills the mug.", 2.0, id="as long as the clip"),
    ],
)
def test_the_kept_clip_lasts_as_long_as_its_line(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    line: str,
    said_in: float,
) -> None:
    Scene.objects.filter(number=2).update(line=line)
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    kept = ProducedItem.objects.filter(kind="clip")
    assert [(clip.seconds, round(video(read(clip.file))[2], 1)) for clip in kept] == [
        (said_in, said_in)
    ]


def test_a_clip_shorter_than_its_line_fails_its_step(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Asked for 5 seconds, it comes back 3.
    fake_model.clips_short_by = 2.0

    clip_of_scene_2(fake_model, steps, say)

    step = SceneStep.objects.get(kind="clip")
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (it came back 3 seconds long, shorter than "
        "the line's 3.5 seconds of audio).",
    )


def test_a_clip_that_came_back_shorter_than_its_line_is_asked_for_afresh(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.clips_short_by = 2.0
    clip_of_scene_2(fake_model, steps, say)
    fake_model.clips_short_by = 0

    clip_of_scene_2(fake_model, steps, say)

    # Waiting on the short one, red, would only fail again: the one kept is the next, lime.
    assert [colour_at(clip, 1.0) for clip in kept_clips()] == ["lime"]


def the_worker_stops(*_: object, **__: object) -> None:
    raise WorkerStopped


@pytest.fixture
def restarted(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scene 2's clip step, run again after its worker stopped once the clip was fetched and
    the voice laid over it, as it was being kept."""
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)


def test_a_clip_fetched_before_the_worker_stopped_isnt_paid_for_again(restarted: None) -> None:
    assert [purpose for purpose in paid_for() if purpose.endswith("_clip")] == [
        "make_broll_clip",
        "collect_broll_clip",
    ]


def test_a_clip_fetched_before_the_worker_stopped_still_gets_its_voice(restarted: None) -> None:
    assert [silences(clip) for clip in kept_clips()] == [[]]


def test_a_clip_asked_of_the_old_boreal_before_the_switch_fails_once_collected(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    # The worker stops once the clip is asked for, before it is collected.
    with monkeypatch.context() as stopping:
        stopping.setattr(work, "collect_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    # It was asked of the old Boreal on fal, before Boreal-H3 replaced it.
    ModelCall.objects.filter(purpose="make_broll_clip").update(
        model="creatify/boreal",
        provider="fal",
        output={"video_id": "fal-request-1"},
    )
    fake_model.respond("produce", turn(says="It failed."))

    tasks.run_scene_step(step_id)

    step = SceneStep.objects.get(pk=step_id)
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (it was asked of creatify/boreal, a video "
        "model no longer used, so it can't be collected).",
    )
    assert paid_for().count("make_broll_clip") == 1
