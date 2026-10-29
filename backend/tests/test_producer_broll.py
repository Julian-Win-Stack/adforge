"""A scene that shows the product while the person's voice says its line, rather than the
person saying it to camera: its clip, made from the sent list cut-and-send builds and never
from a starting picture (spec #210 points 13-19), and the finished ad, driven through the
chat. The producer's model and the scene models are faked at the gateway, but
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
from jobs.first_send import SPEED_LINE
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import (
    FACTS_OK,
    FIRST_SEND_SENTENCE,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    WorkerStopped,
    broll,
    colour_at,
    drawn_in,
    handoffs,
    paid_for,
    results_of,
    silences,
    video,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

SHOWS = "Hot tea poured into the mug on a workbench."
# Scene 2's line: its 8 words take the fake voice, at 2 a second, 4 seconds to say, the
# least a B-roll line may take.
LINE_2 = "Hand-thrown, holds 350 ml, and dishwasher safe too."

# The mug plan, with its second scene showing the mug rather than the person saying it.
BROLL_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            broll({**scene, "line": LINE_2, "shows": SHOWS} if number == 2 else scene)
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

# Scene 2's labels when it shows the mug doing a job rather than at its best, as planned.
DOES_A_JOB: dict[str, Any] = {
    "broll_kind": "does a job",
    "person_shown": "no face",
    "usage": "Hot tea is poured into the mug from a teapot.",
    "result": "The mug is full of steaming tea.",
    "needs": [],
}


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
    """Make each of `scenes`' audio and transcript, and the talking ones' starting pictures:
    scene 2, which shows the mug, has none."""
    talking = [scene for scene in scenes if scene != 2]
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in talking],
        *[("make_line_audio", {"scene": scene}) for scene in scenes],
    )
    for _ in talking:
        fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)


@pytest.fixture
def ready(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 2 has its audio, transcribed."""
    made_ready(fake_model, steps, say, (2,))


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)


# What scene 2's clip is sent before the words (spec #210 points 13, 16): the speed line, the
# mug's front photo as Image 1 and the ad's room alone, as nobody is seen, each labelled.
SENT_BEFORE_THE_WORDS = [
    SPEED_LINE,
    "Image 1 is the product from the front, alone on a plain background.",
    "Image 2 is the room: it is the background.",
]


def sent_prompt(words: str = FIRST_SEND_SENTENCE) -> str:
    """The prompt scene 2's clip is sent with `words` after code's lines: the first-send
    writer's sentence, or the fixing agent's words on a try."""
    return "\n".join([*SENT_BEFORE_THE_WORDS, words])


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


def test_a_scene_that_shows_the_product_plays_its_whole_clip(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A second of silence before and after every line, as a real voice leaves.
    fake_model.pause_seconds = 1.0

    ad = assemble_through_the_chat(fake_model, steps, say)

    # Scene 2's audio, 4 seconds of words and 2 of silence, is covered by a 6-second clip,
    # and all of it is kept, so its motion plays out. The talking scenes around it are cut
    # to their words: the clip ends with its audio, so scene 3 skips none of its picture.
    assert [(cut["scene"], cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [
        (1, 0.9, 5.1),
        (2, 0.0, 6.0),
        (3, 0.9, 3.1),
    ]
    assert colour_at(read(ad.file), 9.5) == "lime"


def test_a_scene_that_shows_the_product_has_its_words_captioned(assembled: ProducedItem) -> None:
    assert [c["text"] for c in assembled.captions if 4.0 <= c["start"] < 8.0] == [
        "Hand-thrown, holds 350",
        "ml, and dishwasher",
        "safe too.",
    ]
    assert drawn_in(read(assembled.file), 5.75) == {"bottom"}


@pytest.fixture
def unlabelled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """Scene 2 planned before the plan gave B-roll scenes their labels, its audio heard."""
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])
    made_ready(fake_model, steps, say, (2,))


# --- No starting picture ----------------------------------------------------------------------


def test_a_scene_that_shows_the_product_gets_no_starting_picture(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Spec #210 point 19: the producer asking for one is told the clip is made without it.
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))

    assert steps.held == []
    assert results_of("make_starting_picture") == [
        "Refused: scene 2 shows the product, so it has no starting picture: its clip is made "
        "from the product's own pictures and videos. Make its clip once its audio is heard. "
        "Nothing was done."
    ]
    assert not {"choose_broll_picture", "make_starting_picture"} & set(paid_for())


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


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"broll_kind": "does a job", "result": "The mug is full."}, id="kind"),
        pytest.param({"usage": "Pour the tea in slowly."}, id="usage"),
    ],
)
def test_a_scene_whose_broll_labels_changed_gets_its_clip_written_from_them(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    change: dict[str, Any],
) -> None:
    # No starting picture to go stale (spec #210 point 19): the clip is written from the
    # scene as it stands.
    Scene.objects.filter(number=2).update(**change)

    clip_of_scene_2(fake_model, steps, say)

    assert paid_for().count("make_broll_clip") == 1
    (written,) = handoffs("write_first_send_prompt")
    assert {field: written["scene"][field] for field in change} == change


