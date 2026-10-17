"""Code runs a fix plan (fix redesign, ticket 04; decisions 2, 6, 7, 13, 19): before the plan
is shown, code catches the rule can'ts and sets each scene's redo and estimate; after the
shop owner's yes it runs every scene at once with no producer turn in between, a picture
that passes its checks starting its clip by itself, and the new ad is assembled once every
scene has landed. The producer is told by system notes only. The scene models are faked at
the gateway, but the clips are real tiny videos and ffmpeg runs for real; the producer's
model is never scripted, so a producer turn would fail the test."""

from decimal import Decimal
from typing import Any

import pytest

from agents import run_plan
from agents.models import SystemNote, ToolCall
from agents.producer import Estimate, _clip_estimate, _estimate, _line_estimate
from chat import messages
from chat.models import Message, Session
from gateway.fake import FakeModel
from jobs import work
from jobs.models import (
    Fix,
    FixCheck,
    FixPlan,
    FixPlanScene,
    Job,
    ReferencePhoto,
    Scene,
    SceneStep,
)

from .conftest import HeldSteps, paid_for, producer_turns, said_yes_to_a_fix
from .test_fix_check import HANDLE_HIDDEN, not_made
from .test_fix_loop import TURN_REWRITE
from .test_producer_broll import TALKING_CHOICE, checked  # noqa: F401 (a fixture)
from .test_producer_picture_then_clip import (
    ads,
    assembled,  # noqa: F401 (a fixture)
    clip,
    picture,
    status,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("checked", "assembled"),
]

BRIGHTER = "Make the workshop brighter."
TURN = "The hand turns the mug all the way round."
NEW_LINE = "Stoneware that keeps your tea hot."


def scene(number: int) -> Scene:
    return Scene.objects.get(number=number)


def owner_says(text: str) -> Message:
    """The shop owner's message, as the chat keeps it, with no producer woken by it."""
    return messages.add(Session.objects.get(), role=Message.Role.USER, text=text)


def row(number: int, action: str, **fields: Any) -> FixPlanScene:
    return FixPlanScene(scene=scene(number), action=action, **fields)


def propose(*rows: FixPlanScene) -> FixPlan:
    said = owner_says("Some notes on the ad.")
    return run_plan.check_and_propose(
        Job.objects.get(), [{"seq": said.seq, "text": said.text}], list(rows)
    )


def say_yes(plan: FixPlan) -> None:
    """The shop owner's yes to the plan, as the chat records it (06's settle), then code
    runs it (08 calls run_after_yes as the yes arrives)."""
    plan.state = FixPlan.State.CONFIRMED
    plan.confirmed_by = owner_says("yes")
    plan.save(update_fields=["state", "confirmed_by"])
    run_plan.run_after_yes(plan)


def rows_of(plan: FixPlan) -> dict[int, FixPlanScene]:
    return {each.scene.number: each for each in plan.scenes.select_related("scene")}


def notes(kind: str | None = None) -> list[str]:
    found = SystemNote.objects.order_by("created_at", "id")
    if kind is not None:
        found = found.filter(kind=kind)
    return list(found.values_list("text", flat=True))


def held_kinds(steps: HeldSteps) -> list[tuple[int, str]]:
    return [
        (each.scene.number, each.kind)
        for each in SceneStep.objects.filter(pk__in=steps.held).select_related("scene")
    ]


# --- Before the plan is shown: rule can'ts and estimates -------------------------------------


def test_a_new_clip_for_a_talking_scene_cant_be_made() -> None:
    plan = propose(row(1, "new_clip", change=TURN))

    one = rows_of(plan)[1]
    assert one.cant == (
        "Scene 1 shows the person talking, and the movement of a talking clip can't be "
        "changed. Its picture or its line can change instead."
    )
    assert one.estimate_usd == one.estimate_up_to_usd == 0
    assert plan.state == FixPlan.State.PROPOSED


def test_a_new_line_too_long_for_its_scene_cant_be_made() -> None:
    long_line = " ".join(["word"] * 40)

    plan = propose(row(3, "new_line", change="Say it longer.", line=long_line))

    assert rows_of(plan)[3].cant == (
        "Scene 3's new line takes about 20 seconds to say, longer than the scene can last "
        "(18 seconds). Send a shorter line."
    )


def test_going_back_to_a_version_the_scene_doesnt_have_cant_be_made() -> None:
    plan = propose(row(2, "go_back", change="The first one.", go_back_to=4))

    assert rows_of(plan)[2].cant == "Scene 2 has only version 1, so there is no version 4."


