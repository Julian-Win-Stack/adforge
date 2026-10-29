"""Fixing a scene once it has a clip, from the worker's side (route B step 1, #135; fix
redesign, ticket 08): a fix is a plan the decider made, said yes to by the shop owner and run
by code (tests/fixing.py). What a fix is estimated to cost, what it cost, and the new ad code
assembles once its last scene lands, also when the fix is said yes to before the whole first
ad is made. The producer only makes the ad's first version and answers what isn't a note. The
producer's model and the scene models are faked at the gateway; the clips are real tiny
videos."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from pytest_django import Settings

from agents.models import SystemNote
from agents.producer import (
    CALL_FALLBACK_USD,
    PICTURE_FALLBACK_USD,
    _estimate,
    _picture_only_estimate_usd,
    picture_estimate,
)
from chat.models import Attachment
from gateway.catalog import MODEL_FOR_PURPOSE, PRICE_PER_SECOND_OF_VIDEO
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from jobs import fix_check, fix_plans, picture_check, work
from jobs.clip_judge import ASKS
from jobs.models import Fix, FixPlan, FixPlanScene, ProducedItem, Scene, SceneStep

from . import fixing
from .conftest import HeldSteps, WorkerStopped, paid_for, results_of
from .fixing import fixed, plan_row, planned, say_yes
from .test_fix_wiring import decided, planning, row
from .test_producer_broll import (
    TALKING_CHOICE,
    assemble_through_the_chat,
    calling,
    checked,  # noqa: F401 (a fixture)
    clip_of_scene_2,
    made_ready,
    run,
)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_2 = "Scene 2 just sits there. The tea should actually pour into the mug."
FIX_2 = "In scene 2, the tea pours from the teapot into the mug and steam rises from it."
NOTE_1 = "In the first scene she should hold the mug with both hands."
FIX_1 = "In scene 1, she holds the mug up beside her face with both hands."
REWRITE: dict[str, Any] = {
    "motion_prompt": "Tea pours from the teapot into the mug and steam rises from it.",
    "motion_prompt_reason": "The note asks for the pour.",
}
BOREAL = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_broll_clip"]]
HEYGEN = PRICE_PER_SECOND_OF_VIDEO[MODEL_FOR_PURPOSE["make_talking_clip"]]

Say = Callable[..., None]


def judge_ask_usd(scene: int) -> Decimal:
    """What one of the clip judge's asks cost on average for the scene's last clip."""
    calls = ModelCall.objects.filter(
        tool_call=last_step("clip", scene).tool_call,
        purpose__in=["check_clip_sides", "check_clip_motion"],
    )
    costs = [call.cost_usd for call in calls if call.cost_usd]
    assert len(costs) == 2 * ASKS, "the clip judge asked each check, each paid for"
    return sum(costs, Decimal(0)) / len(costs)


def broll_clip_usd(seconds: int, *, from_note: bool) -> tuple[Decimal, Decimal]:
    """Scene 2's new B-roll clip, about and up to: its video and the judge's asks, the
    writer's call for a clip made from a note (not recorded yet, so at the fallback; its fix
    is checked by the judge's own asks), and up to 6 tries of the fixing agent, each a new
    picture, a video, the judge and the agent's 3 turns (not recorded yet, so each at the
    fallback)."""
    video = seconds * BOREAL
    judge = 2 * ASKS * judge_ask_usd(2)
    about = video + judge + (CALL_FALLBACK_USD if from_note else 0)
    picture = _picture_only_estimate_usd(Scene.objects.get(number=2))
    return about, about + 6 * (picture + video + judge + 3 * CALL_FALLBACK_USD)


def owner_says(fake_model: FakeModel, say: Say, text: str, *calls: tuple[str, Any]) -> None:
    """The shop owner sends `text`; the producer makes `calls` in one turn, if any, then
    replies."""
    if calls:
        fake_model.respond("produce", turn(calls=list(calls)))
    fake_model.respond("produce", turn(says="On it."))
    say(text)


def picture_with(scene: int, note: str | None) -> tuple[str, dict[str, Any]]:
    return ("make_starting_picture", {"scene": scene, "note": note})


def ads() -> list[int]:
    """Each finished ad's version, oldest first."""
    return list(
        ProducedItem.objects.filter(kind="finished_ad")
        .order_by("version")
        .values_list("version", flat=True)
    )


def the_ad(version: int) -> ProducedItem:
    return ProducedItem.objects.get(kind="finished_ad", version=version)


