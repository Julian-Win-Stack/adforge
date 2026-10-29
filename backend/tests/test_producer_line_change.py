"""Changing what a finished scene says (route B step 1, #138; fix redesign, ticket 08): a plan
the shop owner said yes to gives the scene its new line, and code makes its audio, transcript
and clip again from the starting picture it has (Julian, 2026-10-17), with no note and no
producer turn (tests/fixing.py); when
its clip lands the ad is assembled again by code. What a new line is estimated to cost. The
scene models are faked at the gateway; the clips are real tiny videos."""

from decimal import Decimal

import pytest

from agents.money import dollars
from gateway.catalog import MODEL_FOR_PURPOSE, transcription_cost_usd
from jobs.models import ProducedItem

from .conftest import HeldSteps
from .test_producer_broll import (
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_fixes import (
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


# --- Another plan on the scene while its new line is made --------------------------------------


# --- A new line whose audio runs too long -------------------------------------------------------


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
