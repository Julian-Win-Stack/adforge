"""A B-roll scene's clip is judged right after it is made, driven through the chat (#109): SIDES
looks at 10 frames a second, joined two to a picture, for objects that can't exist and a turn
that shows a side that isn't real; MOTION at 5 frames a second for movement no real camera
could film; each asked 3 times and failing on 2. The judge's ruling is kept on the clip, every
answer with it, for the auto-redo to act on; its cost is the clip step's. Talking clips aren't
judged. The producer's model, the scene models and the judge are faked at the gateway (the
plan: Claude Doc "Clip judge discussion", Julian 2026-10-13; the phone-flip SIDES check,
Julian 2026-10-14)."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from agents import tasks
from agents.money import dollars
from chat import messages
from chat.models import Message, Session
from gateway.catalog import MODEL_FOR_PURPOSE
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import UnusableReply
from jobs import clip_judge
from jobs.clip_judge import ASKS, NEEDS_PHOTOS_SHOWN, PAGE_FRAME, wording_version
from jobs.models import Job, ProducedItem, ProductPhoto, Scene, SceneStep
from jobs.notices import post_notice

from .conftest import (
    CLIP_FIXED,
    CLIP_MOTION_OK,
    CLIP_SIDES_OK,
    HeldSteps,
    WorkerStopped,
    asked_with,
    handoffs,
    paid_for,
    photo,
)
from .fixing import fixed, plan_row, planned, say_yes
from .test_fix_loop import TURN_REWRITE
from .test_producer_broll import (
    BROLL_CHOICE,
    DOES_A_JOB,
    LINE_2,
    MOTION,
    SHOWS,
    calling,
    checked,  # noqa: F401 (a fixture)
    made_ready,
    run,
)
from .test_producer_clip_from_note import REWRITE

pytestmark = pytest.mark.django_db(transaction=True)

FLOATS = "The teapot rises at 1.2 s with no hand on it; a real one stays put."
# A clip fix, as the plan the shop owner said yes to words it.
POURS = "The tea pours into the mug, not beside it."


def a_clip_fix(fake_model: FakeModel) -> SceneStep:
    """The shop owner said yes to a plan making scene 2's clip again so the tea pours into
    the mug: its clip step is queued, the rewrite answering. Gives the step."""
    fixed(2, "new_clip", change=POURS)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    return SceneStep.objects.filter(kind="clip", scene__number=2).latest("id")


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


def test_sides_sees_the_shop_photo_then_ten_frames_a_second_two_to_a_picture(
    judged: ProducedItem,
) -> None:
    # A 5-second clip has 50 frames at 10 a second: with the shop photo, more than the 50
    # pictures a model takes in one ask, so they are joined in pairs (the F1 harness run).
    for shown in labels("check_clip_sides"):
        assert shown[0] == "The shop photo of the product:"
        assert shown[1] == "Frames from the clip, in order:\nFrames at 0 s, 0.1 s, left to right"
        assert len(shown) == 26
        assert shown[-1] == "Frames at 4.8 s, 4.9 s, left to right"


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
    # The auto-fix (#155) makes it again, and the judge passes the remake.
    fake_model.respond(
        "remake_clip_prompt", {"motion_prompt": "Tea pours.", "motion_prompt_reason": "Pour."}
    )
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)
    clip = ProducedItem.objects.get(kind="clip", scene__number=2, version=1)

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
    assert Scene.objects.get(number=2).status == "finished"


# The teapot's lid, a fault of its own at another time.
LID = "The teapot's lid melts into its body at 3.2 s; a real lid stays a separate piece."


def test_a_clip_two_answers_fail_on_different_faults_passes_and_is_kept(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    lid = {
        **CLIP_MOTION_OK,
        "real_motion": {"verdict": "fail", "problem": LID, "back_to_normal_at": "never"},
    }
    fake_model.respond("check_clip_motion", floating(), CLIP_MOTION_OK, lid)
    # Each failing answer names a fault of its own, whichever asks they came from.
    fake_model.answer_unscripted(
        "agree_on_fault",
        lambda request: {
            "read": [],
            "groups": [
                {"asks": [each["ask"]], "fault": "Its own."} for each in request.handoff.answers
            ],
        },
    )

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "pass"
    assert ruling(clip)["agreement"]["real_motion"]["sharing"] == []
    # Kept: no remake.
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 1
    asked = handoffs("agree_on_fault")
    assert sorted(answer["problem"] for answer in asked[0]["answers"]) == [FLOATS, LID]
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert ModelCall.objects.get(purpose="agree_on_fault").tool_call_id == step.tool_call_id


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


def test_a_first_clip_the_judge_couldnt_check_is_kept_and_the_producer_told_so(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
) -> None:
    """Too few answers to settle a check is no fail, so the clip is kept as it is (Julian,
    2026-10-14: "treat that as pass"), and the shop owner is told it wasn't checked."""
    unusable = UnusableReply("no answer", input_tokens=1, output_tokens=1)
    fake_model.respond("check_clip_sides", *[unusable] * 6, CLIP_SIDES_OK)

    clip = clip_of(fake_model, steps, say, 2)

    assert ruling(clip)["verdict"] == "not_checked"
    assert paid_for().count("make_broll_clip") == 1
    step = SceneStep.objects.get(kind="clip", scene__number=2)
    assert step.status == "finished"
    assert step.result.endswith(
        " The clip judge couldn't check this clip, so it is kept unchecked. Scene 2 is "
        "finished. Tell the shop owner."
    )


