"""Changing what a finished scene says (route B step 1, #138; fix redesign, ticket 08): a plan
the shop owner said yes to gives the scene its new line, and code makes its audio, transcript
and clip again from the starting picture it has (Julian, 2026-10-17), with no note and no
producer turn (tests/fixing.py); when
its clip lands the ad is assembled again by code. What a new line is estimated to cost. The
scene models are faked at the gateway; the clips are real tiny videos."""

from decimal import Decimal

import pytest

from agents.money import dollars
from agents.producer import Estimate, _estimate, _picture_only_estimate_usd
from gateway.catalog import MODEL_FOR_PURPOSE, transcription_cost_usd
from gateway.fake import FakeModel
from jobs.models import Fix, FixPlan, ProducedItem, Scene, SceneStep

from .conftest import JUDGED_CLIP, HeldSteps, facts_ok, paid_for, producer_turns
from .fixing import fixed, plan_row, planned
from .test_fix_loop import TURN_REWRITE, latest
from .test_producer_broll import (
    IMAGE_1,
    SHOWS,
    TALKING_CHOICE,
    checked,  # noqa: F401 (a fixture)
    clips_asked,
)
from .test_producer_fixes import (
    ads,
    fix_of,
    last_step,
    row_of,
    shown,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_3 = "The last scene should say it's on sale, not just the price."
LINE_3 = "On sale now for $19.00."
NOTE_2 = "Scene 2 should say it's oven safe too."
LINE_2_NEW = "Hand-thrown, holds 350 ml, oven and dishwasher safe."
OLD_LINE_3 = "Yours for $24.00."
MUG_UP = "She holds the mug up beside her face."
SLOWER_2 = "The mug turns more slowly."


def new_line(scene: int, line: str, note: str = NOTE_3) -> FixPlan:
    """The shop owner said yes to a plan giving `scene` the new `line`: it is changed, and the
    scene's first steps are queued."""
    return fixed(scene, "new_line", note=note, change="Say it as asked.", line=line)


def made_again(steps: HeldSteps) -> None:
    """The scene's audio, its transcript, then its clip, each run as the worker runs them,
    with no producer turn: no new picture is chosen."""
    steps.run_held()


def versions(scene: int) -> dict[str, list[int]]:
    """Each of the scene's kinds of item, by version, oldest first."""
    made: dict[str, list[int]] = {}
    for kind, version in (
        ProducedItem.objects.filter(scene__number=scene)
        .order_by("version")
        .values_list("kind", "version")
    ):
        made.setdefault(kind, []).append(version)
    return made


def captioned(ad: ProducedItem) -> str:
    return " ".join(caption["text"] for caption in ad.captions)


# --- The whole change -------------------------------------------------------------------------


def test_a_talking_scenes_new_line_remakes_only_that_scene_and_a_new_ad(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    untouched = {1: versions(1), 2: versions(2)}
    turns = producer_turns()

    plan = new_line(3, LINE_3)
    scene = Scene.objects.get(number=3)
    assert (scene.line, scene.status, scene.fact_checked) == (LINE_3, "planned", True)

    made_again(steps)

    assert versions(3) == {
        "starting_picture": [1],
        "line_audio": [1, 2],
        "transcript": [1, 2],
        "clip": [1, 2],
    }
    assert {1: versions(1), 2: versions(2)} == untouched
    assert Scene.objects.get(number=3).status == "finished"
    fix = fix_of(plan, 3)
    assert {step.kind for step in fix.steps.all()} == {"line_audio", "transcript", "clip"}
    assert all(step.note == "" for step in fix.steps.all())
    assert latest("clip", 3).picture == latest("starting_picture", 3)
    assert producer_turns() == turns
    # The fix's clip landing assembled the new ad, captioned with the new words.
    assert ads() == [1, 2]
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert "On sale now" in captioned(ad)
    assert "Yours for" not in captioned(ad)
    assert ad.cuts[2]["clip"] == latest("clip", 3).pk


def test_a_line_change_pays_only_for_the_scenes_steps_and_no_judge_checks_the_fix(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    paid = len(paid_for())

    new_line(3, LINE_3)
    made_again(steps)

    # The step 6 judge doesn't check a change of words (#131), and the picture is kept.
    assert paid_for()[paid:] == [
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]


def test_a_broll_scenes_new_line_keeps_what_it_shows_and_its_labels(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    scene = Scene.objects.get(number=2)
    labels = (scene.shows, scene.broll_kind, scene.person_shown, scene.usage, scene.result)

    plan = new_line(2, LINE_2_NEW, NOTE_2)
    made_again(steps)

    scene = Scene.objects.get(number=2)
    assert scene.line == LINE_2_NEW
    assert (scene.shows, scene.broll_kind, scene.person_shown, scene.usage, scene.result) == (
        labels
    )
    assert scene.shows == SHOWS
    assert last_step("clip", 2).fix == fix_of(plan, 2)
    assert ads() == [1, 2]


def test_a_broll_scenes_new_line_pays_for_its_steps_the_judge_and_no_before_picture_or_fix_check(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    paid = len(paid_for())

    new_line(2, LINE_2_NEW, NOTE_2)
    made_again(steps)

    # The picture is kept; the new clip is judged as any B-roll clip is, and a new line isn't
    # checked against the fix (#131).
    assert paid_for()[paid:] == [
        "speak_line",
        "transcribe_line",
        "make_broll_clip",
        "collect_broll_clip",
        *JUDGED_CLIP,
    ]
    # The new clip is in reference mode, the starting picture it kept as Image 1.
    assert clips_asked("starting_picture")[-1] is None
    assert clips_asked("example_pictures")[-1] == [latest("starting_picture", 2).file]
    assert latest("starting_picture", 2).version == 1
    assert clips_asked("prompt")[-1].startswith(IMAGE_1)


def test_a_new_broll_line_too_long_to_say_cant_be_made(
    shown: ProducedItem,  # noqa: F811
) -> None:
    # 30 words: 15 seconds at the fake voice's 2 words a second, over the 14 a scene showing
    # the product can say. (A talking scene's limit: test_run_plan.)
    long_line = " ".join(["Really"] * 29 + ["great."])

    plan = planned(plan_row(2, "new_line", change="Longer.", line=long_line), note=NOTE_2)

    assert row_of(plan, 2).cant == (
        "Scene 2's new line takes about 15 seconds to say, longer than the scene can last "
        "(14 seconds). Send a shorter line."
    )
    assert Scene.objects.get(number=2).line != long_line


# --- Another plan on the scene while its new line is made --------------------------------------


def test_a_new_line_said_yes_to_while_the_scene_is_being_made_waits_for_it(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    first = new_line(3, LINE_3)
    assert {step.line for step in SceneStep.objects.filter(pk__in=steps.held)} == {LINE_3}

    newer = new_line(3, "Half price today.")

    # The scene is being made for the first line, so the newer one waits for it.
    assert Scene.objects.get(number=3).line == LINE_3
    assert not SceneStep.objects.filter(plan_scene__plan=newer).exists()

    steps.run_held()

    assert row_of(first, 3).landed_at is not None
    assert row_of(newer, 3).landed_at is not None
    scene = Scene.objects.get(number=3)
    assert (scene.line, scene.status) == ("Half price today.", "finished")
    assert {
        step.fix for step in SceneStep.objects.filter(scene__number=3, line="Half price today.")
    } == {fix_of(newer, 3)}
    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None
    assert [cut["clip"] for cut in ad.cuts][2] == latest("clip", 3).pk
    assert "Half price today" in captioned(ad)


def test_a_picture_fix_said_yes_to_while_a_new_line_is_made_is_made_on_the_new_line(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    new_line(3, LINE_3)
    picture = fixed(3, "new_picture", note="And she should hold the mug up.", change=MUG_UP)

    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    steps.run_held()

    step = last_step("starting_picture", 3)
    assert (step.line, step.note, step.fix) == (LINE_3, MUG_UP, fix_of(picture, 3))
    assert last_step("clip", 3).line == LINE_3
    assert Scene.objects.get(number=3).status == "finished"


def test_a_clip_fix_said_yes_to_while_a_new_line_is_made_is_made_on_the_new_line_with_its_note(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    line = new_line(2, LINE_2_NEW, NOTE_2)
    clip = fixed(2, "new_clip", note="And slower.", change=SLOWER_2)

    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    steps.run_held()

    made = last_step("clip", 2)
    assert (made.line, made.note, made.fix) == (LINE_2_NEW, SLOWER_2, fix_of(clip, 2))
    # Both keep the scene's starting picture.
    assert last_step("starting_picture", 2).fix is None
    assert {step.fix for step in SceneStep.objects.filter(kind="clip", fix__isnull=False)} == {
        fix_of(line, 2),
        fix_of(clip, 2),
    }
    assert Scene.objects.get(number=2).status == "finished"


# --- A new line whose audio runs too long -------------------------------------------------------


def test_a_new_broll_line_whose_real_audio_runs_too_long_is_shortened_and_made(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    # The line fits at the voice's measured speed, so the plan takes it; a line too long at
    # that speed can't be made instead. Only audio said slower than measured reaches the
    # shortening: here its 8 words take 15.5 seconds, too long for any clip.
    plan = new_line(2, LINE_2_NEW, NOTE_2)
    fake_model.words_per_second = 8 / 15.5
    fake_model.respond("shorten_line", {"line": "Hand-thrown, oven and dishwasher safe."})
    fake_model.respond("fact_check", facts_ok(2))

    steps.run_held()

    too_long = SceneStep.objects.filter(kind="line_audio", scene__number=2, line=LINE_2_NEW).get()
    assert too_long.result == (
        "Background step finished: scene 2's line's audio takes 15.5 seconds to say, too long "
        f"for any clip. It cost {dollars(too_long.tool_call.cost_usd())}. Scene 2's line was "
        "shortened to fit its clip. It isn't the line the shop "
        'owner said yes to: it is now "Hand-thrown, oven and dishwasher safe." Tell the shop '
        "owner. Make its audio again."
    )
    scene = Scene.objects.get(number=2)
    assert scene.line == "Hand-thrown, oven and dishwasher safe."
    assert scene.shortened_from == [LINE_2_NEW]
    assert scene.shows == SHOWS
    # Code made the shortened line's audio, then the rest of the scene, for the plan.
    assert last_step("clip", 2).line == "Hand-thrown, oven and dishwasher safe."
    assert scene.status == "finished"
    assert row_of(plan, 2).landed_at is not None


# --- The estimate -----------------------------------------------------------------------------


def line_estimate(scene: int, line: str) -> Estimate:
    """A new line's estimate, as a plan sets it: the line in quotes, as today's line fix."""
    return _estimate(Scene.objects.get(number=scene), Fix.Redo.LINE, f'"{line}"')


def test_a_line_fix_is_estimated_from_the_new_line(
    shown: ProducedItem,  # noqa: F811
) -> None:
    # The new line's 23 characters at Inworld's $25 a million; its 5 words, 2.5 seconds at the
    # fake voice's speed, heard at Scribe's $0.22 an hour and spoken by HeyGen at $0.05 a
    # second. The scene keeps its picture, so nothing is paid for one.
    expected = (
        Decimal("0.000575")
        + Decimal("2.5") * Decimal("0.22") / 3600
        + Decimal("2.5") * Decimal("0.05")
    ).quantize(Decimal("0.000001"))

    assert line_estimate(3, LINE_3).about == expected


def test_a_line_fix_at_an_uneven_voice_speed_is_estimated_to_the_millionth_of_a_dollar(
    shown: ProducedItem,  # noqa: F811
) -> None:
    # The voice was measured at 2.2 words a second, so the new line's 5 words take 2.2727...
    # seconds: HeyGen's $0.05 a second and Scribe's $0.22 an hour times that never end. On
    # top of the line's 23 characters at $0.000575. A talking clip isn't judged, so it is
    # never made again.
    ProducedItem.objects.filter(kind="voice").update(words_per_second=2.2)

    estimate = line_estimate(3, LINE_3)

    assert estimate == (Decimal("0.114350"), Decimal("0.114350"))
    assert dollars(estimate.about) == "$0.11435"


@pytest.mark.parametrize(
    ("seconds", "cost"),
    [
        (4, Decimal("0.000244")),
        # An hour's price divided into seconds never ends: the estimate is said to the
        # millionth of a dollar a bill is recorded to, never on and on.
        (4.1, Decimal("0.000251")),
        # Half a millionth rounds up, as the bill's database column rounds it.
        (4.23, Decimal("0.000259")),
        (0, Decimal("0")),
    ],
)
def test_a_transcription_is_estimated_to_the_millionth_of_a_dollar(
    seconds: float, cost: Decimal
) -> None:
    estimated = transcription_cost_usd(MODEL_FOR_PURPOSE["transcribe_line"], seconds)
    assert estimated == cost
    assert dollars(estimated) == dollars(cost)
    assert len(f"{estimated:f}") <= len("0.000000")


def test_a_broll_line_fix_is_estimated_with_the_judges_asks_and_up_to_six_agent_tries(
    shown: ProducedItem,  # noqa: F811
) -> None:
    # The scene keeps its picture; the new line's 52
    # characters at Inworld's $25 a million are $0.0013; its 8 words take the fake voice 4
    # seconds, $0.000244 at Scribe's $0.22 an hour; a 5-second clip at Boreal's $0.0792 a
    # second with the judge's 6 asks at $0.003 is $0.414, and up to 6 tries of the fixing
    # agent, each a new picture, that clip and the agent's 3 turns at $0.07 (decider call 13).
    estimate = line_estimate(2, LINE_2_NEW)

    picture = _picture_only_estimate_usd(Scene.objects.get(number=2))
    each_try = picture + Decimal("0.414") + 3 * Decimal("0.07")
    assert estimate == (Decimal("0.415544"), Decimal("0.415544") + 6 * each_try)
