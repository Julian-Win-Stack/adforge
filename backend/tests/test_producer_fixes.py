"""Fixing a finished ad's scene from the shop owner's notes, driven through the chat (route B
step 1, #135): the producer restates what it will change with restate_fix, which records a
Fix and its cost, and a redo tool refuses to make anything for a note on a scene that already
has a clip until that Fix exists word for word and the shop owner has answered since. When a
Fix's clip lands and every scene is finished, code assembles the new ad. The producer's model
and the scene models are faked at the gateway; the clips are real tiny videos."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.db.models.signals import pre_save

from agents import tasks
from agents.models import ToolCall
from agents.producer import (
    PICTURE_FALLBACK_USD,
    _dollars,
    picture_estimate_usd,
)
from agents.tasks import restart_dead_producers
from chat.models import Attachment, Message
from gateway.catalog import MODEL_FOR_PURPOSE, PRICE_PER_SECOND_OF_VIDEO
from gateway.fake import FakeModel, meanwhile, turn
from gateway.models import ModelCall
from jobs import work
from jobs.models import Fix, Job, ProducedItem, Scene, SceneStep

from .conftest import (
    HeldSteps,
    WorkerStopped,
    given_to_the_producer,
    paid_for,
    results_of,
    said_yes_to_a_fix,
)
from .test_producer_broll import (
    TALKING_CHOICE,
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_restarts import WorkerStopped as ProducerStopped
from .test_producer_restarts import the_producer_died, the_worker_stops

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_2 = "Scene 2 just sits there. The tea should actually pour into the mug."
FIX_2 = "In scene 2, the tea pours from the teapot into the mug and steam rises from it."
NOTE_1 = "In the first scene she should hold the mug with both hands."
FIX_1 = "In scene 1, she holds the mug up beside her face with both hands."
REWRITE: dict[str, Any] = {
    "motion_prompt": "Tea pours from the teapot into the mug and steam rises from it.",
    "motion_prompt_reason": "The note asks for the pour.",
}
BOREAL = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_broll_clip"]]
HEYGEN = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_talking_clip"]]

Say = Callable[..., None]


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, if any, then
    replies."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="On it."))
    say(text)


def restating(*fixes: tuple[int, str, str]) -> tuple[str, dict[str, Any]]:
    """A restate_fix call for each (scene, fix, redo)."""
    return (
        "restate_fix",
        {"fixes": [{"scene": scene, "fix": fix, "redo": redo} for scene, fix, redo in fixes]},
    )


def clip_with(scene: int, note: str | None) -> tuple[str, dict[str, Any]]:
    return ("make_clip", {"scene": scene, "note": note})


def picture_with(scene: int, note: str | None) -> tuple[str, dict[str, Any]]:
    return ("make_starting_picture", {"scene": scene, "note": note})


def ads() -> list[int]:
    """Each finished ad's version, oldest first."""
    return list(
        ProducedItem.objects.filter(kind="finished_ad")
        .order_by("version")
        .values_list("version", flat=True)
    )


def times_shown(ad: ProducedItem) -> int:
    return Attachment.objects.filter(file=ad.file).count()


def last_step(kind: str = "clip", scene: int = 2) -> SceneStep:
    step = SceneStep.objects.filter(kind=kind, scene__number=scene).last()
    assert step is not None
    return step


@pytest.fixture
def shown(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> ProducedItem:
    """A chat whose finished ad (version 1) is shown to the shop owner: scenes 1 and 3 have
    the person talking, scene 2 shows the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


@pytest.fixture
def restated_2(fake_model: FakeModel, shown: ProducedItem, say: Say) -> Fix:
    """The shop owner's note on scene 2's clip, restated as a Fix."""
    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))
    return Fix.objects.get()


def confirm_and_redo_2(fake_model: FakeModel, steps: HeldSteps, say: Say) -> None:
    """The shop owner says yes; scene 2's clip is made again for the Fix, and lands."""
    owner_says(fake_model, say, "Yes, do that.", clip_with(2, FIX_2))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)


# --- The guard --------------------------------------------------------------------------------


