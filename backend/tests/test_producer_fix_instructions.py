"""The producer's instructions for fixing a scene after the ad is shown (route B step 1, #137).
Instructions can't be tested like code, so these check what can be: the key sentences are in
what the producer's model is given, pinned word for word so a later edit shows up as a diff;
every refusal a tool gives on the way to a fix has a sentence saying what to do next; and the
whole loop runs with producer turns scripted to follow the instructions word for word, which
proves the instructions describe calls the tools accept. Whether a real model follows them is
measured by the producer eval (tests/eval_producer_fixes.py)."""

from pathlib import Path
from typing import Any

import pytest

from agents.producer import INSTRUCTIONS
from gateway.fake import FakeModel, turn
from gateway.types import TurnReply, TurnRequest
from jobs.models import Fix, ProducedItem

from .conftest import HeldSteps, paid_for, results_of
from .test_producer_broll import (
    TALKING_CHOICE,
    checked,  # noqa: F401 (a fixture)
    run,
)
from .test_producer_fixes import (
    FIX_1,
    FIX_2,
    NOTE_1,
    NOTE_2,
    REWRITE,
    Say,
    ads,
    clip_with,
    last_step,
    owner_says,
    picture_with,
    restating,
    shown,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.django_db(transaction=True)

# Which step fixes a note, from the kind of note (Julian 21:00 Oct 11; "zoom in" is a closer
# starting picture, Julian 21:35, because the camera never moves, a LOCKED rule).
PICK_THE_STEP = (
    "When the shop owner writes about such a scene, pick the smallest thing to make again "
    "that fixes it: how a scene that shows the product moves, or what happens in it, is a new "
    "clip; what a scene's picture shows, such as the wrong side, an object that couldn't be "
    "real or the wrong thing on screen, is a new starting picture, then its clip."
)
ZOOM_IN = '"Zoom in" means a new starting picture, closer: the camera never moves.'
# One restatement for every note, with the cost, before anything is made (Julian 20:51, 21:51).
RESTATE = (
    "Before you reply, call restate_fix once, with a fix for every scene they wrote about "
    "that you can fix."
)
TELL_THEM = (
    "Then tell them in their own words what you'll change in each scene, what it will cost, "
    "and anything you can't do, and wait for their answer."
)
NEVER_THE_STEP = "Never tell them what is made again."
CORRECTED = "If they correct you, restate it again."
NOTHING_BEFORE_THE_YES = "Make nothing until they say yes."
AFTER_THE_YES = (
    "When they say yes, pass each scene's fix word for word as the note of its redo, for every "
    "scene at the same time: make_clip for a new clip, or make_starting_picture for a new "
    "starting picture, then make_clip with no note once the picture is ready."
)
# No stop button in step 1: a note on a scene being made waits for it (Julian 21:44).
STILL_BEING_MADE = (
    "If that scene is still being made, say you'll start as soon as it's finished, and start "
    "it when you're told it is."
)
# The ad is assembled by code when a fix's clip lands (Julian 22:24, #135).
ASSEMBLED = "When a fix's clip is ready, the ad is assembled again and shown to them on its own."
COST_AND_ASK = "Tell them what the fix cost, and ask them to tell you if it's fixed."
NEVER_FIXED = "Never say it is: you can't see pictures or videos."
# No escalation on a second note (Julian 21:53, "option B ... at least for now").
STILL_WRONG = (
    "If they say it still isn't right, restate a new fix for that scene the same way, making "
    "the same thing again."
)
# The "I can't" list (#137): each item is a refusal or a limit in code or docs/limitations.md.
CANT = (
    "What you can't do, said plainly the same way each time: see pictures or videos; use new "
    "product photos once the ad is planned; give the video maker a photo, as it only starts "
    "from the starting picture; or change what the person does in a scene where they talk, "
    "only how it looks or what they hold, through a new starting picture."
)
NOT_YET = (
    "Not yet: changing a scene's words, the music, the text shown over a scene, the voice, the "
    "person, the number of scenes or the ad's length."
)
SLOWER = "Slower can't be done: every clip plays at real-time speed."

PINNED = [
    PICK_THE_STEP,
    ZOOM_IN,
    RESTATE,
    TELL_THEM,
    NEVER_THE_STEP,
    CORRECTED,
    NOTHING_BEFORE_THE_YES,
    AFTER_THE_YES,
    STILL_BEING_MADE,
    ASSEMBLED,
    COST_AND_ASK,
    NEVER_FIXED,
    STILL_WRONG,
    CANT,
    NOT_YET,
    SLOWER,
]

NOTE_3_WORDS = "And in the last scene she should say it's on sale."
FIX_2_AGAIN = "In scene 2, the tea pours in a thin stream from the spout right into the mug."


@pytest.mark.parametrize("sentence", PINNED)
def test_a_fix_sentence_is_in_the_producers_instructions_word_for_word(sentence: str) -> None:
    assert sentence in " ".join(INSTRUCTIONS.split())


def test_the_producers_model_is_given_the_fix_instructions(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[str] = []
    take_turn = fake_model.take_turn

    def keeping(request: TurnRequest) -> TurnReply:
        given.append(request.instructions)
        return take_turn(request)

    monkeypatch.setattr(fake_model, "take_turn", keeping)

    owner_says(fake_model, say, NOTE_2)

    assert given
    for instructions in given:
        assert all(sentence in " ".join(instructions.split()) for sentence in PINNED)


# Each refusal a tool can give on the way to a fix, and the sentence that tells the producer
# what to do instead. The refusal is produced through the chat, so both halves are real.
@pytest.mark.parametrize(
    ("calls", "refused", "told"),
    [
        pytest.param(
            [restating((1, "In scene 1, she waves at the camera.", "clip"))],
            "scene 1 shows the person talking, and a talking clip's movement can't be changed",
            CANT,
            id="an action in a talking scene",
        ),
        pytest.param(
            [restating((3, "In scene 3, she says it's on sale.", "line"))],
            "changing a scene's line isn't built yet",
            NOT_YET,
            id="the words",
        ),
        pytest.param(
            [restating((2, FIX_2, "clip")), clip_with(2, FIX_2)],
            "scene 2's fix hasn't been restated to the shop owner yet",
            TELL_THEM,
            id="a redo before the restatement",
        ),
        pytest.param(
            [clip_with(2, FIX_2)],
            "no fix was restated for it. Call restate_fix first",
            RESTATE,
            id="a redo with no fix",
        ),
    ],
)
def test_a_refusal_on_the_way_to_a_fix_has_a_sentence_saying_what_to_do(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Say,
    calls: list[tuple[str, dict[str, Any]]],
    refused: str,
    told: str,
) -> None:
    paid = paid_for()

    fake_model.respond("produce", *[turn(calls=[call]) for call in calls])
    owner_says(fake_model, say, NOTE_2)

    assert any(
        refused in result for tool in ("restate_fix", "make_clip") for result in results_of(tool)
    )
    assert told in " ".join(INSTRUCTIONS.split())
    assert paid_for() == paid


def test_a_redo_before_the_answer_and_a_picture_fix_as_a_clip_note_are_covered(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "picture"))]),
        turn(says="I'll make the tea pour, for about $0.50.", calls=[picture_with(2, FIX_2)]),
    )
    owner_says(fake_model, say, NOTE_2)
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))

    assert results_of("make_starting_picture")[-1].startswith(
        "Refused: the shop owner hasn't answered since scene 2's fix was restated to them."
    )
    assert results_of("make_clip")[-1].startswith(
        "Refused: scene 2's fix was restated as a new starting picture, not a new clip. Make "
        "its starting picture with the fix as the note, then its clip with no note."
    )
    assert steps.held == []
    instructions = " ".join(INSTRUCTIONS.split())
    assert NOTHING_BEFORE_THE_YES in instructions
    assert AFTER_THE_YES in instructions