@pytest.mark.parametrize(
    (
        "words_per_second",
        "pictures",
        "first",
        "last",
        "sides_pictures",
        "sides_first",
        "sides_last",
    ),
    [
        # A 10-second clip has 50 frames at 5 a second: with the shop photo, more than the 50
        # pictures a model takes in one ask, so they are shown two to a picture. SIDES' 100
        # frames at 10 a second go three to a picture.
        (
            0.8,
            26,
            "Frames at 0 s, 0.2 s, left to right",
            "Frames at 9.6 s, 9.8 s, left to right",
            35,
            "Frames at 0 s, 0.1 s, 0.2 s, left to right",
            "Frame at 9.9 s",
        ),
        # 45 frames and the shop photo fit in one ask, as in the tested setup: each on its own.
        # SIDES' 90 don't, so they go two to a picture.
        (
            0.95,
            46,
            "Frame at 0 s",
            "Frame at 8.8 s",
            46,
            "Frames at 0 s, 0.1 s, left to right",
            "Frames at 8.8 s, 8.9 s, left to right",
        ),
    ],
    ids=["a 10-second clip, two to a picture", "a 9-second clip, one each"],
)
def test_a_clips_frames_are_shown_two_to_a_picture_only_when_they_dont_fit_one_ask(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],  # noqa: F811
    words_per_second: float,
    pictures: int,
    first: str,
    last: str,
    sides_pictures: int,
    sides_first: str,
    sides_last: str,
) -> None:
    fake_model.words_per_second = words_per_second

    clip_of(fake_model, steps, say, 2)

    for shown in labels("check_clip_motion"):
        assert len(shown) == pictures
        assert shown[1].endswith(first)
        assert shown[-1] == last
    for shown in labels("check_clip_sides"):
        assert len(shown) == sides_pictures
        assert shown[1].endswith(sides_first)
        assert shown[-1] == sides_last


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


def test_a_clip_whose_pictures_were_cut_for_an_earlier_judge_is_cut_again(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # A clip mid-judging when SIDES went to 10 frames a second kept pictures cut for the old
    # judge, 5 stills: nothing in them for SIDES to look at, so the clip is cut again.
    made_ready(fake_model, steps, say, (2,))
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    fake_model.respond("check_clip_sides", *[WorkerStopped()] * ASKS)
    fake_model.respond("check_clip_motion", *[WorkerStopped()] * ASKS)
    with pytest.raises(WorkerStopped):
        steps.run_next()
    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    frames = ruling(clip)["pictures"]["frames"]
    clip.ruling = {"pictures": {"stills": frames[::5], "frames": frames}}
    clip.save(update_fields=["ruling"])

    # The step is handed out again, as after a restart.
    fake_model.respond("produce", turn(says="Done."))
    tasks.run_scene_step(SceneStep.objects.get(kind="clip", scene__number=2).pk)

    clip.refresh_from_db()
    assert ruling(clip)["verdict"] == "pass"
    assert ruling(clip)["pictures"].keys() == {"sides", "frames"}
    for shown in labels("check_clip_sides"):
        assert shown[1] == "Frames from the clip, in order:\nFrames at 0 s, 0.1 s, left to right"
        assert len(shown) == 26


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
    fake_model: FakeModel, judged: ProducedItem, steps: HeldSteps
) -> None:
    a_clip_fix(fake_model)
    steps.run_held()

    new = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert ruling(new)["verdict"] == "pass"
    for asked in handoffs("check_clip_motion")[ASKS:] + handoffs("check_clip_sides")[ASKS:]:
        assert asked["what_the_agent_understood_from_the_owner"] == POURS
    # Beside the old clip: the ruling says which, and that the fix's words judged it.
    assert ruling(new)["compared_with"] == judged.pk
    assert ruling(new)["wording"] == wording_version(fix=True) != wording_version()
    assert "compared_with" not in ruling(judged)


