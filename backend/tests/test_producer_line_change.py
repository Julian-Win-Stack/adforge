"""A changed B-roll line is voiced and heard again, then its scene agent draws a current
starting picture and films in its remembered chat. Version swaps keep each clip's original
picture and sent record. Every outside model is faked at the gateway."""

from decimal import Decimal

import pytest

from agents.money import dollars
from gateway.catalog import MODEL_FOR_PURPOSE, transcription_cost_usd
from gateway.fake import FakeModel, turn
from jobs import library, work
from jobs.models import ProducedItem, Scene

from .conftest import NO_CHOICES, HeldSteps, facts_ok, handoffs, paid_for
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
    clips_asked,
    run,
)
from .test_producer_clip_from_note import HOVERS, NEW_MOTION
from .test_producer_fixes import (
    shown,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import clip, status
from .test_producer_plan_edits import Say, answer_of, owner_says

pytestmark = [pytest.mark.django_db(transaction=True)]

NOTE_3 = "The last scene should say it's on sale, not just the price."
LINE_3 = "On sale now for $19.00."
NOTE_2 = "Scene 2 should say it's oven safe too."
LINE_2_NEW = "Hand-thrown, holds 350 ml, oven and dishwasher safe for every single everyday drinks."
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
    """Edit and check the words, then refilm through the scene's live agent."""
    from .scene_script import make_scene_clip

    owner_says(fake_model, say, "Change the line.", ("edit_scene", {"scene": number, "line": line}))
    fake_model.respond("fact_check", facts_ok(number))
    owner_says(fake_model, say, "Go on", ("run_planning_checks", NO_CHOICES))
    make_scene_clip(fake_model, steps, say, scene=number)


def note_clip(fake_model: FakeModel, steps: HeldSteps, say: Say) -> ProducedItem:
    """Pass the owner's exact note into the scene's same chat, then draw and film it."""
    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "beats": [NEW_MOTION]}})]),
    )
    owner_says(
        fake_model,
        say,
        HOVERS,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": NEW_MOTION,
                        "quotes": [HOVERS],
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    return clip(2)


# --- A scene showing the product gets a current starting picture -------------------------------


def test_a_broll_scenes_new_line_gets_a_current_picture_and_keeps_earlier_notes(
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
    assert made.picture is not None and noted.picture is not None
    assert made.picture.file != noted.picture.file
    sent = library.sent_list_of(made)
    assert sent is not None and sent.starting_picture is not None
    assert sent.starting_picture.file == made.picture.file
    assert clips_asked("starting_picture")[-1] == made.picture.file
    assert clips_asked("example_pictures")[-1] == []
    assert HOVERS in str(handoffs("scene_agent")[-1])
    assert LINE_2_NEW in str(handoffs("scene_agent")[-1])
    new_costs = paid_for()[paid:]
    assert new_costs.count("transcribe_line") == 1
    assert "speak_line" not in new_costs
    assert new_costs.count("draw_scene_picture") == 1
    assert new_costs.count("make_broll_clip") == 1
    assert "rewrite_clip_prompt" not in new_costs
    assert "write_first_send_prompt" not in new_costs
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
    assert made.file == said_anew.file and made.sent == said_anew.sent and made.sent != first.sent
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
