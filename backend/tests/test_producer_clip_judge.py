"""A B-roll scene's clip is judged right after it is made, driven through the chat (#109): its
cost is the clip step's, nothing is judged with quality checks off, and the clip step's trace
shows the prompt the clip was made from. Talking clips aren't judged. Every B-roll clip now
carries its sent list and is judged against it (tests/test_clip_judge_references.py): the
tests of SIDES, which judged a clip beside the photo its starting picture was drawn from, are
gone with the starting picture (spec #210 point 19; SIDES itself goes in #241). The
producer's model, the scene models and the judge are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents.money import dollars
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs.models import ProducedItem, SceneStep

from .conftest import (
    HeldSteps,
)
from .test_producer_broll import (
    LINE_2,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .traced import Traces

pytestmark = pytest.mark.django_db(transaction=True)


def clip_of(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scene: int
) -> ProducedItem:
    """Make `scene`'s audio, transcript and clip (and a talking scene's starting picture); gives
    the clip."""
    made_ready(fake_model, steps, say, (scene,))
    calling(fake_model, say, ("make_clip", {"scene": scene}))
    run(fake_model, steps)
    return ProducedItem.objects.get(kind="clip", scene__number=scene)


def ruling(clip: ProducedItem) -> dict[str, Any]:
    """The judge's ruling kept on `clip`."""
    judged: dict[str, Any] | None = clip.ruling
    assert judged is not None, "the clip was judged"
    return judged


@pytest.fixture
def judged(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> ProducedItem:
    """Scene 2's clip, showing the mug, made and judged."""
    return clip_of(fake_model, steps, say, 2)


def test_the_judges_cost_is_the_clip_steps(judged: ProducedItem) -> None:
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    for check in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert check.tool_call_id == step.tool_call_id
    assert f"It cost {dollars(step.tool_call.cost_usd())}." in step.result


# That a talking clip isn't judged, and so carries no ruling, is proved in
# test_producer_auto_fix.py::test_a_talking_scene_is_left_as_it_is.


def test_no_clip_is_judged_with_quality_checks_switched_off(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Any,
) -> None:
    settings.QUALITY_CHECKS = False

    clip = clip_of(fake_model, steps, say, 2)

    assert not ModelCall.objects.filter(purpose__startswith="check_clip_").exists()
    assert clip.ruling is None


def test_a_clip_steps_trace_shows_the_video_prompt_its_clip_was_made_from(
    traces: Traces, judged: ProducedItem
) -> None:
    (step,) = [
        found
        for trace in traces.all()
        for found in trace.find(name="scene step")
        if found.metadata()["step"] == "clip"
    ]
    (sent,) = ModelCall.objects.filter(purpose="make_broll_clip")
    assert sent.handoff["prompt"]
    assert step.value("input") == {
        "kind": "clip",
        "scene": 2,
        "line": LINE_2,
        "prompt": sent.handoff["prompt"],
    }
    assert step.value("output") == {"result": SceneStep.objects.get(kind="clip").result}