def test_photos_on_a_talking_scene_cant_be_used() -> None:
    photo = {"photo": 1, "file": "owner/1.jpg", "thing": "black mug"}
    photo |= {"in_picture": True, "during_action": False}

    plan = propose(row(1, "new_picture", change="A black mug.", photos=[photo]))

    assert rows_of(plan)[1].cant == (
        "Scene 1 shows the person talking, and your photos can't be used in a scene where the "
        "person talks yet."
    )


def test_an_unclear_photo_cant_be_used() -> None:
    photo = {"photo": 2, "file": "owner/2.jpg", "thing": "black mug", "clear": False}
    photo |= {"in_picture": True, "during_action": False}

    plan = propose(row(2, "new_picture", change="A black mug.", photos=[photo]))

    assert rows_of(plan)[2].cant == (
        "Owner photo 2 doesn't show the black mug clearly enough to copy its look. Send a "
        "clearer photo of the black mug."
    )


def owner_photo(number: int, thing: str, **answers: bool) -> dict[str, Any]:
    return {
        "photo": number,
        "file": f"owner/{number}.jpg",
        "thing": thing,
        "in_picture": answers.get("in_picture", True),
        "during_action": answers.get("during_action", False),
    }


def in_use(number: int, thing: str) -> None:
    """Scene 2 already uses owner photo `number` of `thing`, from an earlier fix."""
    ReferencePhoto.objects.create(
        scene=scene(2),
        file=f"owner/{number}.jpg",
        thing=thing,
        use=ReferencePhoto.Use.PICTURE,
        added_by=said_yes_to_a_fix(2, f"A {thing}.", "picture"),
    )


def test_a_scene_using_more_than_three_photos_cant_be_made() -> None:
    in_use(1, "black mug")
    in_use(2, "oak bench")
    photos = [owner_photo(3, "teapot"), owner_photo(4, "linen cloth")]

    plan = propose(row(2, "new_picture", change="A teapot and a cloth.", photos=photos))

    assert rows_of(plan)[2].cant == (
        "Scene 2 would use 4 of your photos (the black mug, the oak bench, the teapot and the "
        "linen cloth), and a scene uses at most 3. Say which to stop using."
    )


def test_the_same_photo_already_in_use_cant_change_anything() -> None:
    in_use(1, "black mug")

    plan = propose(
        row(2, "new_picture", change="A black mug.", photos=[owner_photo(1, "black mug")])
    )

    assert rows_of(plan)[2].cant == (
        "Scene 2 already uses owner photo 1 for the black mug, so nothing would change."
    )


def test_a_new_clip_with_a_photo_of_a_thing_in_the_picture_is_a_new_picture() -> None:
    photo = owner_photo(1, "black mug", in_picture=True)

    plan = propose(row(2, "new_clip", change="A black mug.", photos=[photo]))

    two = rows_of(plan)[2]
    assert (two.action, two.cant) == ("new_picture", "")
    assert two.estimate_usd == _estimate(scene(2), Fix.Redo.PICTURE, "").about + (Decimal("0.10"))


def amounts(estimate: Estimate) -> tuple[Decimal, Decimal]:
    return estimate.about, estimate.up_to


def test_each_scenes_estimate_is_what_todays_fix_would_cost() -> None:
    plan = propose(
        row(1, "new_picture", change=BRIGHTER),
        row(2, "new_clip", change=TURN),
        row(3, "new_line", change="Say it warmer.", line=NEW_LINE),
    )

    rows = rows_of(plan)
    assert (rows[1].estimate_usd, rows[1].estimate_up_to_usd) == amounts(
        _estimate(scene(1), Fix.Redo.PICTURE, BRIGHTER)
    )
    assert (rows[2].estimate_usd, rows[2].estimate_up_to_usd) == amounts(
        _estimate(scene(2), Fix.Redo.CLIP, TURN)
    )
    assert (rows[3].estimate_usd, rows[3].estimate_up_to_usd) == amounts(
        _line_estimate(scene(3), f'"{NEW_LINE}"')
    )
    assert plan.estimate_usd() == sum(each.estimate_usd for each in rows.values())
    # The producer is told the plan, scene by scene, with no picture or reasoning.
    (shown,) = notes("plan_shown")
    assert shown.startswith("A fix plan is shown to the shop owner, waiting for their yes. ")
    assert f"Scene 1: new starting picture, then its clip: {BRIGHTER} " in shown
    assert f"Scene 2: new clip: {TURN} " in shown
    assert f'Scene 3: new line, "{NEW_LINE}", then its picture and clip: Say it warmer. ' in shown
    assert shown.endswith(f"About {run_plan.dollars(plan.estimate_usd())} in all.")


