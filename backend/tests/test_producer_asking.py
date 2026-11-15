"""Plans for product actions, without owner questions."""

from typing import Any

from .conftest import PLAN, a_plan_with, broll


def broll_plan(
    shows: str,
    needs: list[dict[str, Any]],
    colour_photos: list[int],
    owner_part: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The mug plan with its second scene showing `shows`, needing `needs` and with
    `owner_part`."""
    scenes = [
        broll({**scene, "shows": shows, "needs": needs, "owner_part": owner_part})
        if number == 2
        else scene
        for number, scene in enumerate(PLAN["plan"]["scenes"], start=1)
    ]
    return a_plan_with(scenes=scenes, colour_photos=colour_photos)
