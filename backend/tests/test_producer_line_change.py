"""Changing what a finished scene says, driven through the chat (route B step 1, #138): the
producer restates the new line word for word in a Fix, the shop owner says yes, and
change_line gives the scene its new line. Its starting picture, audio, transcript and clip are
then made again by the usual tools, with no note, for that Fix, and when its clip lands the ad
is assembled again by code. The producer's model and the scene models are faked at the
gateway; the clips are real tiny videos."""

from decimal import Decimal
from typing import Any

import pytest

from agents.producer import picture_estimate_usd
from gateway.fake import FakeModel, turn
from jobs.models import Fix, ProducedItem, Scene

from .conftest import HeldSteps, facts_ok, paid_for, results_of
from .test_producer_broll import (
    BROLL_CHOICE,
    SHOWS,
    TALKING_CHOICE,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_fixes import (
    Say,
    ads,
    last_step,
    owner_says,
    restating,
    shown,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_3 = "The last scene should say it's on sale, not just the price."
LINE_3 = "On sale now for $19.00."
FIX_3 = f'In scene 3, she says "{LINE_3}"'
NOTE_2 = "Scene 2 should say it's oven safe too."
LINE_2_NEW = "Hand-thrown, holds 350 ml, oven and dishwasher safe."
FIX_2 = f'In scene 2, the voice says "{LINE_2_NEW}"'


def changing(scene: int, line: str) -> tuple[str, dict[str, Any]]:
    return ("change_line", {"scene": scene, "line": line})


def restated_and_yes(fake_model: FakeModel, say: Say, scene: int, fix: str, line: str) -> None:
    """The shop owner's note is restated as a line Fix, they say yes, and the line changes."""
    owner_says(fake_model, say, "About that scene...", restating((scene, fix, "line")))
    owner_says(fake_model, say, "Yes, do that.", changing(scene, line))


def made_again(fake_model: FakeModel, steps: HeldSteps, say: Say, scene: int) -> None:
    """The scene's starting picture and audio, its transcript, then its clip, each with no
    note, as the producer makes them once the line has changed."""
    owner_says(
        fake_model,
        say,
        "Go on",
        ("make_starting_picture", {"scene": scene, "note": None}),
        ("make_line_audio", {"scene": scene}),
    )
    fake_model.respond(
        "choose_broll_picture" if scene == 2 else "choose_starting_picture",
        BROLL_CHOICE if scene == 2 else TALKING_CHOICE,
    )
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("transcribe_line_audio", {"scene": scene}))
    run(fake_model, steps)
    owner_says(fake_model, say, "Go on", ("make_clip", {"scene": scene, "note": None}))
    run(fake_model, steps)


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


# --- The whole change -------------------------------------------------------------------------


def test_a_talking_scenes_new_line_remakes_only_that_scene_and_a_new_ad(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    untouched = {1: versions(1), 2: versions(2)}

    restated_and_yes(fake_model, say, 3, FIX_3, LINE_3)
    scene = Scene.objects.get(number=3)
    assert (scene.line, scene.status, scene.fact_checked) == (LINE_3, "planned", True)

    made_again(fake_model, steps, say, 3)

    assert versions(3) == {
        "starting_picture": [1, 2],
        "line_audio": [1, 2],
        "transcript": [1, 2],
        "clip": [1, 2],
    }
    assert {1: versions(1), 2: versions(2)} == untouched
    assert Scene.objects.get(number=3).status == "finished"
    fix = Fix.objects.get()
    assert {step.kind for step in fix.steps.all()} == {
        "starting_picture",
        "line_audio",
        "transcript",
        "clip",
    }
    # The fix's clip landing assembled the new ad, captioned with the new words.
    assert ads() == [1, 2]
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    captioned = " ".join(caption["text"] for caption in ad.captions)
    assert "On sale now" in captioned
    assert "Yours for" not in captioned


def test_a_line_change_pays_only_for_the_scenes_steps_and_no_judge_checks_the_fix(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    paid = len(paid_for())

    restated_and_yes(fake_model, say, 3, FIX_3, LINE_3)
    made_again(fake_model, steps, say, 3)

    # The step 6 judge doesn't check a change of words (#131).
    assert paid_for()[paid:] == [
        "choose_starting_picture",
        "make_starting_picture",
        "speak_line",
        "transcribe_line",
        "make_talking_clip",
        "collect_talking_clip",
    ]


def test_the_old_lines_items_are_kept_and_the_new_ad_uses_none_of_them(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    old_clip = ProducedItem.objects.get(kind="clip", scene__number=3)

    restated_and_yes(fake_model, say, 3, FIX_3, LINE_3)
    made_again(fake_model, steps, say, 3)
    owner_says(fake_model, say, "Show me again", ("assemble_ad", {}))

    new_clip = ProducedItem.objects.get(kind="clip", scene__number=3, version=2)
    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None and ad.version == 2
    assert [cut["clip"] for cut in ad.cuts][2] == new_clip.pk
    assert ProducedItem.objects.filter(pk=old_clip.pk).exists()
    assert results_of("assemble_ad")[-1].startswith("The ad was already assembled")


def test_a_broll_scenes_new_line_keeps_what_it_shows_and_its_labels(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    scene = Scene.objects.get(number=2)
    labels = (scene.shows, scene.broll_kind, scene.person_shown, scene.usage, scene.result)

    restated_and_yes(fake_model, say, 2, FIX_2, LINE_2_NEW)
    made_again(fake_model, steps, say, 2)

    scene = Scene.objects.get(number=2)
    assert scene.line == LINE_2_NEW
    assert (scene.shows, scene.broll_kind, scene.person_shown, scene.usage, scene.result) == (
        labels
    )
    assert scene.shows == SHOWS
    assert last_step("clip", 2).fix == Fix.objects.get()
    assert ads() == [1, 2]


# --- What is refused --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("restated", "line", "refused"),
    [
        (
            None,
            LINE_3,
            "scene 3's line changes only for a fix restated as a new line, and no fix was "
            "restated for it. Call restate_fix first and wait for the shop owner's answer.",
        ),
        (
            ("In scene 3, she holds the mug up beside her face.", "picture"),
            LINE_3,
            "scene 3's line changes only for a fix restated as a new line, and its fix was "
            "restated as a new picture. Call restate_fix first and wait for the shop owner's "
            "answer.",
        ),
        (
            (FIX_3, "line"),
            "On sale now for $9.00.",
            f"the line isn't word for word in scene 3's fix as it was restated to the shop "
            f'owner: "{FIX_3}" Pass the new line exactly as it is there, or restate the fix.',
        ),
        (
            (FIX_3, "line"),
            "   ",
            f"the line isn't word for word in scene 3's fix as it was restated to the shop "
            f'owner: "{FIX_3}" Pass the new line exactly as it is there, or restate the fix.',
        ),
    ],
    ids=["no fix", "a picture fix", "not in the fix", "blank"],
)
def test_a_line_change_the_shop_owner_didnt_say_yes_to_is_refused_and_nothing_changes(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
    restated: tuple[str, str] | None,
    line: str,
    refused: str,
) -> None:
    if restated is not None:
        owner_says(fake_model, say, NOTE_3, restating((3, *restated)))
    paid = paid_for()

    owner_says(fake_model, say, "Yes", changing(3, line))

    assert results_of("change_line") == [f"Refused: {refused} Nothing was done."]
    scene = Scene.objects.get(number=3)
    assert (scene.line, scene.status) == ("Yours for $24.00.", "finished")
    assert paid_for() == paid
    assert steps.held == []


def test_a_line_change_before_the_shop_owner_answers_is_refused(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[restating((3, FIX_3, "line"))]),
        turn(says=f'I\'ll have her say "{LINE_3}" for about $0.30.', calls=[changing(3, LINE_3)]),
        turn(says="Changing it."),
    )
    say(NOTE_3)

    assert results_of("change_line") == [
        "Refused: the shop owner hasn't answered since scene 3's fix was restated to them. Wait "
        "for their answer. Nothing was done."
    ]
    assert Scene.objects.get(number=3).line == "Yours for $24.00."
    assert Fix.objects.get().confirmed_by is None


def test_a_line_too_long_to_say_is_refused_with_the_reason(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    # 38 words: 19 seconds at the fake voice's 2 words a second, over the 18 a scene lasts.
    long_line = " ".join(["Really"] * 37 + ["great."])
    owner_says(
        fake_model, say, NOTE_3, restating((3, f'In scene 3, she says "{long_line}"', "line"))
    )

    owner_says(fake_model, say, "Yes", changing(3, long_line))

    assert results_of("change_line") == [
        "Refused: the new line takes longer to say than a scene can last (18 seconds at the "
        "voice's speed), so it can't be used. Ask the shop owner for a shorter line. Nothing was "
        "done."
    ]
    assert Scene.objects.get(number=3).line == "Yours for $24.00."
    assert Fix.objects.get().confirmed_by is None


def test_a_line_change_while_the_scene_is_being_made_is_refused_as_busy(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    restated_and_yes(fake_model, say, 3, FIX_3, LINE_3)
    owner_says(fake_model, say, "Go on", ("make_line_audio", {"scene": 3}))
    fix_again = 'In scene 3, she says "Half price today."'
    owner_says(fake_model, say, "Actually", restating((3, fix_again, "line")))

    owner_says(fake_model, say, "Yes", changing(3, "Half price today."))

    assert results_of("change_line")[-1] == (
        "Refused: scene 3 is still being made. You'll be told when it's ready; change its line "
        "then. Nothing was done."
    )
    assert Scene.objects.get(number=3).line == LINE_3


# --- Run again --------------------------------------------------------------------------------


def test_the_same_line_again_changes_nothing_and_pays_for_nothing(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    restated_and_yes(fake_model, say, 3, FIX_3, LINE_3)
    made_again(fake_model, steps, say, 3)
    paid = paid_for()

    owner_says(fake_model, say, "Again?", changing(3, LINE_3))

    assert results_of("change_line")[-1].startswith(
        f'Scene 3\'s line is already "{LINE_3}", so nothing was changed.'
    )
    assert Scene.objects.get(number=3).status == "finished"
    assert paid_for() == paid


# --- The estimate -----------------------------------------------------------------------------


def test_a_line_fix_is_estimated_as_a_picture_its_audio_and_a_clip(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
) -> None:
    scene = Scene.objects.get(number=3)
    audio = ProducedItem.objects.get(kind="line_audio", scene=scene)
    assert audio.seconds is not None
    # The line's 17 characters at Inworld's $25 a million, and its audio heard at Scribe's
    # $0.22 an hour, on top of a picture and a clip.
    expected = (
        picture_estimate_usd(scene)
        + Decimal("0.000425")
        + Decimal(str(audio.seconds)) * Decimal("0.22") / 3600
    ).quantize(Decimal("0.000001"))

    owner_says(fake_model, say, NOTE_3, restating((3, FIX_3, "line")))

    fix = Fix.objects.get()
    assert fix.estimate_usd == expected
    assert fix.estimate_usd > picture_estimate_usd(scene)
    assert results_of("restate_fix")[-1].startswith("Recorded the fix. Scene 3: about $")


def test_a_new_broll_line_too_long_for_its_clip_is_shortened_and_the_owner_told(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    restated_and_yes(fake_model, say, 2, FIX_2, LINE_2_NEW)
    # Said slower than the voice was measured: its 8 words take 15.5 seconds, too long for
    # any clip.
    fake_model.words_per_second = 8 / 15.5
    fake_model.respond("shorten_line", {"line": "Hand-thrown, oven and dishwasher safe."})
    fake_model.respond("fact_check", facts_ok(2))

    owner_says(fake_model, say, "Go on", ("make_line_audio", {"scene": 2}))
    run(fake_model, steps)

    assert last_step("line_audio", 2).result == (
        "Background step finished: scene 2's line's audio takes 15.5 seconds to say, too long "
        "for any clip. Scene 2's line was shortened to fit its clip. It isn't the line the shop "
        'owner said yes to: it is now "Hand-thrown, oven and dishwasher safe." Tell the shop '
        "owner. Make its audio again."
    )
    scene = Scene.objects.get(number=2)
    assert scene.shortened_from == [LINE_2_NEW]
    assert scene.shows == SHOWS

    # Run again, as after a restart, the change doesn't undo the shortening.
    owner_says(fake_model, say, "Go on", changing(2, LINE_2_NEW))
    assert Scene.objects.get(number=2).line == "Hand-thrown, oven and dishwasher safe."


@pytest.mark.parametrize("tool", ["make_starting_picture", "make_clip"])
def test_a_line_fix_passed_as_a_redos_note_is_refused_saying_to_change_the_line(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
    tool: str,
) -> None:
    scene = 2 if tool == "make_clip" else 3
    fix = FIX_2 if scene == 2 else FIX_3
    owner_says(fake_model, say, "About that scene...", restating((scene, fix, "line")))

    owner_says(fake_model, say, "Yes", (tool, {"scene": scene, "note": fix}))

    assert results_of(tool)[-1] == (
        f"Refused: scene {scene}'s fix was restated as a new line. Change its line with "
        "change_line, then make the scene again with no note. Nothing was done."
    )
    assert steps.held == []
