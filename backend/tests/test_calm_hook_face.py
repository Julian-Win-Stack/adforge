"""The hook's face stays calm and natural (Julian graded 5 of 5 re-filmed hooks pass, Nov 9
2026). Round 13 hook prompts wrote face moves in beats, feeling and after_last_word, always
eyebrow raise, grin or smirk, then nod, and the hooks looked acted."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _film_boxes_offered_for_the_hook(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> dict[str, str]:
    """Start the scene agent on scene 1, the hook, through the chat; return the film form's
    box descriptions it was offered."""
    fake_model.respond("scene_agent", turn(calls=[("give_up", {"reason": "Stop here."})]))
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 1, "instruction": "Open on the problem.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)
    request = next(each for each in fake_model.turns_taken if each.purpose == "scene_agent")
    film = next(tool for tool in request.tools if tool.name == "film")
    boxes: dict[str, Any] = film.arguments.model_json_schema()["$defs"]["VideoForm"]["properties"]
    return {name: box.get("description", "") for name, box in boxes.items()}


def _hook_part(text: str, start: str, end: str) -> str:
    assert start in text, text
    after = text[text.index(start) :]
    return after[: after.index(end)] if end in after else after


def test_the_hook_scene_is_told_to_keep_her_face_calm_and_write_it_only_in_feeling(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents.scene_forms import HOOK_PART

    boxes = _film_boxes_offered_for_the_hook(fake_model, steps, say)
    hook = f"({HOOK_PART})"

    # Feeling: a natural, relaxed face with one short list, nothing big.
    feeling = _hook_part(boxes["feeling"], f"The hook {hook}", "A demo:")
    for rule in (
        "natural and relaxed",
        "never acted for the camera",
        "at most one small face cue",
        "a slight smile, a light laugh or a small head tilt",
        "natural blinks and small head and shoulder shifts",
        "no wide eyes",
        "no big grin",
        "no smirk",
        "no eyebrow raise",
        "no nod after the line",
    ):
        assert rule in feeling, rule
    # The 2-to-4 acted moves rule leaves the hook out; problem scenes keep their acted face.
    assert "except the hook" in boxes["feeling"]
    assert (
        "A problem scene: amused or a bit fed up (raises her eyebrows, smirks" in boxes["feeling"]
    )

    # Beats: the hook's face is written once, in feeling, never in the beats.
    beats = _hook_part(boxes["beats"], f"In the hook {hook} the beats", " She moves")
    assert "only her free hand, shoulders and body" in beats
    assert "her face and head moves go only in feeling" in beats

    # After the last word: a calm settle with no new face reaction, no fixed product move.
    after = boxes["after_last_word"]
    settle = _hook_part(after, f"In the hook {hook}", "With an end picture, end in")
    for rule in (
        "a calm settle with no new face reaction",
        "she keeps looking at the lens",
        "if she holds the product, it settles back where it was",
        "With an end picture, its pose wins",
    ):
        assert rule in settle, rule
    assert "lowers the product" not in after