def test_the_whole_fix_loop_runs_as_the_instructions_say(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    clips_paid = paid_for().count("make_broll_clip") + paid_for().count("make_talking_clip")

    # Three notes in one message: a picture, a clip and the words, which can't change yet.
    # One restate_fix for what can be fixed, then the restatement, with the cost and the can't.
    fake_model.respond(
        "produce",
        turn(calls=[restating((1, FIX_1, "picture"), (2, FIX_2, "clip"))]),
        turn(
            says="In scene 1 she'll hold the mug up beside her face with both hands, and in "
            "scene 2 the tea will pour into the mug with steam rising, for about $0.65 in all. "
            "I can't change what she says yet. Shall I go ahead?"
        ),
    )
    say(f"{NOTE_1} {NOTE_2} {NOTE_3_WORDS}")
    assert Fix.objects.count() == 2
    assert steps.held == []

    # The yes: both redos at the same time, each with its fix word for word.
    fake_model.respond(
        "produce",
        turn(calls=[picture_with(1, FIX_1), clip_with(2, FIX_2)]),
        turn(says="Started both."),
    )
    say("Yes, go ahead.")
    assert len(steps.held) == 2

    # Scene 2's clip lands first: the ad waits for scene 1. Scene 1's picture lands: the
    # producer makes its clip with no note.
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond(
        "produce",
        turn(says="Scene 2 is done; waiting on scene 1."),
        turn(calls=[clip_with(1, None)]),
        turn(says="Making scene 1's clip."),
    )
    steps.run_next()
    steps.run_next()
    assert ads() == [1]

    # Scene 1's clip lands: code assembles the new ad and tells the producer to ask.
    run(fake_model, steps)
    assert ads() == [1, 2]
    assert last_step("clip", 1).result.endswith("Ask them to tell you if it's fixed.")

    # Still not right: the same step again, with the new fix.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2_AGAIN, "clip"))]),
        turn(says="I'll make the tea pour in a thin stream right into the mug, for about $0.40."),
    )
    say("Scene 2 still looks off, the tea misses the mug.")
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2_AGAIN))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    assert ads() == [1, 2, 3]
    assert [fix.redo for fix in Fix.objects.order_by("pk")] == ["picture", "clip", "clip"]
    assert last_step("clip", 2).fix == Fix.objects.order_by("pk").last()
    # Scene 1's clip once, scene 2's twice, and nothing for scene 3.
    paid = paid_for()
    assert paid.count("make_broll_clip") + paid.count("make_talking_clip") == clips_paid + 3
    assert last_step("clip", 3).fix is None


