"""Changing what a finished scene says, on the producer's path (spec #247 point 9; ticket
#262): the producer edits the scene's line, then makes its audio, transcript and clip again.
A scene showing the product keeps its pictures: its new clip is sent what its clip was, for
the line's new seconds, with no prompt written again. Going back to a version brings back
its clip and what it was sent. What a transcription is estimated to cost. The producer is
scripted and every scene model faked at the gateway; the clips are real tiny videos."""

from decimal import Decimal

import pytest

from agents.money import dollars
from gateway.catalog import MODEL_FOR_PURPOSE, transcription_cost_usd
from gateway.fake import FakeModel
from jobs import cut_and_send, library, work
from jobs.models import ProducedItem, Scene

from .conftest import NO_CHOICES, SENT_LIST_JUDGED, HeldSteps, facts_ok, paid_for
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    clips_asked,
    run,
)
from .test_producer_clip_from_note import HOVERS, NEW_MOTION, REWRITE
from .test_producer_fixes import (
    shown,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import clip, status
from .test_producer_plan_edits import Say, answer_of, owner_says

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_3 = "The last scene should say it's on sale, not just the price."
LINE_3 = "On sale now for $19.00."
NOTE_2 = "Scene 2 should say it's oven safe too."
LINE_2_NEW = "Hand-thrown, holds 350 ml, oven and dishwasher safe."
OLD_LINE_3 = "Yours for $24.00."
MUG_UP = "She holds the mug up beside her face."
SLOWER_2 = "The mug turns more slowly."


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


def new_line(fake_model: FakeModel, steps: HeldSteps, say: Say, number: int, line: str) -> None:
    """The producer gives scene `number` its new `line`, then makes its audio, transcript and
    clip again, one tool a turn as the shop owner says go on."""
    owner_says(fake_model, say, "Change the line.", ("edit_scene", {"scene": number, "line": line}))
    fake_model.respond("fact_check", facts_ok(number))
    owner_says(
        fake_model,
        say,
        "Go on",
        ("run_planning_checks", NO_CHOICES),
        ("make_line_audio", {"scene": number}),
    )
    run(fake_model, steps)
    for tool in ("transcribe_line_audio", "make_clip"):
        owner_says(fake_model, say, "Go on", (tool, {"scene": number}))
        run(fake_model, steps)


def note_clip(fake_model: FakeModel, steps: HeldSteps, say: Say) -> ProducedItem:
    """Scene 2's clip made again from the shop owner's note, its prompt written again."""
    owner_says(fake_model, say, HOVERS, ("make_clip", {"scene": 2, "note": HOVERS}))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)
    return clip(2)


# --- A scene showing the product keeps its pictures --------------------------------------------


def test_a_broll_scenes_new_line_is_sent_what_its_clip_was_and_pays_for_no_rewrite(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    noted = note_clip(fake_model, steps, say)
    assert noted.sent is not None
    paid = len(paid_for())

    new_line(fake_model, steps, say, 2, LINE_2_NEW)

    made = clip(2)
    assert made.pk != noted.pk
    assert made.step is not None and made.step.line == LINE_2_NEW
    # Spec #247 point 9: the new line keeps the pictures, and the clip is sent what the note's
    # clip was, its prompt's words as the rewrite wrote them; no picture is drawn.
    assert made.picture is None
    assert made.sent == noted.sent
    sent = library.sent_list_of(made)
    assert sent is not None and cut_and_send.words_of(sent.prompt) == NEW_MOTION
    assert clips_asked("starting_picture")[-1] is None
    assert clips_asked("example_pictures")[-1] == clips_asked("example_pictures")[-2]
    # The new line is checked against the page, as any line.
    assert paid_for()[paid:] == [
        "fact_check",
        "speak_line",
        "transcribe_line",
        "make_broll_clip",
        "collect_broll_clip",
        *SENT_LIST_JUDGED,
    ]
    assert status(2) == "finished"


def test_going_back_after_a_line_change_brings_back_what_the_version_was_sent_and_says_so(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    first = clip(2)
    new_line(fake_model, steps, say, 2, LINE_2_NEW)
    said_anew = clip(2)
    note_clip(fake_model, steps, say)
    assert clip(2).sent != said_anew.sent
    paid = len(paid_for())

    # v1 no longer says the line: the producer can't use it for nothing.
    owner_says(fake_model, say, "The first one.", ("use_version", {"scene": 2, "version": 1}))
    refused = "Refused: scene 2's v1 first version has no clip that still says the scene's line"
    assert answer_of("use_version").startswith(refused)
    # v2, the new line's, does: it comes back with what it was sent, labelled as gone back to.
    owner_says(fake_model, say, "The one before.", ("use_version", {"scene": 2, "version": 2}))

    made = clip(2)
    assert made.file == said_anew.file and made.sent == said_anew.sent == first.sent
    assert made.step is not None and made.step.line == LINE_2_NEW
    top = work.versions(Scene.objects.get(number=2))[-1]
    assert top.clip == made and top.label == "v4 back to v2"
    assert paid_for()[paid:] == []
    assert status(2) == "finished"


# --- The estimate -----------------------------------------------------------------------------


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