def test_a_plan_with_no_change_and_cant_costs_nothing() -> None:
    plan = propose(row(1, "no_change"), row(2, "new_picture", change="X", cant="Can't."))

    assert plan.estimate_usd() == 0
    assert rows_of(plan)[2].cant == "Can't."


# --- After the yes: code runs every scene ----------------------------------------------------


@pytest.fixture
def held(steps: HeldSteps, fake_model: FakeModel) -> HeldSteps:
    return steps


def test_three_scenes_all_land_with_no_producer_turn_and_the_ad_is_assembled_once(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    turns, ads_before = producer_turns(), len(ads())
    old = {number: clip(number) for number in (1, 2, 3)}
    plan = propose(
        row(1, "new_picture", change=BRIGHTER),
        row(2, "new_clip", change=TURN),
        row(3, "go_back", change="The first one.", go_back_to=1),
    )
    paid = paid_for()

    say_yes(plan)

    # Going back happened on the spot, free: nothing was paid, no step is queued for it.
    assert paid_for() == paid
    rows = rows_of(plan)
    assert rows[3].landed_at is not None
    assert work.versions(scene(3))[-1].label == "v2 back to v1"
    # The two others were queued at once, each step from its plan row.
    assert held_kinds(held) == [(1, "starting_picture"), (2, "clip")]
    assert {step.plan_scene_id for step in SceneStep.objects.filter(pk__in=held.held)} == {
        rows[1].pk,
        rows[2].pk,
    }
    assert notes("fixes_started") == [
        "The shop owner said yes to the plan: scene 1's new starting picture and scene 2's new "
        "clip are being made. Scene 3 went back to version 1, for nothing."
    ]

    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    held.run_next()

    # Scene 1's picture passed its checks, so the worker queued its clip by itself.
    assert held_kinds(held) == [(2, "clip"), (1, "clip")]
    clip_1 = SceneStep.objects.get(pk=held.held[-1])
    assert clip_1.picture == picture(1)
    assert clip_1.plan_scene_id == rows[1].pk
    assert len(ads()) == ads_before

    held.run_held()

    assert producer_turns() == turns
    assert {status(number) for number in (1, 2, 3)} == {"finished"}
    assert len(ads()) == ads_before + 1
    ad = ads()[-1]
    assert [cut["clip"] for cut in ad.cuts] == [clip(1).pk, clip(2).pk, clip(3).pk]
    assert clip(1) != old[1] and clip(2) != old[2]
    assert clip(3).file == old[3].file
    assert all(each.landed_at is not None for each in rows_of(plan).values())
    assert len(notes("scene_done")) == 3
    assert notes("ad_shown") == [
        f"Every scene of the plan is done, so the new ad (version {ad.version}) was assembled "
        "and shown to the shop owner."
    ]
    # The producer was never woken: no session is marked as having one running.
    assert not Session.objects.get().producer_running


def test_steps_from_a_plan_give_the_change_to_the_steps_own_prompt_writers(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    say_yes(propose(row(1, "new_picture", change=BRIGHTER), row(2, "new_clip", change=TURN)))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    held.run_held()

    picture_step = SceneStep.objects.filter(scene__number=1, kind="starting_picture").last()
    clip_step = SceneStep.objects.filter(scene__number=2, kind="clip").last()
    assert picture_step is not None and clip_step is not None
    assert (picture_step.note, clip_step.note) == (BRIGHTER, TURN)
    assert clip_step.motion_prompt == TURN_REWRITE["motion_prompt"]
    # The new versions are labelled with the change, as decision 17 says.
    assert work.versions(scene(1))[-1].label == f"v2 {BRIGHTER}"
    assert work.versions(scene(2))[-1].label == f"v2 {TURN}"
    # A fix's own clip is made with no note.
    assert SceneStep.objects.filter(scene__number=1, kind="clip").last().note == ""  # type: ignore[union-attr]


def test_the_steps_are_charged_to_code_not_to_the_producer(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    plan = propose(row(2, "new_clip", change=TURN))
    say_yes(plan)
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)
    held.run_held()

    step = SceneStep.objects.filter(scene__number=2, kind="clip").last()
    assert step is not None
    assert step.tool_call.agent == run_plan.FIX_PLAN
    assert rows_of(plan)[2].cost_usd() == step.tool_call.cost_usd() > 0
    assert notes("scene_done") == [
        f"Scene 2's new clip is done. It cost {run_plan.dollars(rows_of(plan)[2].cost_usd())}."
    ]


def test_running_the_plan_again_starts_nothing_twice(held: HeldSteps) -> None:
    plan = propose(row(2, "new_clip", change=TURN))
    say_yes(plan)

    run_plan.run_after_yes(plan)

    assert held_kinds(held) == [(2, "clip")]
    assert Fix.objects.filter(scene__number=2, fix=TURN).count() == 1


def test_a_plan_not_said_yes_to_isnt_run(held: HeldSteps) -> None:
    plan = propose(row(2, "new_clip", change=TURN))

    with pytest.raises(AssertionError):
        run_plan.run_after_yes(plan)
    assert held.held == []


def test_a_line_change_runs_the_whole_chain_by_code(fake_model: FakeModel, held: HeldSteps) -> None:
    turns = producer_turns()
    say_yes(propose(row(3, "new_line", change="Say it warmer.", line=NEW_LINE)))

    assert scene(3).line == NEW_LINE
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()

    kinds = [
        kind
        for kind in SceneStep.objects.filter(scene__number=3, plan_scene__isnull=False)
        .order_by("id")
        .values_list("kind", flat=True)
    ]
    assert sorted(kinds) == ["clip", "line_audio", "starting_picture", "transcript"]
    assert kinds[-1] == "clip"
    made = clip(3)
    assert made.step is not None and made.step.line == NEW_LINE
    assert status(3) == "finished"
    assert ads()[-1].cuts[-1]["clip"] == made.pk
    assert producer_turns() == turns


def test_going_back_after_the_line_changed_pays_for_a_new_clip(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    say_yes(propose(row(3, "new_line", change="Say it warmer.", line=NEW_LINE)))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    held.run_held()
    first = work.versions(scene(3))[0]
    plan = propose(row(3, "go_back", change="The first picture.", go_back_to=1))
    three = rows_of(plan)[3]
    # The line changed since version 1, so its clip is made again, and the plan says so.
    assert three.remakes_clip
    assert (three.estimate_usd, three.estimate_up_to_usd) == amounts(
        _clip_estimate(scene(3), clip(3).made_from.seconds, from_note=False)  # type: ignore[union-attr, arg-type]
    )
    assert three.estimate_usd > 0
    paid = paid_for()

    say_yes(plan)

    assert held_kinds(held) == [(3, "clip")]
    held.run_held()
    assert paid_for()[len(paid) :] == ["make_talking_clip", "collect_talking_clip"]
    made = clip(3)
    assert made.picture is not None and made.picture.file == first.picture.file  # type: ignore[union-attr]
    assert made.step is not None and made.step.line == NEW_LINE
    assert work.versions(scene(3))[-1].label == "v3 back to v1"
    assert ads()[-1].cuts[-1]["clip"] == made.pk


def test_going_back_with_the_line_unchanged_pays_nothing(held: HeldSteps) -> None:
    plan = propose(row(3, "go_back", change="The first one.", go_back_to=1))
    assert rows_of(plan)[3].estimate_usd == 0
    paid, ads_before = paid_for(), len(ads())

    say_yes(plan)

    assert paid_for() == paid
    assert held.held == []
    # Every scene landed at once, so the new ad is assembled now, scene 3 with its first
    # clip's file again.
    assert len(ads()) == ads_before + 1
    assert ads()[-1].cuts[-1]["clip"] == clip(3).pk
    assert clip(3).file == work.versions(scene(3))[0].clip.file  # type: ignore[union-attr]
    assert not rows_of(plan)[3].remakes_clip


def test_a_failed_fix_check_stops_only_its_scene(fake_model: FakeModel, held: HeldSteps) -> None:
    ads_before = len(ads())
    plan = propose(row(1, "new_picture", change=BRIGHTER), row(2, "new_clip", change=TURN))
    say_yes(plan)
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    fake_model.respond("check_fix", not_made(HANDLE_HIDDEN), not_made(HANDLE_HIDDEN))
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    held.run_held()

    # Scene 1's picture failed its check: no clip was queued for it, and the owner was shown
    # both pictures. Scene 2 finished; the ad waits for the owner's word on scene 1.
    assert not SceneStep.objects.filter(scene__number=1, kind="clip", plan_scene__isnull=False)
    check = FixCheck.objects.get(verdict="failed")
    assert check.notice is not None
    assert status(2) == "finished"
    rows = rows_of(plan)
    assert rows[1].landed_at is None and rows[2].landed_at is not None
    assert len(ads()) == ads_before
    assert notes("check_failed") == [
        f"Scene 1's new starting picture (v2 {BRIGHTER}) may not have the change, so its clip "
        f"wasn't made: {HANDLE_HIDDEN} The chat showed the shop owner v1 first version and v2 "
        f"{BRIGHTER}, and waits for their answer."
    ]

    # The shop owner said go ahead (08 routes their answer): code carries on with that scene.
    run_plan.go_ahead(rows[1])
    assert held_kinds(held) == [(1, "clip")]
    held.run_held()

    assert status(1) == "finished"
    assert len(ads()) == ads_before + 1
    assert rows_of(plan)[1].landed_at is not None


def test_a_step_that_fails_tells_the_producer_by_a_note_and_the_others_finish(
    fake_model: FakeModel, held: HeldSteps
) -> None:
    turns = producer_turns()
    plan = propose(row(1, "new_picture", change=BRIGHTER), row(2, "new_clip", change=TURN))
    say_yes(plan)
    # The picture writer's answer can't be used, so scene 1's picture fails.
    fake_model.respond("choose_starting_picture", {"nonsense": True})
    fake_model.respond("rewrite_clip_prompt", TURN_REWRITE)

    held.run_held()

    rows = rows_of(plan)
    assert rows[1].error != "" and rows[1].landed_at is None
    assert rows[2].landed_at is not None
    assert notes("error")[0].startswith("Scene 1's new starting picture couldn't be made: ")
    assert producer_turns() == turns
    # Scene 1 kept its old clip, so the ad is assembled with scene 2's new one.
    assert ads()[-1].cuts[1]["clip"] == clip(2).pk


def test_photos_on_a_plan_row_are_the_scenes_from_the_yes(held: HeldSteps) -> None:
    photo = owner_photo(1, "black mug", in_picture=False, during_action=True)
    plan = propose(row(2, "new_clip", change="Pour into a black mug.", photos=[photo]))

    say_yes(plan)

    used = ReferencePhoto.objects.get(scene__number=2)
    fix = rows_of(plan)[2].fix
    assert fix is not None
    assert (used.file, used.thing, used.use, used.added_by) == (
        "owner/1.jpg",
        "black mug",
        "clip",
        fix,
    )
    assert fix.photos[0]["use"] == "clip"
    step = SceneStep.objects.get(pk=held.held[0])
    assert list(step.reference_photos.all()) == [used]
    assert ToolCall.objects.filter(agent="producer", tool="restate_fix").count() == 0


def test_a_rule_cant_is_added_after_the_deciders_own() -> None:
    plan = propose(row(1, "new_clip", change=TURN, cant="The person's look can't change."))

    assert rows_of(plan)[1].cant == (
        "The person's look can't change. Scene 1 shows the person talking, and the movement "
        "of a talking clip can't be changed. Its picture or its line can change instead."
    )


def test_a_scene_busy_at_the_yes_runs_once_its_step_is_done(held: HeldSteps) -> None:
    busy = SceneStep.objects.create(
        scene=scene(2),
        kind=SceneStep.Kind.CLIP,
        tool_call=ToolCall.objects.filter(agent="producer").latest("id"),
        **SceneStep.made_for(scene(2)),
    )
    plan = propose(row(2, "new_clip", change=TURN))

    say_yes(plan)

    assert held.held == []
    busy.status = SceneStep.Status.FAILED
    busy.save(update_fields=["status"])
    run_plan.carry_on_scene(scene(2))
    assert held_kinds(held) == [(2, "clip")]


def test_a_thing_in_the_picture_and_more_during_the_action_is_drawn_in_and_sent_to_the_clip(
    held: HeldSteps,
) -> None:
    photo = owner_photo(1, "black mug", in_picture=True, during_action=True)
    plan = propose(row(2, "new_clip", change="A black mug.", photos=[photo]))
    assert rows_of(plan)[2].action == "new_picture"

    say_yes(plan)

    used = ReferencePhoto.objects.filter(scene__number=2).order_by("use")
    assert [(each.file, each.thing, each.use) for each in used] == [
        ("owner/1.jpg", "black mug", "clip"),
        ("owner/1.jpg", "black mug", "picture"),
    ]
    step = SceneStep.objects.get(pk=held.held[0])
    assert step.kind == "starting_picture"
    assert set(step.reference_photos.all()) == set(used)


def test_a_photo_on_a_new_line_goes_in_its_new_picture() -> None:
    plan = propose(
        row(
            2,
            "new_line",
            change="Say it warmer.",
            line="A warm mug.",
            photos=[owner_photo(1, "black mug")],
        )
    )

    assert rows_of(plan)[2].cant == ""


def test_a_photo_on_a_go_back_cant_be_used() -> None:
    plan = propose(
        row(2, "go_back", change="Back.", go_back_to=1, photos=[owner_photo(1, "black mug")])
    )

    assert rows_of(plan)[2].cant == (
        "Scene 2's change is going back to a version, and a photo changes how something looks. "
        "Send the photo as its own change."
    )
