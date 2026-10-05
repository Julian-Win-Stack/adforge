"""The early cut: a B-roll clip's picture plays whole, past the end of its line, while the
next line has already started over it, driven through the chat. The producer's model and the
scene models are faked at the gateway, but the clips are real tiny videos and ffmpeg runs
for real.

A B-roll clip is kept whole: its voice at its start and silence after. The fake video
model makes each as long as it is asked for, the fewest whole seconds that cover its line,
at least 5; set `clips_short_by` to have them come back shorter."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from gateway.fake import FakeModel, turn
from jobs.models import Job, ProducedItem

from .conftest import (
    NO_CHOICES,
    PLAN,
    HeldSteps,
    broll,
    colour_at,
    drawn_in,
    facts_ok,
    loudness,
    video,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

# The fake voice says 2 words a second. A B-roll line takes at least 4 seconds to say, so
# each scene showing the mug says 8 words. Scenes 1 and 3 have an overlay, scene 2 none. The
# first scene is always the person talking to camera.
SCENE_1, SCENE_2, SCENE_3 = PLAN["plan"]["scenes"]
# 8 words: 4 seconds.
SAID_OVER_2 = {**SCENE_2, "line": "Hand-thrown, holds 350 ml, and dishwasher safe too."}

# Showing the mug in scene 2: lines of 8, 8 and 3 words take 4, 4 and 1.5 seconds to say.
SHOWING_2 = [SCENE_1, SAID_OVER_2, SCENE_3]

# Two scenes showing the mug in a row need a fourth scene, the person talking after them:
# its lines of 8, 8, 8 and 6 words take 4, 4, 4 and 3 seconds to say.
FOUR_SCENES = [
    SCENE_1,
    SAID_OVER_2,
    {"line": "Pour, sip, and enjoy every single cup today.", "overlay": None},
    {"line": "Get yours today for just $24.00.", "overlay": "$24.00"},
]

# Ending on the mug: lines of 8, 7 and 8 words take 4, 3.5 and 4 seconds to say.
ENDING_ON_IT = [
    SCENE_1,
    SCENE_2,
    {**SCENE_3, "line": "Get yours today for just $24.00 from Kiln."},
]


def a_plan_showing(*shown: int, scenes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """PLAN, or PLAN with `scenes`, with `shown` showing the mug rather than the person
    saying their line."""
    return {
        **PLAN,
        "plan": {
            **PLAN["plan"],
            "scenes": [
                broll(
                    {**scene, "shows": "Hot tea poured into the mug." if number in shown else None}
                )
                for number, scene in enumerate(scenes or PLAN["plan"]["scenes"], start=1)
            ],
        },
    }


@pytest.fixture
def checked(
    fake_model: FakeModel, planned: None, the_plan: dict[str, Any], say: Callable[..., None]
) -> None:
    """A chat whose ad is planned, has its person, and whose lines passed the fact check."""
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", facts_ok(*range(1, len(the_plan["plan"]["scenes"]) + 1)))
    say("Make the person and check the script")


TALKING_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits the line.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug.",
}

BROLL_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze as the tea is poured.",
    "prompt": "The mug on a workbench in a sunny workshop, tea being poured into it.",
    "prompt_reason": "It shows the pour the line describes, in the person's workshop.",
    "motion_prompt": "Steam rises as the tea fills the mug; the camera holds still.",
    "motion_prompt_reason": "The pour and the steam are what moves in the scene.",
}


def calling(
    fake_model: FakeModel, say: Callable[..., None], *calls: tuple[str, dict[str, Any]]
) -> None:
    """Have the producer make these calls, then reply."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say("Go on")


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def assembled_with(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], broll: set[int]
) -> ProducedItem:
    """The finished ad, made through the chat, each of `broll`'s scenes showing the mug
    with its clip kept whole."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    scenes = tuple(Job.objects.get().scenes.order_by("number").values_list("number", flat=True))
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in scenes],
        *[("make_line_audio", {"scene": scene}) for scene in scenes],
    )
    for scene in scenes:
        if scene in broll:
            fake_model.respond("choose_broll_picture", BROLL_CHOICE)
        else:
            fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


Placed = tuple[tuple[float, float, float, float], tuple[float, float, float, float]]


def placed(ad: ProducedItem) -> list[Placed]:
    """Where each scene's picture plays, as (from, to in its clip, start, end in the ad),
    then its voice."""
    return [
        (
            (cut["clip_start"], cut["clip_end"], cut["start"], cut["end"]),
            (cut["voice_clip_start"], cut["voice_clip_end"], cut["voice_start"], cut["voice_end"]),
        )
        for cut in ad.cuts
    ]


def voice_has_no_gap(ad: ProducedItem, *, said_for: float) -> None:
    """Each line starts where the last one ends, and the voice is heard all the way through
    its `said_for` seconds."""
    ends = [0.0] + [cut["voice_end"] for cut in ad.cuts]
    assert [cut["voice_start"] for cut in ad.cuts] == ends[:-1]
    assert ends[-1] == said_for
    heard = file_store.read(ad.file)
    for second in range(int(said_for)):
        assert loudness(heard, "voice", between=(second + 0.1, second + 0.9)) > -40, second


def captions_follow_the_voice(ad: ProducedItem) -> None:
    """Every caption shows while its scene's line is said, whatever picture plays."""
    for cut in ad.cuts:
        line = (cut["voice_start"], cut["voice_end"])
        shown = [caption for caption in ad.captions if line[0] <= caption["start"] < line[1]]
        # The fake voice speaks from the start of its audio to its end.
        assert (shown[0]["start"], shown[-1]["end"]) == line
        assert all(caption["end"] <= line[1] for caption in shown)