def test_the_ruling_says_which_words_and_models_judged_it(judged: ProducedItem) -> None:
    assert ruling(judged)["wording"] == wording_version()
    assert ruling(judged)["models"] == {
        "sides": MODEL_FOR_PURPOSE["check_clip_sides"],
        "motion": MODEL_FOR_PURPOSE["check_clip_motion"],
        "agree": MODEL_FOR_PURPOSE["agree_on_fault"],
    }
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.model == MODEL_FOR_PURPOSE[call.purpose]


def test_a_fixs_clip_is_judged_with_the_owners_note_and_their_yes_but_no_notice_or_later_word(
    fake_model: FakeModel,
    judged: ProducedItem,
    steps: HeldSteps,
    session_id: str,
) -> None:
    # The owner's note, planned and shown by code, and their typed yes: the fix's clip is
    # started. A plan stored before the fixing agent: a new clip of a B-roll scene.
    note = "Scene 2: the hand just hovers, it should turn the mug all the way round."
    change = "The hand turns the mug all the way round on the workbench."
    say_yes(planned(plan_row(2, "new_clip", change=change), note=note), "Yes, do that.")
    assert [SceneStep.objects.get(pk=pk).kind for pk in steps.held] == ["clip"]
    # While it is being made, code posts a notice and the owner writes again.
    notice = "Scene 2's clip is taking longer than usual."
    post_notice(Job.objects.get(), notice, Message.Level.INFO)
    later = "Also, could the tea be green?"
    messages.add(Session.objects.get(pk=session_id), role=Message.Role.USER, text=later)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    steps.run_held()

    for asked in handoffs("check_clip_sides")[ASKS:] + handoffs("check_clip_motion")[ASKS:]:
        said = [(message["by"], message["text"]) for message in asked["conversation"]]
        assert ("user", note) in said
        assert said[-1] == ("user", "Yes, do that.")
        # The plan shown to the owner is code's notice, as the one posted later.
        assert change not in str(said)
        assert notice not in str(said)
        assert later not in str(said)
        assert asked["what_the_agent_understood_from_the_owner"] == change