@pytest.mark.parametrize("redo", [clip_with(2, FIX_2), picture_with(2, FIX_2)])
def test_a_note_on_a_scene_with_a_clip_and_no_fix_is_refused_and_nothing_is_paid(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say, redo: Any
) -> None:
    paid = paid_for()

    owner_says(fake_model, say, NOTE_2, redo)

    assert paid_for() == paid
    assert steps.held == []
    assert results_of(redo[0])[-1] == (
        "Refused: scene 2 already has a clip, so changing it is a fix, and no fix was restated "
        "for it. Call restate_fix first and wait for the shop owner's answer. Nothing was done."
    )


def test_a_redo_before_the_fix_is_restated_to_the_shop_owner_is_refused(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    paid = paid_for()

    # The producer goes on to the redo in the same run, without waiting for an answer.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        turn(calls=[clip_with(2, FIX_2)]),
        turn(says="Doing it."),
    )
    say("Hmm, and make it quick.")

    assert paid_for() == paid
    assert steps.held == []
    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's fix hasn't been restated to the shop owner yet. Tell them the "
        "restatement and its cost, and wait for their answer. Nothing was done."
    )


def test_a_redo_after_the_restatement_but_before_an_answer_is_refused(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    # The producer tells the shop owner the restatement and goes straight on to the redo.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        turn(says="I'll make the tea pour, for about $0.40.", calls=[clip_with(2, FIX_2)]),
        turn(says="Doing it."),
    )
    say(NOTE_2)

    assert steps.held == []
    assert results_of("make_clip")[-1] == (
        "Refused: the shop owner hasn't answered since scene 2's fix was restated to them. "
        "Wait for their answer. Nothing was done."
    )


def test_after_the_shop_owners_answer_the_redo_is_made_for_the_fix(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)

    fix = Fix.objects.get()
    assert fix.confirmed_by == Message.objects.get(text="Yes, do that.")
    step = last_step()
    assert (step.fix, step.note, step.status) == (fix, FIX_2, "finished")
    assert paid_for().count("make_broll_clip") == 2


def test_a_message_sent_before_the_restatement_was_shown_isnt_the_yes(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    # The shop owner writes again while the producer is still working on restate_fix's turn,
    # before it has told them the restatement or the cost.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        meanwhile(lambda: say("Oh and make the mug blue."), turn(calls=[clip_with(2, FIX_2)])),
        turn(says="Here's what I'll change."),
        turn(says="Noted."),
    )
    say(NOTE_2)

    assert steps.held == []
    assert results_of("make_clip")[3:] == [
        "Refused: scene 2's fix hasn't been restated to the shop owner yet. Tell them the "
        "restatement and its cost, and wait for their answer. Nothing was done."
    ]
    assert Fix.objects.get().confirmed_by is None


def test_a_note_that_isnt_the_fix_word_for_word_is_refused_quoting_the_fix(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, "Make the tea pour into the mug."))

    assert steps.held == []
    assert results_of("make_clip")[-1] == (
        "Refused: the note isn't scene 2's fix as it was restated to the shop owner: "
        f'"{FIX_2}" Pass that fix word for word, or restate it. Nothing was done.'
    )
    assert Fix.objects.get().confirmed_by is None


def test_a_fix_spaced_differently_is_still_the_fix(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, f"  {FIX_2.replace(' ', '  ')}\n"))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    assert last_step().fix == Fix.objects.get()


def test_a_correction_replaces_the_fix_and_needs_its_own_answer(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    corrected = "In scene 2, the tea pours slowly from the left into the mug."
    owner_says(
        fake_model,
        say,
        "No, it should pour from the left, slowly.",
        restating((2, corrected, "clip")),
    )

    # The old Fix no longer unlocks a redo...
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))
    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        f"Refused: the note isn't scene 2's fix as it was restated to the shop owner: "
        f'"{corrected}"'
    )
    # ...and the new one does, after the answer.
    owner_says(fake_model, say, "Yes, that one.", clip_with(2, corrected))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    old, new = Fix.objects.order_by("id")
    assert (old.confirmed_by, last_step().fix) == (None, new)


def test_a_correction_before_any_answer_isnt_unlocked_by_the_first_note(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    corrected = "In scene 2, the tea pours slowly from the left into the mug."
    # Restated, then corrected in the same run: the note before both isn't an answer to it.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        turn(calls=[restating((2, corrected, "clip"))]),
        turn(calls=[clip_with(2, corrected)]),
        turn(says="Doing it."),
    )
    say(NOTE_2)

    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        "Refused: scene 2's fix hasn't been restated to the shop owner yet."
    )