def test_an_unlabelled_scenes_clip_is_made_from_its_sent_list_too(
    fake_model: FakeModel, unlabelled: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("prompt") == [sent_prompt()]


def test_a_clip_step_stopped_after_its_prompt_was_written_pays_for_it_once(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    # The worker stops once the prompt is written, as the clip is asked for.
    with monkeypatch.context() as stopping:
        stopping.setattr(work, "submit_broll_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert [p for p in paid_for() if p in ("write_first_send_prompt", "make_broll_clip")] == [
        "write_first_send_prompt",
        "make_broll_clip",
    ]
    assert clips_asked("prompt") == [sent_prompt()]


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


@pytest.mark.parametrize(
    ("said_in", "asked_for"),
    [
        # Boreal-H3 makes 5 to 15 whole seconds: the fewest that cover the line, at least 5.
        # The rule itself, more lengths included, is checked without a chat in
        # test_producer_fixes.py (test_a_broll_clip_is_estimated_at_its_whole_seconds...).
        pytest.param(4.2, 5, id="4.2 seconds of audio in a 5-second clip"),
        pytest.param(6.3, 7, id="6.3 seconds of audio in a 7-second clip"),
    ],
)
def test_the_clip_is_asked_for_at_the_whole_seconds_that_cover_its_line_and_kept_whole(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    said_in: float,
    asked_for: int,
) -> None:
    # Scene 2's 8 words, said at whatever pace takes this long.
    fake_model.words_per_second = 8 / said_in
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("seconds") == [asked_for]
    # No timings in the prompt (spec #210 point 16): the seconds go in the call alone.
    assert clips_asked("prompt") == [sent_prompt()]
    # Kept whole, at the length the video model made it.
    (clip,) = ProducedItem.objects.filter(kind="clip")
    assert (clip.seconds, round(video(read(clip.file))[2], 1)) == (asked_for, asked_for)
    # The video model's clip is silent: the voice is heard from its start to the end of
    # the line, then it is silent to its end (give or take a frame of sound).
    ((silent_from, silent_to),) = silences(read(clip.file))
    assert (silent_from, round(silent_to)) == (said_in, asked_for)
    # The line's audio keeps its own length.
    assert clip.made_from is not None and clip.made_from.seconds == said_in


def test_a_clip_as_long_as_its_line_carries_its_voice_throughout(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # 10 words at the fake voice's 2 a second: the 5-second clip comes back just as long.
    Scene.objects.filter(number=2).update(
        line="Tea fills the mug, and its glaze shines right through."
    )
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert [silences(clip) for clip in kept_clips()] == [[]]


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
        "the line's 4 seconds of audio).",
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
    # Scene 2's 4 seconds of line, then silence to the end of its 5-second clip.
    ((clip,),) = [silences(clip) for clip in kept_clips()]
    assert (clip[0], round(clip[1])) == (4.0, 5)


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