def times_shown(ad: ProducedItem) -> int:
    return Attachment.objects.filter(file=ad.file).count()


def last_step(kind: str = "clip", scene: int = 2) -> SceneStep:
    step = SceneStep.objects.filter(kind=kind, scene__number=scene).last()
    assert step is not None
    return step


def clip_of(step: SceneStep) -> ProducedItem:
    return ProducedItem.objects.get(kind="clip", step=step)


def row_of(plan: FixPlan, number: int) -> FixPlanScene:
    return plan.scenes.select_related("fix").get(scene__number=number)


def fix_of(plan: FixPlan, number: int) -> Fix:
    fix = row_of(plan, number).fix
    assert fix is not None, "a row said yes to has its Fix"
    return fix


def notes(kind: str) -> list[str]:
    return list(
        SystemNote.objects.filter(kind=kind)
        .order_by("created_at", "id")
        .values_list("text", flat=True)
    )


def ad_shown(ad: ProducedItem) -> str:
    return (
        f"Every scene of the plan is done, so the new ad (version {ad.version}) was assembled "
        "and shown to the shop owner."
    )


@pytest.fixture
def shown(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> ProducedItem:
    """A chat whose finished ad (version 1) is shown to the shop owner: scenes 1 and 3 have
    the person talking, scene 2 shows the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


def clip_fix_of_2(fake_model: FakeModel, steps: HeldSteps, change: str = FIX_2) -> FixPlan:
    """Scene 2's clip made again for `change` by a plan said yes to, and landed."""
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    plan = fixed(2, "new_clip", note=NOTE_2, change=change)
    steps.run_held()
    return plan


# --- Estimates --------------------------------------------------------------------------------


def test_a_picture_fix_is_estimated_at_the_last_pictures_cost_and_a_clip(
    shown: ProducedItem,
) -> None:
    scene = Scene.objects.get(number=1)
    picture = last_step("starting_picture", 1)
    paid = sum(
        (
            call.cost_usd or Decimal(0)
            for call in picture.tool_call.model_calls.all()
            if call.purpose not in ("plan_before_picture", "make_before_picture")
        ),
        Decimal(0),
    )
    assert paid > 0

    # The fake voice says scene 1's 8 words in 4 seconds.
    # Scene 1's talking clip isn't judged, so it can't be made again; its picture can be
    # drawn again, and checked again, for each fix check that fails it. No fix check was
    # paid for yet, so each of its asks is estimated at the fallback.
    redraws = fix_check.MOST_REDRAWS * (paid + picture_check.ASKS * CALL_FALLBACK_USD)
    assert picture_estimate(scene) == (paid + 4 * HEYGEN, paid + redraws + 4 * HEYGEN)


def test_a_broll_picture_fix_leaves_out_the_shared_before_picture(shown: ProducedItem) -> None:
    picture = last_step("starting_picture", 2)
    drew_it = picture.tool_call.model_calls.filter(purpose="make_before_picture")
    assert drew_it.exists(), "scene 2's picture drew the ad's before-picture"
    kept = picture.tool_call.model_calls.exclude(
        purpose__in=["plan_before_picture", "make_before_picture"]
    )

    cost = sum((call.cost_usd or Decimal(0) for call in kept), Decimal(0))
    about, up_to = broll_clip_usd(5, from_note=False)
    redraws = fix_check.MOST_REDRAWS * (cost + picture_check.ASKS * CALL_FALLBACK_USD)
    assert picture_estimate(Scene.objects.get(number=2)) == (cost + about, cost + redraws + up_to)


def test_a_picture_with_no_recorded_cost_is_estimated_at_the_fallback(
    shown: ProducedItem,
) -> None:
    ModelCall.objects.filter(tool_call=last_step("starting_picture", 1).tool_call).update(
        cost_usd=0
    )

    assert picture_estimate(Scene.objects.get(number=1)).about == (
        PICTURE_FALLBACK_USD + 4 * HEYGEN
    )


def test_with_quality_checks_off_a_picture_fix_is_estimated_with_no_redraw(
    shown: ProducedItem, settings: Settings
) -> None:
    settings.QUALITY_CHECKS = False

    # Nothing checks the fix, so nothing draws its picture again.
    about, up_to = picture_estimate(Scene.objects.get(number=1))
    assert about == up_to


def test_a_clip_fix_is_estimated_at_its_video_the_judge_and_the_writer_at_the_fallback(
    shown: ProducedItem,
) -> None:
    # No clip was made from a note yet, so the writer's call is at the fallback; only scene
    # 2's B-roll clip can be filmed again by the fixing agent.
    assert _estimate(Scene.objects.get(number=2), Fix.Redo.CLIP, FIX_2) == (
        broll_clip_usd(5, from_note=True)
    )


def test_a_clip_fix_is_estimated_from_the_ads_last_recorded_writer_and_judge(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    clip_fix_of_2(fake_model, steps)

    (writer,) = [
        call.cost_usd or 0 for call in ModelCall.objects.filter(purpose="rewrite_clip_prompt")
    ]
    assert writer > 0
    # Its fix is checked by the clip judge's own asks: no check of its own is priced.
    assert _estimate(Scene.objects.get(number=2), Fix.Redo.CLIP, FIX_2).about == (
        5 * BOREAL + 2 * ASKS * judge_ask_usd(2) + writer
    )


def test_with_quality_checks_off_a_clip_fix_is_estimated_without_the_judge_or_the_fix_check(
    shown: ProducedItem, settings: Settings
) -> None:
    settings.QUALITY_CHECKS = False

    # The clip's video and the writer's call, at the fallback, and no remake: nothing judges it.
    about = 5 * BOREAL + CALL_FALLBACK_USD
    assert _estimate(Scene.objects.get(number=2), Fix.Redo.CLIP, FIX_2) == (about, about)


def test_a_clip_fix_with_no_judge_ask_recorded_is_estimated_at_the_fallback_an_ask(
    shown: ProducedItem,
) -> None:
    ModelCall.objects.filter(purpose__in=["check_clip_sides", "check_clip_motion"]).delete()

    # The judge's 2 asks, which check the fix too, and the writer's call, each at the
    # fallback, and up to 6 tries of the fixing agent, each a new picture, a video, the
    # judge's 2 asks and the agent's 3 turns.
    video = 5 * BOREAL
    about = video + 3 * CALL_FALLBACK_USD
    picture = _picture_only_estimate_usd(Scene.objects.get(number=2))
    up_to = about + 6 * (picture + video + 5 * CALL_FALLBACK_USD)
    assert _estimate(Scene.objects.get(number=2), Fix.Redo.CLIP, FIX_2) == (about, up_to)


# --- What a fix cost --------------------------------------------------------------------------


def test_a_fixs_cost_is_its_own_steps_and_leaves_out_other_fixes_and_the_first_ad(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    first = fix_of(clip_fix_of_2(fake_model, steps), 2)
    assert first.cost_usd() == last_step().tool_call.cost_usd()
    assert first.cost_usd() > 5 * BOREAL  # the clip and its prompt's rewrite

    corrected = "In scene 2, the tea pours slowly from the left into the mug."
    second = fix_of(clip_fix_of_2(fake_model, steps, corrected), 2)

    first_step, second_step = SceneStep.objects.filter(kind="clip", fix__isnull=False).order_by(
        "id"
    )
    assert (first_step.fix, second_step.fix) == (first, second)
    assert first.cost_usd() == first_step.tool_call.cost_usd()
    assert second.cost_usd() == second_step.tool_call.cost_usd()


def test_a_failed_redos_cost_is_counted(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    plan = fixed(2, "new_clip", note=NOTE_2, change=FIX_2)
    fake_model.respond("rewrite_clip_prompt", {"motion_prompt": " ", "nonsense": 1})
    steps.run_held()

    assert last_step().status == "failed"
    assert fix_of(plan, 2).cost_usd() == last_step().tool_call.cost_usd() > 0


# --- The ad assembled by code -----------------------------------------------------------------


def test_a_clip_with_no_fix_names_no_fix_cost_and_assembles_nothing(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    assemble_through_the_chat(fake_model, steps, say)

    for step in SceneStep.objects.filter(kind="clip"):
        assert "fix" not in step.result
        assert step.result.endswith("Tell the shop owner.")
    # Only the producer's assemble_ad made the ad.
    assert ads() == [1]
    assert len(results_of("assemble_ad")) == 1


def test_a_fixs_clip_landing_assembles_the_new_ad_and_shows_it_once(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    old = {clip.scene.number: clip for clip in ProducedItem.objects.filter(kind="clip")}  # type: ignore[union-attr]

    clip_fix_of_2(fake_model, steps)

    assert ads() == [1, 2]
    ad = the_ad(2)
    new_2 = ProducedItem.objects.get(kind="clip", scene__number=2, version=2)
    assert [cut["clip"] for cut in ad.cuts] == [old[1].pk, new_2.pk, old[3].pk]
    assert times_shown(ad) == 1
    assert notes("ad_shown") == [ad_shown(ad)]


def test_two_scenes_fixed_together_assemble_once_when_the_last_lands(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    plan = say_yes(
        planned(
            plan_row(1, "new_picture", change=FIX_1),
            plan_row(2, "new_clip", change=FIX_2),
            note=f"{NOTE_1} {NOTE_2}",
        )
    )
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    # Scene 1's new picture lands, its clip queued by code; then scene 2's clip lands while
    # scene 1's is still to make: no half-fixed ad is assembled.
    steps.run_next()
    steps.run_next()
    assert row_of(plan, 2).landed_at is not None
    assert ads() == [1]

    steps.run_held()

    assert ads() == [1, 2]
    assert last_step("clip", 1).fix == fix_of(plan, 1)
    ad = the_ad(2)
    assert [cut["clip"] for cut in ad.cuts][:2] == [
        ProducedItem.objects.get(kind="clip", scene__number=1, version=2).pk,
        ProducedItem.objects.get(kind="clip", scene__number=2, version=2).pk,
    ]
    assert times_shown(ad) == 1


STEAM_NOTE = "Also, the steam should be thicker."
STEAM_FIX = f"{FIX_2} The steam is thick."


@pytest.mark.parametrize("answer", [None, "No, leave the steam."], ids=["unanswered", "no"])
def test_a_fixs_clip_landing_while_a_newer_plan_isnt_said_yes_to_assembles_its_ad(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, answer: str | None
) -> None:
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    first = fixed(2, "new_clip", note=NOTE_2, change=FIX_2)
    (first_step,) = steps.held
    # While its clip is being made, the shop owner writes about scene 2 again.
    newer = planned(plan_row(2, "new_clip", change=STEAM_FIX), note=STEAM_NOTE)
    if answer is not None:
        fix_plans.settle(newer, FixPlan.State.DECLINED, by=fixing.owner_says(answer))

    steps.run_held()

    # They haven't said yes to the newer plan, so the first one's ad is theirs to see.
    assert row_of(first, 2).landed_at is not None
    assert ads() == [1, 2]
    ad = the_ad(2)
    assert [cut["clip"] for cut in ad.cuts][1] == clip_of(SceneStep.objects.get(pk=first_step)).pk
    assert times_shown(ad) == 1
    assert not SceneStep.objects.filter(plan_scene__plan=newer).exists()


def test_a_go_back_landing_for_nothing_doesnt_hold_up_another_fixs_assembly(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    clip_fix_of_2(fake_model, steps)
    clip_fix_of_2(fake_model, steps, "In scene 2, the tea pours from the left.")
    # The shop owner goes back to the first fix's clip, brought back for nothing.
    back = fixed(2, "go_back", change="The first pour.", go_back_to=2)
    assert row_of(back, 2).landed_at is not None
    assert steps.held == []
    # Then a picture fix on scene 1 lands its clip.
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    plan = fixed(1, "new_picture", note=NOTE_1, change=FIX_1)

    steps.run_held()

    ad = ProducedItem.objects.filter(kind="finished_ad").order_by("version").last()
    assert ad is not None
    assert SystemNote.objects.filter(plan=plan, kind="ad_shown").get().text == ad_shown(ad)
    assert ad.cuts[0]["clip"] == clip_of(last_step("clip", 1)).pk
    first_fix = work.versions(Scene.objects.get(number=2))[1].clip
    assert first_fix is not None
    assert ProducedItem.objects.get(pk=ad.cuts[1]["clip"]).file == first_fix.file


def test_a_picture_fix_assembles_once_its_clip_lands(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    plan = fixed(1, "new_picture", note=NOTE_1, change=FIX_1)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    steps.run_next()
    assert ads() == [1]

    steps.run_held()

    assert ads() == [1, 2]
    fix = fix_of(plan, 1)
    assert last_step("clip", 1).fix == fix
    assert {step.kind for step in fix.steps.all()} == {"starting_picture", "clip"}
    assert fix.cost_usd() == sum(
        (step.tool_call.cost_usd() for step in fix.steps.all()), Decimal(0)
    )


def test_assemble_ad_after_the_automatic_one_is_already_assembled(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps, say: Say
) -> None:
    clip_fix_of_2(fake_model, steps)

    # Not a note: the decider passes it on to the producer.
    owner_says(fake_model, say, "Show me", ("assemble_ad", {}))

    assert ads() == [1, 2]
    assert results_of("assemble_ad")[-1].startswith(
        "The ad was already assembled from these clips, overlays and this music (version 2"
    )
    assert times_shown(the_ad(2)) == 1


def test_a_fix_clip_whose_ad_cant_be_assembled_tells_the_producer_why(
    fake_model: FakeModel, shown: ProducedItem, steps: HeldSteps
) -> None:
    ProducedItem.objects.filter(kind="transcript", scene__number=3).update(words=[])

    clip_fix_of_2(fake_model, steps)

    assert ads() == [1]
    assert last_step().status == "finished"
    assert notes("error") == [
        "The new ad couldn't be assembled: no words were heard in scene 3's audio, so there "
        "is nothing to cut its clip to. Make the line's audio again, then its clip."
    ]


def test_a_worker_stopping_before_the_assembly_assembles_once_when_run_again(
    fake_model: FakeModel,
    shown: ProducedItem,
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import tasks

    fake_model.respond("rewrite_clip_prompt", REWRITE)
    fixed(2, "new_clip", note=NOTE_2, change=FIX_2)
    (step_id,) = steps.held
    assemble = work.assemble_ad

    def the_worker_stops(*args: Any, **kwargs: Any) -> ProducedItem:
        assemble(*args, **kwargs)
        raise WorkerStopped

    with monkeypatch.context() as stopping:
        stopping.setattr("agents.producer.assemble_ad", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    assert SceneStep.objects.get(pk=step_id).status == "running"
    tasks.run_scene_step(step_id)

    assert ads() == [1, 2]
    assert times_shown(the_ad(2)) == 1
    assert paid_for().count("make_broll_clip") == 2
    assert len(notes("ad_shown")) == 1


# --- Fixing a scene before the whole ad is made ------------------------------------------------
# A scene can be fixed as soon as it has its clip, while the others are still being made
# (Julian 2026-10-12 00:26, #135 review question 5).


@pytest.fixture
def early(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Say,
) -> None:
    """A chat whose music is made and whose three scenes are ready, but only scene 2 has its
    clip: scenes 1 and 3 aren't finished, and there is no ad yet."""
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    clip_of_scene_2(fake_model, steps, say)


def test_a_scene_with_its_clip_is_fixed_while_the_others_are_still_being_made(
    fake_model: FakeModel, early: None, steps: HeldSteps
) -> None:
    plan = clip_fix_of_2(fake_model, steps)

    fix = fix_of(plan, 2)
    assert (fix.scene.number, fix.fix, fix.redo) == (2, FIX_2, "clip")
    assert row_of(plan, 2).landed_at is not None
    assert (last_step().fix, last_step().status) == (fix, "finished")
    assert ProducedItem.objects.filter(kind="clip", scene__number=2).count() == 2
    # Scenes 1 and 3 have no clip yet, so there is no ad to assemble.
    assert ads() == []
    assert notes("error") == [
        "The new ad couldn't be assembled: scenes 1 and 3 aren't finished: a scene is "
        "finished once its clip is made. Finish them, then assemble the ad."
    ]


def test_a_plan_cant_change_a_scene_with_no_clip_yet_while_another_has_its_clip(
    early: None,
) -> None:
    plan = planned(
        plan_row(2, "new_clip", change=FIX_2),
        plan_row(1, "new_picture", change=FIX_1),
        note=f"{NOTE_1} {NOTE_2}",
    )

    assert row_of(plan, 1).cant == "Scene 1 has no clip yet, so its note goes into making it."
    assert row_of(plan, 2).cant == ""


def test_a_plan_cant_change_a_scene_whose_first_clip_is_still_being_made(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    owner_says(fake_model, say, "Make scene 1 too.", ("make_clip", {"scene": 1}))
    assert len(steps.held) == 1

    plan = planned(plan_row(1, "new_picture", change=FIX_1), note=NOTE_1)

    assert row_of(plan, 1).cant == "Scene 1 has no clip yet, so its note goes into making it."


def test_before_the_ad_is_made_a_note_on_a_scene_with_no_clip_yet_is_the_producers_to_make(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    # The decider reads it as the note it is; a plan of only scenes with no clip yet isn't
    # shown, and the producer makes the scene with the note.
    fake_model.respond(
        "decide_fixes",
        planning(("look_at_references", {"scene": 1}), decided(row(1, "new_picture", NOTE_1))),
    )
    owner_says(fake_model, say, NOTE_1, picture_with(1, NOTE_1))

    [plan] = FixPlan.objects.all()
    assert plan.state == "replaced"
    assert not plan.messages.exists()
    assert not plan.system_notes.exists()
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)

    assert not Fix.objects.exists()
    step = last_step("starting_picture", 1)
    assert (step.status, step.note, step.fix) == ("finished", NOTE_1, None)


def test_a_note_on_a_scene_with_its_clip_and_one_without_is_planned_and_made_both_ways(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    fake_model.respond(
        "decide_fixes",
        planning(
            ("look_at_references", {"scene": 1}),
            ("look_at_references", {"scene": 2}),
            decided(row(2, "fix", FIX_2), row(1, "new_picture", NOTE_1)),
        ),
    )
    owner_says(fake_model, say, f"{NOTE_1} {NOTE_2}", picture_with(1, NOTE_1))

    # Scene 2's fix is shown as the plan; scene 1's note goes into making it.
    [plan] = FixPlan.objects.all()
    assert plan.state == "proposed" and plan.messages.filter(plan_event="shown").exists()
    assert row_of(plan, 1).cant == "Scene 1 has no clip yet, so its note goes into making it."
    assert last_step("starting_picture", 1).note == NOTE_1
    # The producer is told which scenes the plan covers, and which is its own to make.
    [told] = plan.system_notes.filter(kind="plan_shown").values_list("text", flat=True)
    assert "Scene 2: fixed by the fixing agent, to this goal" in told
    assert (
        "Scene 1 has no clip yet, so it isn't in the plan: the shop owner's note on it is "
        "yours to make it with."
    ) in told
    produced = [each for each in fake_model.turns_taken if each.purpose == "produce"]
    assert any(told in str(each.handoff.conversation) for each in produced[-2:])


def test_an_early_fix_landing_before_the_other_first_clips_leaves_the_first_ad_to_the_producer(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    fake_model.respond("rewrite_clip_prompt", REWRITE)
    plan = fixed(2, "new_clip", note=NOTE_2, change=FIX_2)
    (fixing_id,) = steps.held
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    clip_1, clip_3 = last_step("clip", 1), last_step("clip", 3)
    # Scene 2's fix lands first, while scenes 1 and 3's first clips are still being made.
    assert steps.held == [fixing_id, clip_1.pk, clip_3.pk]
    steps.run_next()

    assert row_of(plan, 2).landed_at is not None
    assert ads() == []

    run(fake_model, steps)

    # The first clips are the ad's first making: the producer is told, and assembles it.
    assert ads() == []
    for step in (clip_1, clip_3):
        step.refresh_from_db()
        assert step.result.endswith(f"Scene {step.scene.number} is finished. Tell the shop owner.")
    calling(fake_model, say, ("assemble_ad", {}))

    assert ads() == [1]
    ad = the_ad(1)
    assert [cut["clip"] for cut in ad.cuts] == [
        clip_of(clip_1).pk,
        clip_of(SceneStep.objects.get(pk=fixing_id)).pk,
        clip_of(clip_3).pk,
    ]
    assert times_shown(ad) == 1
    assert notes("ad_shown") == []


def test_first_clips_assemble_nothing_while_a_plan_waits_for_the_owners_yes(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    planned(plan_row(2, "new_clip", change=FIX_2), note=NOTE_2)

    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    run(fake_model, steps)

    # Nothing was said yes to, so this is the ad's first making as before: the producer
    # assembles it.
    assert ads() == []
    for scene in (1, 3):
        assert last_step("clip", scene).result.endswith(
            f"Scene {scene} is finished. Tell the shop owner."
        )


def test_an_early_fix_landing_last_assembles_the_first_ad(
    fake_model: FakeModel, early: None, steps: HeldSteps, say: Say
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    run(fake_model, steps)

    clip_fix_of_2(fake_model, steps)

    assert ads() == [1]
    ad = the_ad(1)
    assert [cut["clip"] for cut in ad.cuts][1] == clip_of(last_step()).pk
    assert times_shown(ad) == 1
    assert notes("ad_shown") == [ad_shown(ad)]


# --- Words ------------------------------------------------------------------------------------