def test_one_answer_unlocks_two_scenes_restated_together_and_no_other(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(
        fake_model,
        say,
        f"{NOTE_1} {NOTE_2}",
        restating((1, FIX_1, "picture"), (2, FIX_2, "clip")),
    )
    fix_1, fix_2 = Fix.objects.order_by("scene__number")
    assert fix_1.asked == fix_2.asked

    owner_says(
        fake_model,
        say,
        "Yes to both",
        picture_with(1, FIX_1),
        clip_with(2, FIX_2),
        picture_with(3, "She smiles more."),
    )

    assert results_of("make_starting_picture")[-1] == (
        "Refused: scene 3 already has a clip, so changing it is a fix, and no fix was restated "
        "for it. Call restate_fix first and wait for the shop owner's answer. Nothing was done."
    )
    assert len(steps.held) == 2
    assert (last_step("starting_picture", 1).fix, last_step("clip", 2).fix) == (fix_1, fix_2)
    yes = Message.objects.get(text="Yes to both")
    assert [fix.confirmed_by for fix in Fix.objects.order_by("scene__number")] == [yes, yes]


def test_a_redo_doesnt_accept_another_scenes_fix(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, NOTE_1, restating((1, FIX_1, "picture")))

    owner_says(fake_model, say, "Yes", picture_with(3, FIX_1))

    assert steps.held == []
    assert results_of("make_starting_picture")[-1].startswith(
        "Refused: scene 3 already has a clip, so changing it is a fix, and no fix was restated"
    )


def test_a_fix_for_the_picture_isnt_a_note_for_the_clip(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "picture")))

    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))

    assert steps.held == []
    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's fix was restated as a new starting picture, not a new clip. Make "
        "its starting picture with the fix as the note, then its clip with no note. Nothing "
        "was done."
    )


def test_a_picture_note_on_a_scene_with_no_clip_yet_is_made_as_before(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, "Make it sunny", picture_with(1, "A sunny workshop."))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)

    step = last_step("starting_picture", 1)
    assert (step.status, step.note, step.fix) == ("finished", "A sunny workshop.", None)
    assert not Fix.objects.exists()


def test_the_same_confirmed_fix_again_is_already_made_and_charges_nothing(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)
    paid = paid_for()

    owner_says(fake_model, say, "Did it work?", clip_with(2, FIX_2))

    assert paid_for() == paid
    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        "Scene 2's clip was already made from this starting picture and audio with this note "
        "(version 2), so nothing was made or paid for again."
    )


def test_a_redo_that_failed_can_be_tried_again_without_a_new_answer(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))
    # The rewrite's answer can't be used, so the step fails; told so, the producer tries
    # again straight away, with no new message from the shop owner.
    fake_model.respond("rewrite_clip_prompt", {"motion_prompt": " ", "nonsense": 1}, REWRITE)
    fake_model.respond(
        "produce", turn(calls=[clip_with(2, FIX_2)]), turn(says="Trying again."), turn(says="Done.")
    )
    steps.run_held()
    steps.run_held()

    failed, made = SceneStep.objects.filter(kind="clip", scene__number=2, fix__isnull=False)
    fix = Fix.objects.get()
    assert (failed.status, failed.fix, made.status, made.fix) == ("failed", fix, "finished", fix)
    assert fix.confirmed_by == Message.objects.get(text="Yes")


def test_a_redo_while_the_scenes_clip_is_being_made_is_refused_as_busy(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))

    owner_says(fake_model, say, "Go", clip_with(2, FIX_2))

    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's clip is already being made. You'll be told when it's ready. "
        "Nothing was done."
    )
    assert len(steps.held) == 1


