"""Script happy scene-agent turns at the gateway; the real tools do all scene work."""

import re
from collections.abc import Callable

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps


def script_scene_agents(fake_model: FakeModel, *, scenes: int = 1) -> None:
    """Queue one draw and one film for each scene; external checks pass by default."""
    from gateway.models import ModelCall

    from .test_scene_film import FILM
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    def heard(_: object) -> dict[str, str]:
        call = ModelCall.objects.filter(purpose="make_broll_clip").latest("id")
        match = re.search(r"<d>\[English\] (.*?)</d>", call.handoff["prompt"], re.DOTALL)
        assert match is not None
        return {"text": match[1]}

    fake_model.answer_unscripted("transcribe_line", heard)
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.answer_unscripted(
        "scene_agent",
        lambda _: turn(
            calls=[
                (
                    "give_up",
                    {
                        "reason": "The scripted scene could not make its clip.",
                    },
                )
            ]
        ),
    )
    for _ in range(scenes):
        fake_model.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
            turn(calls=[("film", FILM)]),
        )


def make_scene_clip(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    scene: int = 2,
    instruction: str = "Show this scene's planned action.",
    quotes: list[str] | None = None,
) -> None:
    """Start and finish a ready scene through the producer and the scene agent."""
    from .test_producer_broll import calling, run

    script_scene_agents(fake_model)
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": scene,
                "instruction": instruction,
                "quotes": quotes or ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
