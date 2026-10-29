"""Going back to an earlier starting picture (#143, fix redesign decision 8): the scene that
test_versions.py goes back on, with pictures and clips 2 and 3 made by the producer from
notes. The ad is first made through the chat; the scene models are faked at the gateway, but
the clips are real tiny videos and ffmpeg runs for real."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from jobs import work
from jobs.models import ProducedItem, Scene

from .conftest import HeldSteps, results_of
from .test_producer_broll import TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    HIGHER,
    assembled,  # noqa: F401 (a fixture)
    new_picture,
    status,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checked")]

SLOWER = "Pour the tea slower."
BACK = "The higher one was better."
# Spec #210 point 19: a scene that shows the product has no starting picture to bring back, so
# these run on talking scene 3.
SCENE = 3


def version(kind: str, number: int) -> ProducedItem:
    """Scene 3's `kind` at version `number`."""
    return ProducedItem.objects.get(kind=kind, scene__number=SCENE, version=number)


def versions(kind: str) -> list[int]:
    return list(
        ProducedItem.objects.filter(kind=kind, scene__number=SCENE)
        .order_by("version")
        .values_list("version", flat=True)
    )


def labels() -> list[str]:
    return [each.label for each in work.versions(Scene.objects.get(number=SCENE))]


@pytest.fixture
def higher_then_slower(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    assembled: ProducedItem,  # noqa: F811
) -> None:
    """Scene 3 got a picture for "hold it higher" and its clip, then a picture for "pour the
    tea slower" and its clip, each made by the producer with the note: pictures and clips 2
    and 3."""
    new_picture(fake_model, steps, say, SCENE, HIGHER)
    new_picture(fake_model, steps, say, SCENE, SLOWER)
    assert (versions("starting_picture"), versions("clip")) == ([1, 2, 3], [1, 2, 3])
    assert status(SCENE) == "finished"


def test_the_producer_brings_back_a_picture_on_a_scene_with_a_clip_for_nothing(
    fake_model: FakeModel, say: Callable[..., None], higher_then_slower: None
) -> None:
    # Nothing locks once a scene has a clip (spec #247 point 5): the picture already made for
    # the line with no note is handed back, free, and the scene waits for a clip made from it.
    fake_model.respond(
        "produce",
        turn(calls=[("make_starting_picture", {"scene": SCENE, "note": None})]),
        turn(says="On it."),
    )
    say("Make scene 3's picture again")

    assert results_of("make_starting_picture")[-1].startswith(
        "Scene 3's starting picture was already made for this line with no note (version 1), "
        "so it is the scene's starting picture again, as version 4, and nothing was made or "
        "paid for again."
    )
    assert versions("starting_picture") == [1, 2, 3, 4]
    assert status(SCENE) == "planned"