def test_a_redo_with_no_note_on_a_finished_scene_is_refused_and_nothing_is_paid(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    # Scene 1's first picture was made with the producer's own note, so a picture with none
    # isn't already made.
    SceneStep.objects.filter(kind="starting_picture", scene__number=1).update(
        note="She holds the mug in warm morning light."
    )
    paid = paid_for()

    owner_says(fake_model, say, "Scene 1 looks off to me.", picture_with(1, None))

    assert paid_for() == paid
    assert steps.held == []
    assert results_of("make_starting_picture")[-1] == (
        "Refused: scene 1 already has a clip, so changing it is a fix. Call restate_fix first, "
        "wait for the shop owner's answer, then pass the fix word for word as the note. Nothing "
        "was done."
    )


def test_a_clip_with_no_note_isnt_made_for_a_picture_fix_the_owner_has_since_corrected(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, NOTE_1, restating((1, FIX_1, "picture")))
    owner_says(fake_model, say, "Yes", picture_with(1, FIX_1))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    # Seeing the new picture, the shop owner corrects the fix: restated, not yet confirmed.
    owner_says(
        fake_model,
        say,
        "No, one hand.",
        restating((1, "In scene 1, she holds it in one hand.", "picture")),
    )
    paid = paid_for()

    owner_says(fake_model, say, "Hmm", clip_with(1, None))

    assert paid_for() == paid
    assert steps.held == []
    assert results_of("make_clip")[-1].startswith(
        "Refused: scene 1 already has a clip, so changing it is a fix."
    )


def test_a_free_clip_with_no_note_on_a_finished_scene_is_still_handed_back(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    paid = paid_for()

    owner_says(fake_model, say, "Is scene 2 done?", clip_with(2, None))

    assert paid_for() == paid
    assert results_of("make_clip")[-1].startswith("Scene 2's clip was already made")


def test_a_clip_fix_isnt_a_note_for_the_picture(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", picture_with(2, FIX_2))

    assert steps.held == []
    assert results_of("make_starting_picture")[-1] == (
        "Refused: scene 2's fix was restated as a new clip, not a new starting picture. Make "
        "its clip with the fix as the note. Nothing was done."
    )


# --- restate_fix ------------------------------------------------------------------------------


def test_restating_before_the_ad_is_shown_is_refused(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    say: Say,
) -> None:
    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))

    assert results_of("restate_fix") == [
        "Refused: the ad hasn't been assembled and shown to the shop owner yet, and a fix "
        "changes a finished ad. Nothing was done."
    ]
    assert not Fix.objects.exists()


@pytest.mark.parametrize(
    ("fixes", "refused"),
    [
        (
            [(9, FIX_2, "clip")],
            "the ad has no scene 9: its scenes are 1 to 3.",
        ),
        (
            [(1, FIX_1, "clip")],
            "scene 1 shows the person talking, and a talking clip's movement can't be changed: "
            "the video model ignores what it's asked about movement in a talking clip. Restate "
            "it as a new starting picture instead.",
        ),
        (
            [(2, FIX_2, "line")],
            "changing a scene's line isn't built yet. Tell the shop owner it can't be done yet.",
        ),
        ([(2, "  \n", "clip")], "scene 2's fix is blank. Restate it in one line."),
        (
            [(2, FIX_2, "clip"), (2, FIX_2, "picture")],
            "scene 2 is restated twice. Give each scene one fix that carries everything the "
            "shop owner asked for it.",
        ),
        ([], "no fix was given. Restate at least one scene's fix."),
    ],
)
def test_restate_fix_refuses_what_it_cant_record(
    fake_model: FakeModel,
    shown: ProducedItem,
    say: Say,
    fixes: list[tuple[int, str, str]],
    refused: str,
) -> None:
    owner_says(fake_model, say, NOTE_2, restating(*fixes))

    assert results_of("restate_fix") == [f"Refused: {refused} Nothing was done."]
    assert not Fix.objects.exists()


def test_restating_a_scene_with_no_clip_is_refused(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    ProducedItem.objects.filter(kind="clip", scene__number=3).delete()

    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip"), (3, FIX_1, "picture")))

    assert results_of("restate_fix") == [
        "Refused: scene 3 has no clip yet, so it isn't finished and there is nothing to fix. "
        "Nothing was done."
    ]
    assert not Fix.objects.exists()


def test_restating_the_clip_of_a_scene_planned_before_its_labels_is_refused(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    Scene.objects.filter(number=2).update(broll_kind="", person_shown="")

    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))

    assert results_of("restate_fix") == [
        "Refused: scene 2 was planned before scenes had their B-roll labels, so its movement "
        "can't be rewritten from a note. Restate it as a new starting picture instead. Nothing "
        "was done."
    ]


def test_a_fix_is_recorded_with_its_redo_estimate_and_the_call_that_asked(
    restated_2: Fix,
) -> None:
    fix = restated_2
    call = ToolCall.objects.get(tool="restate_fix")
    assert (fix.scene.number, fix.fix, fix.redo, fix.asked, fix.confirmed_by) == (
        2,
        FIX_2,
        "clip",
        call,
        None,
    )
    assert fix.job == Job.objects.get()
    assert fix.estimate_usd == 5 * BOREAL
    assert call.asked_about == "fixes"


def test_restate_fix_hands_back_each_estimate_the_total_and_to_wait(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    owner_says(
        fake_model, say, f"{NOTE_1} {NOTE_2}", restating((1, FIX_1, "picture"), (2, FIX_2, "clip"))
    )

    fix_1, fix_2 = Fix.objects.order_by("scene__number")
    total = fix_1.estimate_usd + fix_2.estimate_usd
    assert results_of("restate_fix") == [
        "Recorded the fixes. Scene 1: about "
        f"{_dollars(fix_1.estimate_usd)}. Scene 2: about {_dollars(fix_2.estimate_usd)}. "
        f"About {_dollars(total)} in all. Tell the shop owner this restatement and what it "
        "will cost, and wait for their answer: nothing is made until they say yes. Once they "
        "have, pass each scene's fix word for word as the note of its redo."
    ]


def test_the_fix_holds_the_shop_owners_note(restated_2: Fix) -> None:
    note = Message.objects.get(text=NOTE_2)
    assert restated_2.notes == [{"seq": note.seq, "text": NOTE_2}]


def test_the_fix_holds_every_message_since_the_ad_was_shown(
    fake_model: FakeModel, shown: ProducedItem, say: Say
) -> None:
    owner_says(fake_model, say, "Hmm, scene 2.")
    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))

    assert [note["text"] for note in Fix.objects.get().notes] == ["Hmm, scene 2.", NOTE_2]


