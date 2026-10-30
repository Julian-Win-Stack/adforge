"""Which earlier clip assemble_ad names as still showing under a scene's voice (round 7
unlisted, item 5): the latest earlier one whose picture shows, never one cut to nothing."""

from typing import Any

from agents.producer import _voice_over_the_clip_before


def cut(
    scene: int, start: float, end: float, voice_start: float, voice_end: float
) -> dict[str, Any]:
    return {
        "scene": scene,
        "start": start,
        "end": end,
        "voice_start": voice_start,
        "voice_end": voice_end,
    }


def test_a_scene_cut_to_nothing_isnt_named_as_the_clip_still_showing() -> None:
    # Scene 2's clip runs past its line through all of scene 3's and into scene 4's.
    said = _voice_over_the_clip_before(
        [
            cut(1, 0, 4, 0, 4),
            cut(2, 4, 10, 4, 7),
            cut(3, 10, 10, 7, 9),
            cut(4, 10, 12, 9, 12),
        ]
    )

    assert "Scene 2's clip plays on past its line, so none of its picture shows" in said
    assert "Scene 2's clip plays on past its line, so its picture shows from 10" in said
    assert "Scene 3's clip" not in said