@pytest.mark.parametrize("the_plan", [a_plan_showing(2, scenes=SHOWING_2)], indirect=True)
def test_talking_then_broll_then_talking(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Scene 2's 4 seconds of line are covered by a 5-second clip, the shortest made.
    ad = assembled_with(fake_model, steps, say, {2})

    assert ProducedItem.objects.get(kind="clip", scene__number=2).seconds == 5.0
    assert placed(ad) == [
        ((0.0, 4.0, 0.0, 4.0), (0.0, 4.0, 0.0, 4.0)),
        # The B-roll picture plays whole, to 9 seconds; its line ends at 8.
        ((0.0, 5.0, 4.0, 9.0), (0.0, 4.0, 4.0, 8.0)),
        # Scene 3's line starts at 8, over the B-roll's end, with all its sound; its
        # picture skips the second it is behind, so its lips match its words.
        ((1.0, 1.5, 9.0, 9.5), (0.0, 1.5, 8.0, 9.5)),
    ]
    assert ad.seconds == 9.5
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(9.5, abs=0.1)
    assert [colour_at(heard, at) for at in (2.0, 6.0, 8.75, 9.25)] == [
        "red",
        "lime",
        "lime",
        "blue",
    ]
    voice_has_no_gap(ad, said_for=9.5)
    captions_follow_the_voice(ad)
    # Scene 3's caption shows from when its line starts, over the B-roll's end.
    assert ad.captions[-1] == {"text": "Yours for $24.00.", "start": 8.0, "end": 9.5}
    # Scene 3's overlay shows while its picture plays, not while its line is said over
    # scene 2's picture, which has none.
    assert [drawn_in(heard, at) for at in (8.75, 9.25)] == [{"bottom"}, {"top", "bottom"}]


@pytest.mark.parametrize("the_plan", [a_plan_showing(2, 3, scenes=FOUR_SCENES)], indirect=True)
def test_talking_then_broll_then_broll_then_talking(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Scenes 2 and 3, of 4 seconds of line each, are each covered by a 5-second clip.
    ad = assembled_with(fake_model, steps, say, {2, 3})

    assert placed(ad) == [
        ((0.0, 4.0, 0.0, 4.0), (0.0, 4.0, 0.0, 4.0)),
        ((0.0, 5.0, 4.0, 9.0), (0.0, 4.0, 4.0, 8.0)),
        # Its line starts at 8, over the end of the first B-roll clip, but its picture
        # plays from its start, at 9: a second behind its line.
        ((0.0, 5.0, 9.0, 14.0), (0.0, 4.0, 8.0, 12.0)),
        # Two seconds behind by now: the talking scene skips that much of its picture, so
        # its lips match its words.
        ((2.0, 3.0, 14.0, 15.0), (0.0, 3.0, 12.0, 15.0)),
    ]
    assert ad.seconds == 15.0
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(15.0, abs=0.1)
    assert [colour_at(heard, at) for at in (8.75, 9.25, 13.75, 14.25)] == [
        "lime",
        "blue",
        "blue",
        "yellow",
    ]
    voice_has_no_gap(ad, said_for=15.0)
    captions_follow_the_voice(ad)
    # Scene 4's overlay shows once its picture plays, not while its line is said over
    # scene 3's.
    assert [drawn_in(heard, at) for at in (13.75, 14.25)] == [{"bottom"}, {"top", "bottom"}]


@pytest.mark.parametrize("the_plan", [a_plan_showing(3, scenes=ENDING_ON_IT)], indirect=True)
def test_ending_on_broll(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Scene 3's 4 seconds of line are covered by a 5-second clip, the shortest made.
    ad = assembled_with(fake_model, steps, say, {3})

    assert placed(ad)[-1] == ((0.0, 5.0, 7.5, 12.5), (0.0, 4.0, 7.5, 11.5))
    # The ad runs to the end of its last picture.
    assert ad.seconds == 12.5
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(12.5, abs=0.1)
    assert colour_at(heard, 12.0) == "blue"
    voice_has_no_gap(ad, said_for=11.5)
    captions_follow_the_voice(ad)
    # After the last line, the end of the clip plays over the music only, with its overlay.
    assert loudness(heard, "music", between=(11.6, 12.4)) > -40
    assert (
        loudness(heard, "voice", between=(11.6, 12.4))
        < loudness(heard, "voice", between=(7.6, 11.4)) - 30
    )
    assert drawn_in(heard, 12.0) == {"top"}