def test_a_corrected_fix_holds_the_first_note_and_the_correction(
    fake_model: FakeModel, restated_2: Fix, say: Say
) -> None:
    owner_says(
        fake_model, say, "No, from the left.", restating((2, FIX_2 + " From the left.", "clip"))
    )

    assert [note["text"] for note in Fix.objects.last().notes] == [  # type: ignore[union-attr]
        NOTE_2,
        "No, from the left.",
    ]


def test_a_fix_after_the_scenes_confirmed_one_holds_only_whats_new(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    # Confirmed, but its clip still being made: the ad isn't shown again yet.
    owner_says(fake_model, say, "Yes, do that.", clip_with(2, FIX_2))
    owner_says(fake_model, say, "And slower.", restating((2, f"{FIX_2} Slowly.", "clip")))

    assert [note["text"] for note in Fix.objects.last().notes] == ["And slower."]  # type: ignore[union-attr]


def test_another_scenes_fix_holds_everything_since_the_ad_was_shown(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes, do that.", clip_with(2, FIX_2))
    owner_says(fake_model, say, NOTE_1, restating((1, FIX_1, "picture")))

    assert [note["text"] for note in Fix.objects.get(scene__number=1).notes] == [
        NOTE_2,
        "Yes, do that.",
        NOTE_1,
    ]


def test_restate_fix_run_again_after_a_restart_records_each_fix_once(
    fake_model: FakeModel, shown: ProducedItem, say: Say, session_id: str
) -> None:
    owner_says(fake_model, say, NOTE_2)
    fake_model.respond("produce", turn(calls=[restating((2, FIX_2, "clip"))]))

    # The worker stops once the fix is recorded, before the call is kept as finished.
    with (
        the_worker_stops(
            pre_save,
            ToolCall,
            when=lambda call: call.tool == "restate_fix" and call.finished_at is not None,
        ),
        pytest.raises(ProducerStopped),
    ):
        say("Please fix scene 2.")
    assert Fix.objects.count() == 1
    the_producer_died(session_id)
    fake_model.respond("produce", turn(says="Here's what I'll change."))
    restart_dead_producers()

    assert Fix.objects.count() == 1
    assert ToolCall.objects.get(tool="restate_fix").finished
    assert results_of("restate_fix")[0].startswith("Recorded the fix. Scene 2: about ")


def test_the_producer_is_given_only_the_restate_fix_call_and_its_result(
    fake_model: FakeModel, restated_2: Fix, say: Say
) -> None:
    owner_says(fake_model, say, "Yes")

    given = given_to_the_producer(ModelCall.objects.filter(purpose="produce").count())
    restated = [each for each in given if each.get("tool") == "restate_fix"]
    assert len(restated) == 1
    assert restated[0]["result"] == results_of("restate_fix")[0]
    assert not any(FIX_2 in str(each) for each in given if each.get("tool") != "restate_fix")


# --- Estimates and costs ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("audio_seconds", "billed"), [(4.0, 5), (5.0000001, 5), (5.2, 6), (9.0, 9), (14.9, 15)]
)
def test_a_broll_clip_is_estimated_at_its_whole_seconds_at_the_boreal_price(
    audio_seconds: float, billed: int
) -> None:
    assert work.clip_estimate_usd(audio_seconds, broll=True) == billed * BOREAL


@pytest.mark.parametrize(("audio_seconds", "billed"), [(0.5, "1"), (3.5, "3.5"), (9.0, "9.0")])
def test_a_talking_clip_is_estimated_at_its_audio_at_the_heygen_price(
    audio_seconds: float, billed: str
) -> None:
    assert work.clip_estimate_usd(audio_seconds, broll=False) == Decimal(billed) * HEYGEN


def test_a_picture_fix_is_estimated_at_the_last_pictures_cost_and_a_clip(
    shown: ProducedItem,
) -> None:
    scene = Scene.objects.get(number=1)
    picture = last_step("starting_picture", 1)
    paid = sum(
        (
            call.cost_usd or Decimal(0)
            for call in picture.tool_call.model_calls.all()
            if call.purpose not in ("plan_before_picture", "make_before_picture")
        ),
        Decimal(0),
    )
    assert paid > 0

    # The fake voice says scene 1's 8 words in 4 seconds.
    assert picture_estimate_usd(scene) == paid + 4 * HEYGEN


def test_a_broll_picture_fix_leaves_out_the_shared_before_picture(shown: ProducedItem) -> None:
    picture = last_step("starting_picture", 2)
    drew_it = picture.tool_call.model_calls.filter(purpose="make_before_picture")
    assert drew_it.exists(), "scene 2's picture drew the ad's before-picture"
    kept = picture.tool_call.model_calls.exclude(
        purpose__in=["plan_before_picture", "make_before_picture"]
    )

    assert picture_estimate_usd(Scene.objects.get(number=2)) == (
        sum((call.cost_usd or Decimal(0) for call in kept), Decimal(0)) + 5 * BOREAL
    )


def test_a_picture_with_no_recorded_cost_is_estimated_at_the_fallback(
    shown: ProducedItem,
) -> None:
    ModelCall.objects.filter(tool_call=last_step("starting_picture", 1).tool_call).update(
        cost_usd=0
    )

    assert picture_estimate_usd(Scene.objects.get(number=1)) == (PICTURE_FALLBACK_USD + 4 * HEYGEN)


def test_a_fixs_cost_is_what_its_own_steps_cost(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)

    step = last_step()
    assert restated_2.cost_usd() == step.tool_call.cost_usd()
    assert restated_2.cost_usd() > 5 * BOREAL  # the clip and its prompt's rewrite


def test_a_fixs_cost_leaves_out_steps_of_other_fixes_and_of_the_first_ad(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)
    corrected = "In scene 2, the tea pours slowly from the left into the mug."
    owner_says(fake_model, say, "Pour from the left now.", restating((2, corrected, "clip")))
    owner_says(fake_model, say, "Yes", clip_with(2, corrected))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    first, second = Fix.objects.order_by("id")
    first_step, second_step = SceneStep.objects.filter(kind="clip", fix__isnull=False)
    assert first.cost_usd() == first_step.tool_call.cost_usd()
    assert second.cost_usd() == second_step.tool_call.cost_usd()


def test_a_failed_redos_cost_is_counted(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))
    fake_model.respond("rewrite_clip_prompt", {"motion_prompt": " ", "nonsense": 1})
    run(fake_model, steps)

    assert last_step().status == "failed"
    assert restated_2.cost_usd() == last_step().tool_call.cost_usd() > 0


