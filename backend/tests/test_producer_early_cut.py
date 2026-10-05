"""The early cut: a B-roll clip's picture plays whole, past the end of its line, while the
next line has already started over it, driven through the chat. The producer's model and the
scene models are faked at the gateway, but the clips are real tiny videos and ffmpeg runs
for real.

Until B-roll clips are kept whole, a clip is cut to its line when it is kept, so the clip is
lengthened in the file store here, as a kept-whole clip will be: its voice at its start and
silence after, its last frame held."""

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

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

# The fake voice says 2 words a second, so the plan's lines of 8, 7 and 3 words take 4,
# 3.5 and 1.5 seconds to say. Scenes 1 and 3 have an overlay, scene 2 none. The first scene
# is always the person talking to camera.

# Two scenes showing the mug in a row need a fourth scene, the person talking after them:
# its lines of 8, 7, 6 and 6 words take 4, 3.5, 3 and 3 seconds to say.
FOUR_SCENES = [
    *PLAN["plan"]["scenes"][:2],
    {"line": "Pour, sip, and enjoy every cup.", "overlay": None},
    {"line": "Get yours today for just $24.00.", "overlay": "$24.00"},
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


def kept_whole(scene: int, *, seconds: float) -> None:
    """Scene `scene`'s clip, kept at `seconds` rather than cut to its line, as a B-roll clip
    kept whole is: its voice at its start, then silence to its end."""
    clip = ProducedItem.objects.get(kind="clip", scene__number=scene)
    with tempfile.TemporaryDirectory() as folder:
        cut, whole = Path(folder) / "cut.mp4", Path(folder) / "whole.mp4"
        cut.write_bytes(file_store.read(clip.file))
        subprocess.run(
            [settings.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(cut)]
            + ["-vf", f"tpad=stop_mode=clone:stop_duration={seconds}"]
            + ["-af", f"apad=whole_dur={seconds}", "-t", str(seconds), str(whole)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        clip.file = file_store.save("clip.mp4", whole.read_bytes())
    clip.seconds = seconds
    clip.save()


def assembled_with(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    broll: dict[int, float],
) -> ProducedItem:
    """The finished ad, made through the chat, each of `broll`'s scenes showing the mug
    with its clip kept whole at the seconds given."""
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
    for scene, seconds in broll.items():
        kept_whole(scene, seconds=seconds)
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


@pytest.mark.parametrize("the_plan", [a_plan_showing(2)], indirect=True)
def test_talking_then_broll_then_talking(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    ad = assembled_with(fake_model, steps, say, {2: 4.5})

    assert placed(ad) == [
        ((0.0, 4.0, 0.0, 4.0), (0.0, 4.0, 0.0, 4.0)),
        # The B-roll picture plays whole, to 8.5 seconds; its line ends at 7.5.
        ((0.0, 4.5, 4.0, 8.5), (0.0, 3.5, 4.0, 7.5)),
        # Scene 3's line starts at 7.5, over the B-roll's end, with all its sound; its
        # picture skips the second it is behind, so its lips match its words.
        ((1.0, 1.5, 8.5, 9.0), (0.0, 1.5, 7.5, 9.0)),
    ]
    assert ad.seconds == 9.0
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(9.0, abs=0.1)
    assert [colour_at(heard, at) for at in (2.0, 6.0, 8.25, 8.75)] == [
        "red",
        "lime",
        "lime",
        "blue",
    ]
    voice_has_no_gap(ad, said_for=9.0)
    captions_follow_the_voice(ad)
    # Scene 3's caption shows from when its line starts, over the B-roll's end.
    assert ad.captions[-1] == {"text": "Yours for $24.00.", "start": 7.5, "end": 9.0}
    # Scene 3's overlay shows while its picture plays, not while its line is said over
    # scene 2's picture, which has none.
    assert [drawn_in(heard, at) for at in (8.25, 8.75)] == [{"bottom"}, {"top", "bottom"}]


@pytest.mark.parametrize("the_plan", [a_plan_showing(2, 3, scenes=FOUR_SCENES)], indirect=True)
def test_talking_then_broll_then_broll_then_talking(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    ad = assembled_with(fake_model, steps, say, {2: 4.5, 3: 3.5})

    assert placed(ad) == [
        ((0.0, 4.0, 0.0, 4.0), (0.0, 4.0, 0.0, 4.0)),
        ((0.0, 4.5, 4.0, 8.5), (0.0, 3.5, 4.0, 7.5)),
        # Its line starts at 7.5, over the end of the first B-roll clip, but its picture
        # plays from its start, at 8.5: a second behind its line.
        ((0.0, 3.5, 8.5, 12.0), (0.0, 3.0, 7.5, 10.5)),
        # A second and a half behind by now: the talking scene skips that much more of its
        # picture, so its lips match its words.
        ((1.5, 3.0, 12.0, 13.5), (0.0, 3.0, 10.5, 13.5)),
    ]
    assert ad.seconds == 13.5
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(13.5, abs=0.1)
    assert [colour_at(heard, at) for at in (8.25, 8.75, 11.75, 12.25)] == [
        "lime",
        "blue",
        "blue",
        "yellow",
    ]
    voice_has_no_gap(ad, said_for=13.5)
    captions_follow_the_voice(ad)
    # Scene 4's overlay shows once its picture plays, not while its line is said over
    # scene 3's.
    assert [drawn_in(heard, at) for at in (11.75, 12.25)] == [{"bottom"}, {"top", "bottom"}]


@pytest.mark.parametrize("the_plan", [a_plan_showing(3)], indirect=True)
def test_ending_on_broll(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    ad = assembled_with(fake_model, steps, say, {3: 5.0})

    assert placed(ad)[-1] == ((0.0, 5.0, 7.5, 12.5), (0.0, 1.5, 7.5, 9.0))
    # The ad runs to the end of its last picture.
    assert ad.seconds == 12.5
    heard = file_store.read(ad.file)
    assert video(heard)[2] == pytest.approx(12.5, abs=0.1)
    assert colour_at(heard, 12.0) == "blue"
    voice_has_no_gap(ad, said_for=9.0)
    captions_follow_the_voice(ad)
    # After the last line, the end of the clip plays over the music only, with its overlay.
    assert loudness(heard, "music", between=(9.5, 12.4)) > -40
    assert (
        loudness(heard, "voice", between=(9.5, 12.4))
        < loudness(heard, "voice", between=(7.6, 8.9)) - 30
    )
    assert drawn_in(heard, 12.0) == {"top"}
