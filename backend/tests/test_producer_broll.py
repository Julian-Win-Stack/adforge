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
from agents.scene_forms import SPEED_LINE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelReply, ModelRequest
from jobs import work
from jobs.models import Job, ProducedItem, Scene

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
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

SHOWS = "Hot tea poured into the mug on a workbench."
# Scene 2's words: the plan's line adds "At home, every day." for 12 words, the fewest a
# line has in spec #401's plans.
LINE_2 = "Hand-thrown, holds 350 ml, and dishwasher safe too."

# The mug plan, with its second scene showing the mug rather than the person saying it.
BROLL_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            broll(
                {
                    **scene,
                    "line": LINE_2 + " At home, every day.",
                    "shows": SHOWS,
                    "kind": "broll",
                    "kind_why": "The inside of the mug needs a close view.",
                }
                if number == 2
                else scene
            )
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
    """Run the held steps, the producer replying once, when the last of them finishes."""
    if steps.held:
        fake_model.respond("produce", turn(says="Done."))
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
    """Draw a scene's picture through its scene agent without filming."""
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Picture prepared for review."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": scene, "instruction": "Prepare its picture.", "quotes": ["Go on"]},
        ),
    )
    run(fake_model, steps)


def made_ready(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scenes: tuple[int, ...]
) -> None:
    """Current scenes need no advance audio."""
    return


@pytest.fixture
def ready(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 2 has its audio, transcribed."""
    made_ready(fake_model, steps, say, (2,))


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    from .scene_script import make_scene_clip

    make_scene_clip(fake_model, steps, say)


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
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model, scenes=3)
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": n, "instruction": "Show the planned action.", "quotes": ["Go on"]},
            )
            for n in (1, 2, 3)
        ],
    )
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def test_a_scene_that_shows_the_product_plays_its_whole_clip(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    ad = assemble_through_the_chat(fake_model, steps, say)
    assert [(cut["scene"], cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [
        (1, 0.0, 5.0),
        (2, 0.0, 5.0),
        (3, 0.0, 5.0),
    ]
    assert colour_at(read(ad.file), 9.5) == "lime"


def test_a_scene_that_shows_the_product_has_its_words_captioned(assembled: ProducedItem) -> None:
    assert [c["text"] for c in assembled.captions if 5.0 <= c["start"] < 10.0] == [
        "Hand-thrown, holds 350",
        "ml, and dishwasher",
        "safe too. At",
        "home, every day.",
    ]
    assert drawn_in(read(assembled.file), 7.75) == {"bottom"}


@pytest.fixture
def unlabelled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """Scene 2 planned before the plan gave B-roll scenes their labels, its audio heard."""
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])
    made_ready(fake_model, steps, say, (2,))


# --- No starting picture ----------------------------------------------------------------------


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
    assert not handoffs("write_first_send_prompt")
    intro = str(handoffs("scene_agent")[0])
    assert all(value in intro for value in change.values())


def test_an_unlabelled_scene_is_made_from_its_agents_starting_picture(
    fake_model: FakeModel, unlabelled: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("starting_picture")[0]
    assert clips_asked("example_pictures") == [[]]


def test_a_clip_step_stopped_after_its_prompt_was_written_pays_for_it_once(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    (step_id,) = steps.held
    # The worker stops once the prompt is written, as the clip is asked for.
    with monkeypatch.context() as stopping:
        stopping.setattr(work, "submit_broll_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    # The worker starts again and hands the step out again.
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()

    assert paid_for().count("draw_scene_picture") == 1
    assert paid_for().count("make_broll_clip") == 1
    assert not handoffs("write_first_send_prompt")
    assert clips_asked("starting_picture")[0]
    assert clips_asked("example_pictures") == [[]]


# --- The clip ----------------------------------------------------------------------------------


def test_a_clip_as_long_as_its_line_carries_its_voice_throughout(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # 12 words at the fake voice's 2.4 a second: the 5-second clip comes back just as long.
    fake_model.words_per_second = 2.4
    Scene.objects.filter(number=2).update(
        line="Tea fills the mug, and its glaze shines right through every day."
    )
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert [silences(clip) for clip in kept_clips()] == [[]]


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
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    (step_id,) = steps.held
    with monkeypatch.context() as stopping:
        create = ProducedItem.objects.create

        def stop_after_the_clip(**kwargs: Any) -> ProducedItem:
            if kwargs.get("kind") == ProducedItem.Kind.CLIP:
                raise WorkerStopped
            return create(**kwargs)

        stopping.setattr(ProducedItem.objects, "create", stop_after_the_clip)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)


def test_a_clip_fetched_before_the_worker_stopped_isnt_paid_for_again(restarted: None) -> None:
    assert [purpose for purpose in paid_for() if purpose.endswith("_clip")] == [
        "make_broll_clip",
        "collect_broll_clip",
    ]


def test_a_clip_asked_of_the_old_boreal_before_the_switch_fails_once_collected(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    (step_id,) = steps.held

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

    tasks.carry_on_after_the_worker_starts()
    steps.run_held()

    assert not kept_clips()
    assert (
        "it was asked of creatify/boreal, a video model no longer used, so it can't be collected"
    ) in str(results_of("film"))
    assert paid_for().count("make_broll_clip") == 1