@pytest.mark.parametrize(
    ("amount", "said"),
    [("0.396", "$0.396"), ("0.4", "$0.40"), ("1.5", "$1.50"), ("0", "$0.00"), ("2.05", "$2.05")],
)
def test_estimates_are_in_dollars_to_the_cent_or_their_last_digit(amount: str, said: str) -> None:
    assert _dollars(Decimal(amount)) == said


# --- Messages, and the ad assembled by code ---------------------------------------------------


def test_the_fixs_clip_finishing_names_its_running_cost(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)

    assert last_step().result.startswith(
        "Background step finished: scene 2's clip is ready (version 2, 5 seconds), made from "
        "starting picture version 1 and audio version 1, with its movement rewritten from the "
        f'note "{FIX_2}". Scene 2 is finished. Scene 2\'s fix has cost '
        f"{_dollars(restated_2.cost_usd())} so far."
    )


def test_a_clip_with_no_fix_names_no_fix_cost_and_assembles_nothing(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    assemble_through_the_chat(fake_model, steps, say)

    for step in SceneStep.objects.filter(kind="clip"):
        assert "fix" not in step.result
        assert step.result.endswith("Tell the shop owner.")
    # Only the producer's assemble_ad made the ad.
    assert ads() == [1]
    assert ToolCall.objects.filter(tool="assemble_ad").count() == 1


def test_a_fixs_clip_landing_assembles_the_new_ad_and_shows_it(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    old = {clip.scene.number: clip for clip in ProducedItem.objects.filter(kind="clip")}  # type: ignore[union-attr]

    confirm_and_redo_2(fake_model, steps, say)

    assert ads() == [1, 2]
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    new_2 = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert [cut["clip"] for cut in ad.cuts] == [old[1].pk, new_2.pk, old[3].pk]
    assert times_shown(ad) == 1
    assert last_step().result.endswith(
        f"Scene 2's fix has cost {_dollars(restated_2.cost_usd())} so far. Tell the shop owner. "
        f"Every scene is finished, so the ad was assembled again with it (version 2, "
        f"{ad.seconds:g} seconds) and shown to the shop owner in the chat. Ask them to tell "
        "you if it's fixed."
    )


def test_two_fixes_assemble_once_when_the_last_lands(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(
        fake_model, say, f"{NOTE_1} {NOTE_2}", restating((1, FIX_1, "picture"), (2, FIX_2, "clip"))
    )
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2), picture_with(1, FIX_1))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    # Scene 2's clip lands while scene 1's picture is still being made: no ad yet.
    fake_model.respond("produce", turn(says="Done."), turn(says="Done."))
    steps.run_next()
    assert ads() == [1]
    assert last_step().result.endswith(
        "The ad will be assembled again, and shown to the shop owner, once scene 1 is finished."
    )
    steps.run_next()
    assert ads() == [1]

    # Scene 1's clip, made from its new picture with no note, belongs to its fix.
    owner_says(fake_model, say, "Great", clip_with(1, None))
    run(fake_model, steps)

    assert ads() == [1, 2]
    clip_1 = last_step("clip", 1)
    assert clip_1.fix == Fix.objects.get(scene__number=1)
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts][:2] == [
        ProducedItem.objects.get(kind="clip", scene__number=1, version=2).pk,
        ProducedItem.objects.get(kind="clip", scene__number=2, version=2).pk,
    ]
    assert times_shown(ad) == 1


