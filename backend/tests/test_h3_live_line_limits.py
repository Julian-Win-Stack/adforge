"""Scene edits and filming refuse underlength speech before new model charges."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem, Scene

from .conftest import HeldSteps, facts_ok, paid_for, results_of
from .test_producer_broll import calling, run
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)
SHORT = "This mug holds hot tea."


def start(fake: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(
        fake,
        say,
        (
            "start_scene_agent",
            {"scene": 2, "instruction": "Show the planned action.", "quotes": ["Go on"]},
        ),
    )
    run(fake, steps)


@pytest.mark.parametrize("kind", ["talking", "broll"])
def test_an_underlength_replacement_is_refused_before_fact_checking(
    scene_agent_ready: None,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    kind: str,
) -> None:
    Scene.objects.filter(number=2).update(
        kind=kind,
        broll_kind="showcase",
        usage="",
        result="",
        kind_why="The mug's inside needs a close view." if kind == "broll" else "",
        person_shown="no face" if kind == "broll" else "has face",
    )
    scene = Scene.objects.get(number=2)
    original = (scene.kind, scene.line, scene.shows)
    fields = {
        name: getattr(scene, name)
        for name in ("shows", "usage", "result", "broll_kind", "person_shown")
    }
    before = paid_for()
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("change_scene", {**fields, "line": SHORT})]),
        turn(calls=[("give_up", {"reason": "The owner needs to supply a longer line."})]),
    )

    start(fake_model, steps, say)

    assert "at least 12 words" in results_of("change_scene")[-1]
    scene.refresh_from_db()
    assert (scene.kind, scene.line, scene.shows) == original
    assert not {"fact_check", "make_broll_clip"} & set(paid_for()[len(before) :])


def test_an_underlength_checkpoint_cannot_buy_a_new_clip(
    scene_agent_ready: None,  # noqa: F811
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    def draw_for_the_saved_short_line() -> Any:
        # A checkpoint left by an earlier worker can contain a short historical line.
        Scene.objects.filter(number=2).update(line=SHORT)
        return turn(calls=[("draw_starting_picture", PICTURE_FORM)])

    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond("transcribe_line", {"text": SHORT})
    fake_model.respond(
        "scene_agent",
        draw_for_the_saved_short_line,
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The owner needs to supply a longer line."})]),
    )

    start(fake_model, steps, say)

    assert "at least 12 words" in results_of("film")[-1]
    assert "make_broll_clip" not in paid_for()
    assert not ProducedItem.objects.filter(kind=ProducedItem.Kind.CLIP).exists()