def test_a_note_on_a_scene_being_made_waits_and_starts_when_its_told_it_finished(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    owner_says(fake_model, say, NOTE_2, restating((2, FIX_2, "clip")))
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2))
    assert len(steps.held) == 1

    # A second note on scene 2 while its clip is still being made: restated, and the yes
    # waits for the clip being made.
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2_AGAIN, "clip"))]),
        turn(
            says="I'll make the tea pour in a thin stream right into the mug, for about $0.40. "
            "I'll start as soon as scene 2's current clip finishes."
        ),
    )
    say("Actually, the tea should pour in a thin stream.")
    owner_says(fake_model, say, "Yes", clip_with(2, FIX_2_AGAIN))
    assert results_of("make_clip")[-1] == (
        "Refused: scene 2's clip is already being made. You'll be told when it's ready. "
        "Nothing was done."
    )
    assert len(steps.held) == 1

    # Told the clip being made has finished, the producer starts the new fix.
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fake_model.respond(
        "produce", turn(calls=[clip_with(2, FIX_2_AGAIN)]), turn(says="Started the new one.")
    )
    steps.run_next()
    assert len(steps.held) == 1
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    assert last_step("clip", 2).fix == Fix.objects.order_by("pk").last()
    assert last_step("clip", 2).status == "finished"
    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None
    newest = ProducedItem.objects.filter(kind="clip", scene__number=2).order_by("version").last()
    assert newest is not None and newest.pk in [cut["clip"] for cut in ad.cuts]


def test_the_fix_limits_are_written_down_and_dated() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    limitations = (docs / "limitations.md").read_text()
    for limit in [
        "**No stop button for a fix.**",
        "**The producer never says a fix worked.**",
        "**No escalation on a repeated note.**",
    ]:
        entry = next(line for line in limitations.splitlines() if limit in line)
        assert "2026-10-11" in entry
    adr = (docs / "adr" / "0005-one-agent-first.md").read_text()
    assert "_Note, 2026-10-12 (route B step 1, #137): nothing stops a running step yet." in adr
