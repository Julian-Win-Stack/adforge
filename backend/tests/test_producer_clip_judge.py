"""A B-roll scene's clip is judged right after it is made, driven through the chat (#109): its
cost is the clip step's, nothing is judged with quality checks off, and the clip step's trace
shows the prompt the clip was made from. Talking clips aren't judged. Every B-roll clip
carries its sent list and is judged against it (what each ask is shown and told:
tests/test_clip_judge_references.py); here, how the judge runs whatever it is asked: each
check once, a restart paying for nothing twice, an ask or a whole ruling that breaks, and the
words and models a ruling names. The SIDES check, which judged a clip beside the photo its
starting picture was drawn from, went with the starting picture (spec #210 point 19, #241).
The producer's model, the scene models and the judge are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from agents.money import dollars
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs import clip_judge
from jobs.clip_judge import wording_version
from jobs.models import ProducedItem, SceneStep

from .conftest import (
    CLIP_PRODUCT_OK,
    SENT_LIST_JUDGED,
    HeldSteps,
    WorkerStopped,
    paid_for,
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


def judge_calls() -> list[str]:
    return [purpose for purpose in paid_for() if purpose.startswith("check_clip_")]


WRONG = "The press shows a second handle on its back at 2.1 s; a real one has one."
FAILING = {**CLIP_PRODUCT_OK, "verdict": "fail", "fault": WRONG, "compared_against": "Image 1"}


def test_a_broll_clip_is_judged_once_by_each_check_with_no_vote(judged: ProducedItem) -> None:
    assert judge_calls() == SENT_LIST_JUDGED
    assert ruling(judged)["verdict"] == "pass"


def test_the_ruling_says_which_words_and_models_judged_it(judged: ProducedItem) -> None:
    assert ruling(judged)["wording"] == wording_version()
    assert ruling(judged)["models"] == {
        check: MODEL_FOR_PURPOSE[f"check_clip_{check}"]
        for check in ("references", "product", "motion")
    }
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.model == MODEL_FOR_PURPOSE[call.purpose]


def test_a_first_clip_the_judge_couldnt_check_is_kept_and_the_producer_told_so(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    """No usable answer to settle a check is no fail, so the clip is kept as it is (Julian,
    2026-10-14: "treat that as pass"), and the shop owner is told it wasn't checked."""
    for check in ("check_clip_product", "check_clip_motion"):
        fake_model.respond(check, UnusableReply("no answer", input_tokens=1, output_tokens=1))

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "not_checked"
    assert paid_for().count("make_broll_clip") == 1
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert step.status == "finished"
    assert step.result.endswith(
        " The clip judge couldn't check this clip, so it is kept unchecked. Scene 2 is "
        "finished. Tell the shop owner."
    )


def test_a_clip_judged_again_after_the_worker_stopped_pays_for_no_answer_twice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    # The worker stops in PRODUCT's ask, after the first question is answered and while
    # MOTION's, asked at the same time, is.
    fake_model.respond("check_clip_product", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    pictures = ruling(ProducedItem.objects.get(kind="clip", scene__number=2))["pictures"]

    # The step is handed out again, as after a restart.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(SceneStep.objects.get(kind="clip", scene__number=2).pk)

    assert paid_for().count("make_broll_clip") == 1
    assert sorted(judge_calls()) == sorted(SENT_LIST_JUDGED)
    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert ruling(clip)["verdict"] == "pass"
    # Shown the same pictures, cut once.
    assert ruling(clip)["pictures"] == pictures


def test_an_ask_failing_for_a_reason_nobody_expected_goes_unchecked_and_the_rest_still_count(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", FAILING)
    fake_model.respond("check_clip_motion", RuntimeError("the judge broke"))

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "fail"
    assert ruling(clip)["checks"]["product"] == "fail"
    assert ruling(clip)["checks"]["real_motion"] == "not_checked"
    assert ruling(clip)["answers"]["motion"] == [None]
    assert clip_judge.still_failing(ruling(clip)) == [WRONG]


def test_a_clip_that_cant_be_judged_at_all_is_kept_as_not_checked_with_a_whole_ruling(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("the sent list's pictures couldn't be read")

    # A reason nobody expected, before any ask.
    monkeypatch.setattr(clip_judge, "sent_references", broken)

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "not_checked"
    assert "couldn't be read" in ruling(clip)["error"]
    # The same shape as any ruling, so whatever reads it finds every part.
    assert (ruling(clip)["failed_answers"], ruling(clip)["failures"]) == (0, [])
    assert ruling(clip)["checks"] == dict.fromkeys(
        ("product", "real_motion", "sudden_change", "does_what_was_ordered"), "not_checked"
    )
    assert ruling(clip)["wording"] == wording_version()
    assert not judge_calls()
    assert SceneStep.objects.get(kind="clip", scene__number=2).status == "finished"


def test_a_broll_clip_without_its_sent_list_is_never_kept_as_judged(judged: ProducedItem) -> None:
    # Every B-roll clip keeps its sent list before it is judged (spec #210 point 19): one
    # without, as a clip made before that, stops its step rather than being ruled on.
    ProducedItem.objects.filter(pk=judged.pk).update(sent=None, ruling=None)
    clip = ProducedItem.objects.get(pk=judged.pk)

    with pytest.raises(AssertionError):
        clip_judge.judge_clip(SceneStep.objects.get(kind="clip", scene__number=2), clip)

    assert ProducedItem.objects.get(pk=judged.pk).ruling is None
