"""A B-roll scene's clip is judged right after it is made, driven through the chat (#109): SIDES
looks at 5 stills for objects that can't exist, MOTION at 5 frames a second for movement no
real camera could film, each asked 3 times and failing on 2. The judge's ruling is kept on the
clip, every answer with it, for the auto-redo to act on; its cost is the clip step's. Talking
clips aren't judged. The producer's model, the scene models and the judge are faked at the
gateway (the plan: Claude Doc "Clip judge discussion", Julian 2026-10-13)."""

from collections.abc import Callable
from typing import Any

import pytest

from agents import tasks
from agents.money import dollars
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs.clip_judge import ASKS, wording_version
from jobs.models import Job, ProducedItem, Scene, SceneStep

from .conftest import (
    CLIP_MOTION_OK,
    CLIP_SIDES_OK,
    HeldSteps,
    WorkerStopped,
    handoffs,
    paid_for,
    said_yes_to_a_fix,
)
from .test_producer_broll import (
    BROLL_CHOICE,
    DOES_A_JOB,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_clip_from_note import REWRITE

pytestmark = pytest.mark.django_db(transaction=True)

FLOATS = "The teapot rises at 1.2 s with no hand on it; a real one stays put."


def sideways() -> dict[str, Any]:
    """SIDES' answer when the mug shows a second handle."""
    return {**CLIP_SIDES_OK, "verdict": "fail", "problem": "A second handle at 2 s."}


def floating(question: str = "real_motion") -> dict[str, Any]:
    """MOTION's answer when the teapot floats."""
    return {
        **CLIP_MOTION_OK,
        question: {"verdict": "fail", "problem": FLOATS, "back_to_normal_at": "never"},
    }


def clip_of(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scene: int
) -> ProducedItem:
    """Make `scene`'s starting picture, audio, transcript and clip; gives the clip."""
    made_ready(fake_model, steps, say, (scene,))
    calling(fake_model, say, ("make_clip", {"scene": scene}))
    run(fake_model, steps)
    return ProducedItem.objects.get(kind="clip", scene__number=scene)


def labels(purpose: str) -> list[list[str]]:
    """The label of each picture each of the 3 asks for `purpose` was shown, by ask."""
    asked = [
        [image["label"] for image in call.images]
        for call in ModelCall.objects.filter(purpose=purpose)
    ]
    assert len(asked) == 3
    return asked


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


def test_a_broll_clip_is_judged_three_times_by_each_check(judged: ProducedItem) -> None:
    assert paid_for().count("check_clip_sides") == 3
    assert paid_for().count("check_clip_motion") == 3
    assert sorted(ask["ask"] for ask in handoffs("check_clip_sides")) == [1, 2, 3]
    assert sorted(ask["ask"] for ask in handoffs("check_clip_motion")) == [1, 2, 3]
    assert ruling(judged)["verdict"] == "pass"
    assert ruling(judged)["failed_answers"] == 0


def test_sides_sees_the_shop_photo_then_5_stills_from_the_first_frame_to_the_last(
    judged: ProducedItem,
) -> None:
    for shown in labels("check_clip_sides"):
        assert shown[0] == "The shop photo of the product:"
        assert shown[1] == "Frames from the clip, in order:\nFrame at 0 s"
        assert len(shown) == 6
        # The last still is from the clip's end: a 5-second clip's.
        assert shown[-1].startswith("Frame at 4.9")


def test_motion_sees_the_shop_photo_then_a_frame_every_fifth_of_a_second(
    judged: ProducedItem,
) -> None:
    for shown in labels("check_clip_motion"):
        assert shown[0] == "The shop photo of the product:"
        times = [label.removeprefix("Frames from the clip, in order:\n") for label in shown[1:]]
        assert times == [f"Frame at {n / 5:g} s" for n in range(25)]


def test_the_shop_photo_is_the_one_the_starting_picture_was_drawn_from(
    judged: ProducedItem,
) -> None:
    photo = Job.objects.get().photos.get(position=BROLL_CHOICE["photo"])
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.images[0]["key"] == photo.file


def test_motion_is_told_the_scenes_order_and_how_the_product_is_used(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    needs = [{"what": "the mug's handle", "photos": [1]}]
    Scene.objects.filter(number=2).update(**DOES_A_JOB | {"needs": needs})

    clip_of(fake_model, steps, say, 2)

    for asked in handoffs("check_clip_sides") + handoffs("check_clip_motion"):
        assert "Parts of the product this scene needs to show: the mug's handle" in asked["facts"]
    for asked in handoffs("check_clip_motion"):
        assert asked["order"] == SHOWS
        assert f"How the product is used in this scene: {DOES_A_JOB['usage']}" in asked["facts"]
        assert f"What that use changes: {DOES_A_JOB['result']}" in asked["facts"]
        assert "<shop text>" in asked["facts"]
    # SIDES isn't told what should happen: only what the product is.
    for asked in handoffs("check_clip_sides"):
        assert "order" not in asked
        assert "How the product is used" not in asked["facts"]
        assert "<shop text>" in asked["facts"]


def test_a_clip_the_judge_fails_keeps_each_failed_answers_problem_and_what_it_saw(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    fake_model.respond("check_clip_motion", floating(), CLIP_MOTION_OK, floating())

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "fail"
    assert ruling(clip)["failed_answers"] == 2
    assert [(failed["check"], failed["problems"]) for failed in ruling(clip)["failures"]] == [
        ("motion", [FLOATS]),
        ("motion", [FLOATS]),
    ]
    assert all(failed["answer"] == floating() for failed in ruling(clip)["failures"])
    # Every answer is kept, the passing ones too (Julian: keep every judge answer).
    motion = ruling(clip)["answers"]["motion"]
    assert sorted(motion, key=lambda answer: answer["real_motion"]["verdict"]) == [
        floating(),
        floating(),
        CLIP_MOTION_OK,
    ]
    assert ruling(clip)["answers"]["sides"] == [CLIP_SIDES_OK] * 3
    # The clip is kept and the scene finished: what to do about it is the auto-redo's.
    assert Scene.objects.get(number=2).status == "finished"


def test_the_judges_cost_is_the_clip_steps(judged: ProducedItem) -> None:
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    for check in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert check.tool_call_id == step.tool_call_id
    assert f"It cost {dollars(step.tool_call.cost_usd())}." in step.result


def test_a_talking_clip_isnt_judged(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    clip = clip_of(fake_model, steps, say, 1)

    assert not ModelCall.objects.filter(purpose__startswith="check_clip_").exists()
    assert clip.ruling is None


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


def test_an_ask_with_no_usable_answer_goes_unchecked(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    unusable = UnusableReply("no answer", input_tokens=1, output_tokens=1)
    fake_model.respond("check_clip_sides", *[unusable] * 6, CLIP_SIDES_OK)

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["checks"]["sides"] == "not_checked"
    assert ruling(clip)["verdict"] == "not_checked"
    assert SceneStep.objects.get(kind="clip", scene__number=2).status == "finished"


def test_a_long_clips_frames_are_shown_two_to_a_picture_to_fit_one_ask(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    # A 10-second clip has 50 frames at 5 a second: with the shop photo, more than the 50
    # pictures a model takes in one ask.
    fake_model.words_per_second = 0.8

    clip_of(fake_model, steps, say, 2)

    for shown in labels("check_clip_motion"):
        assert len(shown) == 26
        assert shown[1].endswith("Frames at 0 s, 0.2 s, left to right")
        assert shown[-1] == "Frames at 9.6 s, 9.8 s, left to right"


def test_a_clip_judged_again_after_the_worker_stopped_pays_for_no_answer_twice(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    fake_model.respond("check_clip_motion", CLIP_MOTION_OK, CLIP_MOTION_OK, WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()

    # The step is handed out again, as after a restart.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(SceneStep.objects.get(kind="clip", scene__number=2).pk)

    assert paid_for().count("make_broll_clip") == 1
    assert paid_for().count("check_clip_sides") == 3
    assert paid_for().count("check_clip_motion") == 3
    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert ruling(clip)["verdict"] == "pass"


def test_an_ask_failing_for_a_reason_nobody_expected_goes_unchecked_and_the_rest_still_count(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_sides", *[sideways()] * 3)
    fake_model.respond("check_clip_motion", RuntimeError("the judge broke"))

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "fail"
    assert ruling(clip)["checks"]["sides"] == "fail"
    assert None in ruling(clip)["answers"]["motion"]


def test_a_clip_that_cant_be_judged_at_all_is_kept_as_not_checked_with_a_whole_ruling(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    made_ready(fake_model, steps, say, (2,))
    # A clip step whose starting picture lost the photo it was drawn from: nothing to judge by.
    SceneStep.objects.filter(kind="starting_picture", scene__number=2).update(photo=None)
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)

    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert ruling(clip)["verdict"] == "not_checked"
    assert "photo" in ruling(clip)["error"]
    # The same shape as any ruling, so whatever reads it finds every part.
    assert ruling(clip)["failed_answers"] == 0
    assert ruling(clip)["failures"] == []
    assert ruling(clip)["checks"] == dict.fromkeys(
        ("sides", "real_motion", "sudden_change", "does_what_was_ordered"), "not_checked"
    )
    assert SceneStep.objects.get(kind="clip", scene__number=2).status == "finished"


def test_a_9_second_clips_frames_are_each_shown_on_their_own(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # 45 frames and the shop photo fit in one ask, as in the tested setup.
    fake_model.words_per_second = 0.95

    clip_of(fake_model, steps, say, 2)

    for shown in labels("check_clip_motion"):
        assert len(shown) == 46
        assert shown[-1] == "Frame at 8.8 s"


def test_both_checks_are_told_the_products_colour_and_size_and_the_conversation(
    judged: ProducedItem,
) -> None:
    for asked in handoffs("check_clip_sides") + handoffs("check_clip_motion"):
        assert "Colour: sage green" in asked["facts"]
        assert "Size: handheld" in asked["facts"]
        # Only for what the product is and looks like; the owner beats the page (plan §3).
        assert "the owner is right" in asked["about_the_conversation"]
        said = [message["text"] for message in asked["conversation"]]
        assert "Make the person and check the script" in said
        # Not a fix's clip: nothing the agent restated.
        assert asked["what_the_agent_understood_from_the_owner"] is None


def test_both_checks_see_a_photo_of_what_the_scene_needs_labelled_with_what_it_shows(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    needs = [{"what": "the mug's handle", "photos": [2]}]
    Scene.objects.filter(number=2).update(**DOES_A_JOB | {"needs": needs})

    clip_of(fake_model, steps, say, 2)

    handle = Job.objects.get().photos.get(position=2)
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.images[1] == {"label": "A shop photo of the mug's handle:", "key": handle.file}
        assert call.images[2]["label"].startswith("Frames from the clip, in order:")


def test_a_fixs_clip_is_judged_with_what_the_agent_understood_from_the_owner(
    fake_model: FakeModel, judged: ProducedItem, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fix = said_yes_to_a_fix(2, "The tea pours into the mug, not beside it.", "clip")
    calling(fake_model, say, ("make_clip", {"scene": 2, "note": fix.fix}))
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    run(fake_model, steps)

    new = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert ruling(new)["verdict"] == "pass"
    for asked in handoffs("check_clip_motion")[ASKS:] + handoffs("check_clip_sides")[ASKS:]:
        assert asked["what_the_agent_understood_from_the_owner"] == fix.fix


def test_the_ruling_says_which_words_and_models_judged_it(judged: ProducedItem) -> None:
    assert ruling(judged)["wording"] == wording_version()
    assert ruling(judged)["models"] == {
        "sides": MODEL_FOR_PURPOSE["check_clip_sides"],
        "motion": MODEL_FOR_PURPOSE["check_clip_motion"],
    }
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.model == MODEL_FOR_PURPOSE[call.purpose]
