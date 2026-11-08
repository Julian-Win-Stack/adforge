"""Happy scene-agent model turns for owner-part gate tests; all tools stay real."""

from collections.abc import Iterator

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES


@pytest.fixture(autouse=True)
def part_scene_agent(fake_model: FakeModel) -> None:
    def turns() -> Iterator[Turn]:
        # Several approved-part corrections can make a new clip in the same test.
        for _ in range(6):
            yield turn(calls=[("draw_starting_picture", PICTURE_FORM)])
            yield turn(calls=[("film", FILM)])

    from jobs.models import ProducedItem

    def heard(_: object) -> dict[str, str]:
        clip = ProducedItem.objects.filter(kind="clip").latest("id")
        assert clip.step is not None
        return {"text": clip.step.line}

    fake_model.answer_unscripted("transcribe_line", heard)
    scripted = turns()
    fake_model.answer_unscripted("scene_agent", lambda _: next(scripted))
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)


def script_part_picture(fake_model: FakeModel, item: int) -> None:
    """The scripted agent deliberately selects the approved part from the library."""
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {
                        **PICTURE_FORM,
                        "picks": [{"item": item, "take": "Use the approved part's shape."}],
                    },
                )
            ]
        ),
        turn(calls=[("film", FILM)]),
    )
