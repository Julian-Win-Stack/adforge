"""The early cut: a B-roll clip's picture plays whole, past the end of its line, while the
next line has already started over it, driven through the chat. The producer's model and the
scene models are faked at the gateway, but the clips are real tiny videos and ffmpeg runs
for real.

A B-roll clip is kept whole: its voice at its start and silence after. The fake video
model makes each as long as it is asked for, the fewest whole seconds that cover its line,
at least 5. Two whole ads are rendered for the early cut: one with a talking scene after the
B-roll, and one ending on B-roll, whose length is the end of its last picture. Where each part
plays in the other shapes (two B-roll scenes in a row, a line said entirely over a B-roll
clip) is checked on `assembly.cuts` in test_assembly.py, and the overlay's timing on
`assembly.write_text_to_draw`."""

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
    facts_ok,
    loudness,
)

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = [pytest.mark.django_db(transaction=True)]

# The fake voice says 2 words a second. A B-roll line takes at least 4 seconds to say, so
# each scene showing the mug says 8 words. Scenes 1 and 3 have an overlay, scene 2 none. The
# first scene is always the person talking to camera.
SCENE_1, SCENE_2, SCENE_3 = PLAN["plan"]["scenes"]
# 8 words: 4 seconds.
SAID_OVER_2 = {**SCENE_2, "line": "Hand-thrown, holds 350 ml, and dishwasher safe too."}

# Showing the mug in scene 2: lines of 8, 8 and 3 words take 4, 4 and 1.5 seconds to say.
SHOWING_2 = [SCENE_1, SAID_OVER_2, SCENE_3]

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


def calling(
    fake_model: FakeModel, say: Callable[..., None], *calls: tuple[str, dict[str, Any]]
) -> None:
    """Have the producer make these calls, then reply."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say("Go on")


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once, when the last of them finishes."""
    if steps.held:
        fake_model.respond("produce", turn(says="Done."))
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
        if scene not in broll:
            fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)
    from .scene_script import script_scene_agents

    script_scene_agents(fake_model, scenes=len(broll))
    calling(
        fake_model,
        say,
        *[
            (
                "start_scene_agent",
                {"scene": scene, "instruction": "Show the planned action.", "quotes": ["Go on"]},
            )
            if scene in broll
            else ("make_clip", {"scene": scene})
            for scene in scenes
        ],
    )
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