def test_a_sibling_fixs_picture_landing_first_doesnt_let_a_clip_assemble_a_half_fixed_ad(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(
        fake_model, say, f"{NOTE_1} {NOTE_2}", restating((1, FIX_1, "picture"), (2, FIX_2, "clip"))
    )
    owner_says(fake_model, say, "Yes", picture_with(1, FIX_1), clip_with(2, FIX_2))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fake_model.respond("produce", turn(says="Done."), turn(says="Done."))
    steps.run_next()  # scene 1's new picture lands; its clip isn't started yet
    steps.run_next()  # scene 2's clip lands

    assert ads() == [1]
    assert last_step().result.endswith(
        "The ad will be assembled again, and shown to the shop owner, once scene 1 is finished."
    )
    owner_says(fake_model, say, "Nice", clip_with(1, None))
    run(fake_model, steps)

    assert ads() == [1, 2]
    ad = ProducedItem.objects.get(kind="finished_ad", version=2)
    assert [cut["clip"] for cut in ad.cuts][:2] == [
        ProducedItem.objects.get(kind="clip", scene__number=1, version=2).pk,
        ProducedItem.objects.get(kind="clip", scene__number=2, version=2).pk,
    ]


def test_a_fix_brought_back_free_doesnt_hold_up_another_fixs_assembly(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)
    # Scene 2's clip is changed again, then the shop owner goes back to the first fix, whose
    # clip is brought back for nothing.
    said_yes_to_a_fix(2, "In scene 2, the tea pours from the left.", "clip")
    owner_says(
        fake_model,
        say,
        "Try from the left",
        clip_with(2, "In scene 2, the tea pours from the left."),
    )
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)
    said_yes_to_a_fix(2, FIX_2, "clip")
    owner_says(fake_model, say, "Go back", clip_with(2, FIX_2))
    # Then a picture fix on scene 1 lands its clip.
    said_yes_to_a_fix(1, FIX_1, "picture")
    owner_says(fake_model, say, "And scene 1", picture_with(1, FIX_1))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    owner_says(fake_model, say, "Its clip", clip_with(1, None))
    run(fake_model, steps)

    assert last_step("clip", 1).result.endswith("Ask them to tell you if it's fixed.")


