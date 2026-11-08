"""Which scenes are shown the page's how-to photos, and the picture room they leave (#477):
pure logic, no database."""

import pytest

from agents.scene_agent import media
from gateway.types import Shown, ToolUse
from jobs.models import Scene


@pytest.mark.parametrize(
    ("scene", "uses"),
    [
        pytest.param(Scene(kind="broll", broll_kind="does a job"), True, id="B-roll doing a job"),
        pytest.param(
            Scene(kind="broll", broll_kind="showcase", usage="Fill it with the mix."),
            True,
            id="B-roll with a usage",
        ),
        pytest.param(Scene(kind="broll", broll_kind="showcase"), False, id="B-roll, no usage"),
        pytest.param(Scene(kind="talking", product_state="in use"), True, id="talking, in use"),
        pytest.param(
            Scene(kind="talking", product_state="out of the box"), False, id="talking, not in use"
        ),
    ],
)
def test_a_scene_uses_the_product_when_it_does_a_job_has_a_usage_or_is_in_use(
    scene: Scene, uses: bool
) -> None:
    assert media.uses_product(scene) is uses


def look(key: str) -> ToolUse:
    return ToolUse(
        call_id=key,
        tool="look_at_library",
        arguments={"item": 1},
        result=f"Library item {key}.",
        pictures=[Shown(label=key, key=key)],
    )


def test_how_to_photos_filling_the_library_room_leave_no_room_and_never_less() -> None:
    # More how-to photos than the library's share: no opened look keeps its pictures.
    fitted = media._fit(
        [look("a"), look("b")],
        reserved=media._library_room() + 1,
        library_reserved=media._library_room() + 1,
    )
    assert [len(each.pictures) for each in fitted if isinstance(each, ToolUse)] == [0, 0]
    assert all(media.NOT_SHOWN in each.result for each in fitted if isinstance(each, ToolUse))