def test_at_most_5_needs_photos_are_shown_one_per_photo_and_the_frames_fit_beside_them(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Six more photos, 3 to 8, none with a face; photo 3 shows two things the scene needs.
    job = Job.objects.get()
    for number in range(3, 9):
        ProductPhoto.objects.create(
            job=job,
            position=number,
            file=file_store.save(f"photo-{number}.png", photo(number)),
        )
    needs = [
        {"what": "the mug's handle", "photos": [3]},
        {"what": "the mug's rim", "photos": [3]},
        *({"what": f"part {number}", "photos": [number]} for number in range(4, 9)),
    ]
    Scene.objects.filter(number=2).update(**DOES_A_JOB | {"needs": needs})
    # A 9-second clip: 45 frames at 5 a second, which fit one to a picture beside the shop
    # photo alone, but not beside 5 needs photos too (50 pictures an ask).
    fake_model.words_per_second = 0.95

    clip_of(fake_model, steps, say, 2)

    assert NEEDS_PHOTOS_SHOWN == 5
    handle = job.photos.get(position=3)
    for call in ModelCall.objects.filter(purpose__startswith="check_clip_"):
        assert call.images[1] == {
            "label": "A shop photo of the mug's handle and the mug's rim:",
            "key": handle.file,
        }
        assert [image["label"] for image in call.images[2:6]] == [
            f"A shop photo of part {number}:" for number in range(4, 8)
        ]
        assert "part 8" not in json.dumps(call.images)
        assert call.images[6]["label"].startswith("Frames from the clip, in order:")
    # SIDES' 90 frames at 10 a second go three to a picture beside the 6 photos.
    for shown in labels("check_clip_sides"):
        assert len(shown) == 6 + 30
    for shown in labels("check_clip_motion"):
        assert len(shown) == 6 + 23
        assert shown[6].endswith("Frames at 0 s, 0.2 s, left to right")
        assert shown[-1] == "Frame at 8.8 s"


def test_neither_check_is_handed_the_clips_prompt_or_line_and_blank_colour_and_size_are_left_out(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    Job.objects.update(product_colour="", product_size="")

    clip_of(fake_model, steps, say, 2)

    for asked in handoffs("check_clip_sides") + handoffs("check_clip_motion"):
        handed = json.dumps(asked)
        assert MOTION not in handed
        assert LINE_2 not in handed
        assert asked["facts"].startswith("Product: Stoneware Mug\n")
        assert "Colour:" not in asked["facts"]
        assert "Size:" not in asked["facts"]
        assert PAGE_FRAME in asked["facts"]


def test_a_fixs_clip_and_the_old_one_each_get_half_the_room_in_one_ask(
    fake_model: FakeModel, judged: ProducedItem, steps: HeldSteps
) -> None:
    a_clip_fix(fake_model)
    steps.run_held()

    # Each 5-second clip is 25 frames: alone, each is shown on its own; beside the other clip,
    # each gets 24 pictures' room of the 49 left by the shop photo, so its frames are joined
    # two to a picture.
    for shown in [
        [image["label"] for image in call.images]
        for call in ModelCall.objects.filter(purpose="check_clip_motion").order_by("id")[ASKS:]
    ]:
        old = next(n for n, label in enumerate(shown) if "old clip" in label)
        new = next(n for n, label in enumerate(shown) if "new clip" in label)
        assert (old, new - old, len(shown) - new) == (1, 13, 13)
        assert shown[-1] == "Frame at 4.8 s"
    # The old clip's frames, kept on it, then the new one's.
    remade = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    complained = ProducedItem.objects.get(pk=judged.pk)
    old_keys = [p["key"] for p in ruling(complained)["pictures_beside"]["frames"]]
    new_keys = [p["key"] for p in ruling(remade)["pictures"]["frames"]]
    for call in ModelCall.objects.filter(purpose="check_clip_motion").order_by("id")[ASKS:]:
        assert [image["key"] for image in call.images[1:]] == old_keys + new_keys
    # SIDES is shown the new clip as for any clip.
    for shown in [
        [image["label"] for image in call.images]
        for call in ModelCall.objects.filter(purpose="check_clip_sides").order_by("id")[ASKS:]
    ]:
        assert not any("old clip" in label for label in shown)


def a_fixs_clip_judged_until_the_worker_stops(fake_model: FakeModel, steps: HeldSteps) -> SceneStep:
    """Scene 2's clip made again for a clip fix, the worker stopping before the judge's last
    MOTION answer; gives the step, as handed out again."""
    step = a_clip_fix(fake_model)
    answered = {**CLIP_MOTION_OK, "owner_change": CLIP_FIXED}
    fake_model.respond("check_clip_motion", answered, answered, WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    return step


def test_a_fixs_clip_judged_again_after_the_worker_stopped_shows_the_old_clip_cut_once(
    fake_model: FakeModel,
    judged: ProducedItem,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutting = clip_judge.cut_pictures
    cut: list[str] = []

    def counted(clip: str, **kwargs: Any) -> Any:
        cut.append(clip)
        return cutting(clip, **kwargs)

    monkeypatch.setattr(clip_judge, "cut_pictures", counted)
    step = a_fixs_clip_judged_until_the_worker_stops(fake_model, steps)
    requests = asked_with(fake_model, "check_clip_motion")

    tasks.run_scene_step(step.pk)

    assert cut.count(judged.file) == 1
    # Only the ask the worker stopped in is asked again, shown the old clip as before.
    (again,) = requests
    first = ModelCall.objects.filter(purpose="check_clip_motion").order_by("id")[ASKS]
    assert [image.key for image in again.images] == [image["key"] for image in first.images]
    assert paid_for().count("check_clip_motion") == 2 * ASKS
    new = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert ruling(new)["compared_with"] == judged.pk


def test_a_fixs_clip_whose_old_frames_couldnt_be_cut_stays_judged_alone_when_asked_again(
    fake_model: FakeModel,
    judged: ProducedItem,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutting = clip_judge.cut_pictures
    failed: list[str] = []

    def fails_once_on_the_old_clip(clip: str, **kwargs: Any) -> Any:
        if clip == judged.file and not failed:
            failed.append(clip)
            raise RuntimeError("ffmpeg couldn't read the clip")
        return cutting(clip, **kwargs)

    monkeypatch.setattr(clip_judge, "cut_pictures", fails_once_on_the_old_clip)
    step = a_clip_fix(fake_model)
    fake_model.respond("check_clip_motion", CLIP_MOTION_OK, CLIP_MOTION_OK, WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    requests = asked_with(fake_model, "check_clip_motion")

    tasks.run_scene_step(step.pk)

    # Its frames were cut to fit alone: the old clip's can't join them now.
    (again,) = requests
    assert again.instructions == clip_judge.MOTION_CHECK
    assert not any("old clip" in image.label for image in again.images)
    new = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert "compared_with" not in ruling(new)