def test_assembly_failing_unexpectedly_leaves_the_fixs_clip_made(
    fake_model: FakeModel,
    restated_2: Fix,
    steps: HeldSteps,
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any, **__: Any) -> ProducedItem:
        raise RuntimeError("ffmpeg crashed")

    monkeypatch.setattr("agents.producer.assemble_ad", broken)

    confirm_and_redo_2(fake_model, steps, say)

    step = last_step()
    assert step.status == "finished"
    assert ads() == [1]
    assert step.result.endswith(
        "The ad couldn't be assembled again: an unexpected error stopped it. The details are "
        "in the server log. Assemble it with assemble_ad."
    )


def test_a_picture_fix_assembles_once_its_clip_lands(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, NOTE_1, restating((1, FIX_1, "picture")))
    owner_says(fake_model, say, "Yes", picture_with(1, FIX_1))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    assert ads() == [1]

    owner_says(fake_model, say, "Looks good", clip_with(1, None))
    run(fake_model, steps)

    assert ads() == [1, 2]
    fix = Fix.objects.get()
    assert last_step("clip", 1).fix == fix
    assert fix.cost_usd() == sum(
        (step.tool_call.cost_usd() for step in fix.steps.all()), Decimal(0)
    )


def test_assemble_ad_after_the_automatic_one_is_already_assembled(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    confirm_and_redo_2(fake_model, steps, say)

    owner_says(fake_model, say, "Show me", ("assemble_ad", {}))

    assert ads() == [1, 2]
    assert results_of("assemble_ad")[-1].startswith(
        "The ad was already assembled from these clips, overlays and this music (version 2"
    )
    assert times_shown(ProducedItem.objects.get(kind="finished_ad", version=2)) == 1


def test_a_fix_clip_whose_ad_cant_be_assembled_tells_the_producer_why(
    fake_model: FakeModel, restated_2: Fix, steps: HeldSteps, say: Say
) -> None:
    ProducedItem.objects.filter(kind="transcript", scene__number=3).update(words=[])

    confirm_and_redo_2(fake_model, steps, say)

    assert ads() == [1]
    assert last_step().result.endswith(
        "The ad couldn't be assembled again: no words were heard in scene 3's audio, so there "
        "is nothing to cut its clip to. Make the line's audio again, then its clip."
    )


def test_a_worker_stopping_before_the_assembly_assembles_once_when_run_again(
    fake_model: FakeModel,
    restated_2: Fix,
    steps: HeldSteps,
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    (step_id,) = steps.held
    assemble = work.assemble_ad

    def the_worker_stops(*args: Any, **kwargs: Any) -> ProducedItem:
        assemble(*args, **kwargs)
        raise WorkerStopped

    with monkeypatch.context() as stopping:
        stopping.setattr("agents.producer.assemble_ad", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(step_id)

    assert ads() == [1, 2]
    assert times_shown(ProducedItem.objects.get(kind="finished_ad", version=2)) == 1
    assert paid_for().count("make_broll_clip") == 2


# --- Words ------------------------------------------------------------------------------------


def test_context_names_note_and_fix_under_fixing_a_scene() -> None:
    context = (Path(__file__).resolve().parents[2] / "CONTEXT.md").read_text()
    language = context.split("## Language", 1)[1]
    fixing = language.split("### Fixing a scene", 1)[1].split("\n### ", 1)[0]

    assert "**Note**:" in fixing
    assert "**Fix**:" in fixing
