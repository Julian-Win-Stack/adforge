"""Code runs a fix plan (fix redesign, ticket 04; decisions 2, 6, 7, 13, 19): before the plan
is shown, the rule can'ts are caught and each scene's redo and estimate set; after the shop
owner's yes, every scene is run at once with no producer turn in between, a picture that
passes its checks starting its clip by itself, and the new ad is assembled once every scene
has landed. The producer is told by system notes only (decision 20).

Two entry points: `check_and_propose`, called once the decider has decided, and
`run_after_yes`, called once the plan is confirmed. `go_ahead` carries a scene on after the
shop owner said go ahead to its failed fix check. The worker calls `step_finished` and
`step_failed` for each step a plan started, and `carry_on_scene` after any other step, so a
scene busy at the yes is run once it is free."""

import logging
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from chat.models import Message, Session
from jobs import reference_photos, work
from jobs.checks import LONGEST_BROLL_LINE_SECONDS, LONGEST_LINE_SECONDS
from jobs.fix_plans import PlanCant, propose
from jobs.models import (
    Fix,
    FixCheck,
    FixPlan,
    FixPlanScene,
    Job,
    ProducedItem,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.notices import post_notice

from . import step_signals
from .loop import Refused, why_it_failed
from .models import SystemNote, ToolCall
from .money import dollars
from .producer import (
    CLEAN_PICTURE_USD,
    MILLIONTH,
    Estimate,
    _clip_estimate,
    _current_picture,
    _each_call_usd,
    _estimate,
    _latest_clip,
    _line_estimate,
    _listed,
    _picture_only_estimate_usd,
    _seconds_to_say,
    _use_the_photos,
    assemble_the_ad,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FIX_PLAN",
    "agent_gave_up",
    "carry_on_scene",
    "check_and_propose",
    "first_making",
    "go_ahead",
    "held_on_a_check",
    "run_after_yes",
    "runs_something",
    "step_failed",
    "step_finished",
]

# The agent name on the tool calls code makes for a plan's steps: the producer is given only
# its own (loop._finished_steps), so it is never woken by them or given their results.
FIX_PLAN = "fix_plan"
# The fixing agent's purpose in the gateway's catalog (spec section 3).
FIXING_AGENT_PURPOSE = "fix_scene"

Action = FixPlanScene.Action

_REDO = {
    Action.FIX: Fix.Redo.SCENE,
    Action.NEW_PICTURE: Fix.Redo.PICTURE,
    Action.NEW_CLIP: Fix.Redo.CLIP,
    Action.NEW_LINE: Fix.Redo.LINE,
}


# --- Before the plan is shown -----------------------------------------------------------------


def check_and_propose(
    job: Job,
    notes: Sequence[dict[str, Any]],
    scenes: Sequence[FixPlanScene],
    question: str = "",
    cants: Sequence[PlanCant] = (),
) -> FixPlan:
    """Record the decider's plan for the shop owner's notes of this round, waiting for their
    yes: each scene's row (unsaved, as `fix_plans.propose` takes them) checked against the
    rule can'ts of decision 13 first, a can't kept in its `cant`; a new clip or line with a
    photo of a thing in the starting picture made a new picture, as the route says; each doable
    scene's estimate set as today's fix of the same redo would cost (a go back is free unless
    its clip must be made again). The producer is told the plan, scene by scene."""
    counted: set[tuple[str, str]] = set()
    if settings.ROUTE_OWNER_PHOTOS_AFTER_THE_DECIDER:
        for row in scenes:
            _routed_again(job, row)
    for row in scenes:
        row.change = " ".join(row.change.split())
        row.line = " ".join(row.line.split())
        row.overlay = " ".join(row.overlay.split())
        # The decider's can'ts come first; a rule's is added after, never in place of it.
        row.cant = " ".join(filter(None, [row.cant, _rule_cant(row)]))
        # Whether a photo is clear is the decider's to say, and code's rule to keep: the row
        # keeps the photo as the plan shows it.
        row.photos = [
            {key: value for key, value in photo.items() if key != "clear"} for photo in row.photos
        ]
        if row.cant or row.action == Action.NO_CHANGE:
            row.estimate_usd = row.estimate_up_to_usd = Decimal(0)
            continue
        if row.action == Action.NEW_CLIP and any(photo["in_picture"] for photo in row.photos):
            row.action = Action.NEW_PICTURE
            row.new_movement = True
        if row.action == Action.NEW_LINE and any(photo["in_picture"] for photo in row.photos):
            # A new line keeps its starting picture, but a photo of a thing in it changes how
            # it looks: a new picture, with the new line.
            row.action = Action.NEW_PICTURE
        if row.action == Action.GO_BACK:
            assert row.go_back_to is not None, "a go back names its version"
            row.remakes_clip = not work.go_back_is_free(row.scene, row.go_back_to)
        estimate = _row_estimate(row, counted)
        row.estimate_usd, row.estimate_up_to_usd = estimate
    plan = propose(job, notes, scenes, question=question, cants=cants)
    _note(plan, SystemNote.Kind.PLAN_SHOWN, _shown(plan))
    return plan


def _routed_again(job: Job, row: FixPlanScene) -> None:
    """Decision 5's fallback: each of the row's photos routed by the route call, looking at
    the scene's starting picture and the photo, in place of the decider's answers. An
    answer that can't be had leaves the decider's."""
    picture = _shown_picture(row.scene)
    if picture is None:
        return
    for photo in row.photos:
        answer = reference_photos.where_it_goes(
            job,
            picture=picture.file,
            photo=str(photo["file"]),
            thing=str(photo["thing"]),
            shows=row.scene.shows,
            change=row.change,
        )
        if answer is None:
            continue
        photo["in_picture"] = answer.where != "appears_during_the_clip"
        photo["during_action"] = answer.where == "appears_during_the_clip" or (
            answer.where == "in_the_picture" and bool(photo["during_action"])
        )
        photo["clear"] = answer.clear_enough


def first_making(plan: FixPlan) -> list[FixPlanScene]:
    """The plan's rows about a scene with no clip yet: the producer still makes it for the
    first time, with the shop owner's note, as before the ad had a clip."""
    return [row for row in plan.scenes.select_related("scene") if _before_its_clip(row.scene)]


def _before_its_clip(scene: Scene) -> bool:
    return not scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists()


def _rule_cant(row: FixPlanScene) -> str:
    """What the shop owner is told when the row breaks a rule code knows, or blank."""
    scene = row.scene
    number = scene.number
    if _before_its_clip(scene):
        return f"Scene {number} has no clip yet, so its note goes into making it."
    if row.action == Action.FIX and not scene.shows:
        return (
            f"Scene {number} shows the person talking, and the fixing agent only films scenes "
            "that show the product. Its picture or its line can change instead."
        )
    if row.action == Action.FIX and not scene.broll_kind:
        return (
            f"Scene {number} was planned before scenes had their B-roll labels, so it can't be "
            "filmed again by the fixing agent. Its picture can change instead."
        )
    if row.action == Action.NEW_CLIP and not scene.shows:
        return (
            f"Scene {number} shows the person talking, and the movement of a talking clip "
            "can't be changed. Its picture or its line can change instead."
        )
    if row.action == Action.NEW_CLIP and not scene.broll_kind:
        return (
            f"Scene {number} was planned before scenes had their B-roll labels, so its "
            "movement can't be changed. Its picture can change instead."
        )
    if row.line:
        longest = LONGEST_BROLL_LINE_SECONDS if scene.shows else LONGEST_LINE_SECONDS
        seconds = _seconds_to_say(scene, row.line)
        if seconds > longest:
            return (
                f"Scene {number}'s new line takes about {round(seconds)} seconds to say, longer "
                f"than the scene can last ({longest} seconds). Send a shorter line."
            )
    if row.action == Action.GO_BACK:
        count = len(work.versions(scene))
        if row.go_back_to is None or not 1 <= row.go_back_to <= count:
            has = "only version 1" if count == 1 else f"versions 1 to {count}"
            return f"Scene {number} has {has}, so there is no version {row.go_back_to}."
        if work.versions(scene)[row.go_back_to - 1].picture is None and not (
            work.go_back_is_free(scene, row.go_back_to)
        ):
            # A B-roll scene made from example pictures: its clip no longer says the line,
            # and there is no picture to make a new one from (work.NothingToBringBack).
            return (
                f"Scene {number}'s version {row.go_back_to} can't be brought back: its clip no "
                "longer says the scene's line, and it has no starting picture to make a new "
                "clip from."
            )
    return _photo_cant(row) if row.photos else ""


def _photo_cant(row: FixPlanScene) -> str:
    """Why the row's photos can't be used, or blank."""
    scene = row.scene
    number = scene.number
    if not scene.shows:
        return (
            f"Scene {number} shows the person talking, and your photos can't be used in a scene "
            "where the person talks yet."
        )
    if not scene.broll_kind:
        return (
            f"Scene {number} was planned before scenes had their B-roll labels, so your photos "
            "can't be used in it."
        )
    # A new line's clip takes a photo of a thing only during it; with one of a thing in its
    # picture, the row is made a new picture after this.
    if row.action == Action.GO_BACK:
        return (
            f"Scene {number}'s change is going back to a version, and a photo changes how "
            "something looks. Send the photo as its own change."
        )
    using = list(scene.photos_in_use())
    for photo in row.photos:
        thing = str(photo["thing"])
        if photo.get("clear") is False:
            return (
                f"Owner photo {photo['photo']} doesn't show the {thing} clearly enough to copy "
                f"its look. Send a clearer photo of the {thing}."
            )
        if any(
            used.file == photo["file"] and reference_photos.same_thing(used.thing, thing)
            for used in using
        ):
            return (
                f"Scene {number} already uses owner photo {photo['photo']} for the {thing}, so "
                "nothing would change."
            )
    # One photo per thing: a thing taken again replaces the photo of it in use, and one used
    # in the picture and during the clip is still one photo.
    things: list[str] = []
    for thing in [used.thing for used in using] + [str(photo["thing"]) for photo in row.photos]:
        if not any(reference_photos.same_thing(thing, other) for other in things):
            things.append(thing)
    if len(things) > reference_photos.MOST_PHOTOS:
        return (
            f"Scene {number} would use {len(things)} of your photos "
            f"({_listed([f'the {thing}' for thing in things])}), and a scene uses at most "
            f"{reference_photos.MOST_PHOTOS}. Say which to stop using."
        )
    return ""


def _row_estimate(row: FixPlanScene, counted: set[tuple[str, str]]) -> Estimate:
    """What the row is estimated to cost, as today's fix of the same redo: with, for the first
    row of the plan taking each thing from a photo, its clean picture, unless one was already
    drawn for the ad. `counted` holds the photos already counted."""
    scene = row.scene
    if row.action == Action.FIX:
        estimate = _fix_estimate(scene)
    elif row.action == Action.GO_BACK:
        if not row.remakes_clip:
            return Estimate(Decimal(0), Decimal(0))
        return _quantized(_clip_estimate(scene, _audio_seconds(scene), from_note=False))
    elif row.line:
        # A new picture asked for with a new line: both, then the clip. A new line keeps the
        # picture, or draws one for a scene that has none.
        new_picture = row.action == Action.NEW_PICTURE or _shown_picture(scene) is None
        estimate = _quantized(_line_estimate(scene, f'"{row.line}"', new_picture=new_picture))
    else:
        estimate = _estimate(scene, _REDO[Action(row.action)], row.change)
    clean = Decimal(0)
    for photo in row.photos:
        key = (str(photo["file"]), str(photo["thing"]).casefold())
        if key in counted or (
            ReferencePhoto.objects.filter(scene__job=scene.job, file=key[0], thing__iexact=key[1])
            .exclude(drawn="")
            .exists()
        ):
            continue
        counted.add(key)
        clean += CLEAN_PICTURE_USD
    return Estimate(estimate.about + clean, estimate.up_to + clean)


# The fixing agent's turns for one try: a look or two, then film_again. A GUESS, until real
# runs of the agent in the app are recorded (fixing agent build, T3).
AGENT_TURNS_PER_TRY = 3
# The most tries the fixing agent makes of one scene (spec section 3, the money guard).
MOST_TRIES = 6


def _fix_estimate(scene: Scene) -> Estimate:
    """What a fix row is estimated to cost: one try of the fixing agent (a new starting
    picture, its clip and the clip judge's asks, which check the shop owner's change too, and
    the agent's turns), up to MOST_TRIES of them."""
    clip = _clip_estimate(scene, _audio_seconds(scene), from_note=False)
    one_try = (
        _picture_only_estimate_usd(scene)
        + clip.about
        + AGENT_TURNS_PER_TRY * _each_call_usd(scene, [FIXING_AGENT_PURPOSE])
    )
    return _quantized(Estimate(one_try, MOST_TRIES * one_try))


def _quantized(estimate: Estimate) -> Estimate:
    return Estimate(*(amount.quantize(MILLIONTH, rounding=ROUND_HALF_UP) for amount in estimate))


def _audio_seconds(scene: Scene) -> float:
    """How long the scene's line as it stands lasts: its audio's, or as the voice would say
    it when its audio isn't made yet."""
    audio = _audio(scene)
    if audio is not None and audio.seconds is not None:
        return audio.seconds
    return _seconds_to_say(scene, scene.line)


def _shown(plan: FixPlan) -> str:
    """The plan as the producer is told it: each scene's change, in plain words."""
    lines = [_line_of(row) for row in plan.scenes.select_related("scene")]
    cants = [f"Can't: {cant['text']}" for cant in plan.cants]
    question = [f"Question for the shop owner: {plan.question}"] if plan.question else []
    total = f"About {dollars(plan.estimate_usd())} in all."
    return " ".join(
        ["A fix plan is shown to the shop owner, waiting for their yes.", *lines, *cants]
        + question
        + [total]
    )


def _line_of(row: FixPlanScene) -> str:
    number = row.scene.number
    if _before_its_clip(row.scene):
        # The producer makes it, as before the ad had a clip (design thread, 04:22 Oct 17).
        return (
            f"Scene {number} has no clip yet, so it isn't in the plan: the shop owner's note "
            "on it is yours to make it with."
        )
    if row.cant:
        return f"Scene {number} can't change: {row.cant}"
    if row.action == Action.GO_BACK or not row.change:
        return f"Scene {number}: {_what(row)}."
    return f"Scene {number}: {_what(row)}: {row.change}"


def _what(row: FixPlanScene) -> str:
    if row.action == Action.FIX:
        return "fixed by the fixing agent, to this goal"
    if row.action == Action.NEW_PICTURE and row.line:
        return f'new starting picture and new line, "{row.line}", then its clip'
    if row.action == Action.NEW_PICTURE:
        return "new starting picture, then its clip"
    if row.action == Action.NEW_CLIP:
        return "new clip"
    if row.action == Action.NEW_LINE:
        return f'new line, "{row.line}", then its clip from the same starting picture'
    if row.action == Action.GO_BACK:
        return f"back to version {row.go_back_to}" + (
            ", its clip made again with the line as it stands"
            if row.remakes_clip
            else ", for nothing"
        )
    return "no change"


# --- After the yes ---------------------------------------------------------------------------


def run_after_yes(plan: FixPlan) -> None:
    """Run a confirmed plan: every scene that changes at once. A go back is done on the spot;
    a new picture, clip or line has its first steps queued, each recorded as the plan row it
    runs, and the worker carries each scene on as its steps land. Run again, nothing is
    started twice."""
    assert plan.state == FixPlan.State.CONFIRMED and plan.confirmed_by_id is not None, (
        "a plan is run once the shop owner said yes to it"
    )
    with transaction.atomic():
        plan = FixPlan.objects.select_for_update().get(pk=plan.pk)
        rows = [
            row
            for row in plan.scenes.select_related("scene")
            if _doable(row) and row.started_at is None
        ]
        if not rows:
            return
        for row in rows:
            row.started_at = timezone.now()
            if row.action != Action.GO_BACK:
                row.fix = _confirmed_fix(plan, row)
            row.save(update_fields=["started_at", "fix"])
            _no_longer_held(row)
        for row in rows:
            _carry_on(row)
        rows = [FixPlanScene.objects.select_related("scene").get(pk=row.pk) for row in rows]
        _note(plan, SystemNote.Kind.FIXES_STARTED, _started(rows))
        _assemble_once_landed(plan)


# A failed fix check whose picture is being drawn again, or was, waits for nobody: the try
# after it decides. One whose redraw failed or stopped waits for the shop owner again.
DRAWN_AGAIN = [SceneStep.Status.RUNNING, SceneStep.Status.FINISHED]


def _no_longer_held(row: FixPlanScene) -> None:
    """An older plan's row of the same scene whose fix check failed, and waits for the shop
    owner's answer, was answered by this newer plan (flow step 9: anything but go ahead is
    planned again). It waits no more, so it never holds back the scene's bring-back or its
    plan's ad."""
    waiting = FixCheck.objects.filter(
        step__plan_scene=OuterRef("pk"),
        verdict=FixCheck.Verdict.FAILED,
        gone_ahead_at__isnull=True,
    ).exclude(
        # Its picture drawn again, the try after it decides. One still being drawn again
        # was answered as well: no redraw is paid for once the scene is planned again.
        step__redraws__status=SceneStep.Status.FINISHED
    )
    FixPlanScene.objects.filter(
        scene=row.scene_id,
        pk__lt=row.pk,
        started_at__isnull=False,
        landed_at__isnull=True,
        error="",
    ).filter(Exists(waiting)).update(
        error=f"Scene {row.scene.number} was planned again before its failed fix check was "
        "answered."
    )


def runs_something(plan: FixPlan) -> bool:
    """Whether a yes to `plan` would run anything: it isn't only can'ts, a question, or
    scenes left as they are. The chat offers no yes button for one that doesn't."""
    return any(_doable(row) for row in plan.scenes.all())


def _doable(row: FixPlanScene) -> bool:
    return row.action != Action.NO_CHANGE and not row.cant


def _confirmed_fix(plan: FixPlan, row: FixPlanScene) -> Fix:
    """The Fix the row's steps are made for, confirmed by the plan's yes: the worker's fix
    check, the clip judge's look at the clip the shop owner complained about, and their
    photos go by it. Its photos are the scene's from now on."""
    fix = Fix.objects.create(
        job=plan.job,
        scene=row.scene,
        fix=row.change,
        redo=_REDO[Action(row.action)],
        notes=plan.notes,
        estimate_usd=row.estimate_usd,
        asked=_code_call(row, "run_fix_plan"),
        confirmed_by=plan.confirmed_by,
        photos=[
            {
                "photo": photo["photo"],
                "file": photo["file"],
                "thing": reference_photos.name_of(str(photo["thing"])),
                "use": use.value,
                "where": "the decider looked",
            }
            for photo in row.photos
            for use in _uses(photo)
        ],
    )
    _use_the_photos(fix)
    return fix


def _uses(photo: dict[str, Any]) -> list[ReferencePhoto.Use]:
    """How a photo's thing is used (decision 21): drawn into the new starting picture when it
    is in the picture now, and also sent to the clip in reference mode when more of it shows
    during the action (design thread 00:30 Oct 17); only to the clip when it shows only
    during the action. A thing in neither belongs in the picture from the start."""
    if photo["during_action"] and not photo["in_picture"]:
        return [ReferencePhoto.Use.CLIP]
    if photo["during_action"]:
        return [ReferencePhoto.Use.PICTURE, ReferencePhoto.Use.CLIP]
    return [ReferencePhoto.Use.PICTURE]


def _started(rows: list[FixPlanScene]) -> str:
    made = [
        f"scene {row.scene.number}'s {_made(row)}" for row in rows if row.action != Action.GO_BACK
    ]
    told = ["The shop owner said yes to the plan:"]
    if made:
        told.append(f"{_listed(made)} {'is' if len(made) == 1 else 'are'} being made.")
    for row in rows:
        if row.action == Action.GO_BACK:
            told.append(
                f"Scene {row.scene.number} went back to version {row.go_back_to}"
                + (", for nothing." if row.landed_at else ", and its clip is being made again.")
            )
    return " ".join(told)


def _made(row: FixPlanScene) -> str:
    """What the row makes, as "new starting picture"."""
    if row.action == Action.GO_BACK:
        return f"go back to version {row.go_back_to}"
    if row.action == Action.FIX:
        return "fix"
    if row.action == Action.NEW_PICTURE and row.line:
        return "new starting picture and new line"
    return {
        Action.NEW_PICTURE: "new starting picture",
        Action.NEW_CLIP: "new clip",
        Action.NEW_LINE: "new line",
    }[Action(row.action)]


def go_ahead(row: FixPlanScene) -> None:
    """The shop owner said go ahead to the row's failed fix check (08 routes their answer):
    its scene carries on, with the new version as it is."""
    with transaction.atomic():
        # The job, then the chat, before the row, as every writer of a plan locks them: a
        # step of the scene finishing meanwhile locks the job, then carries this row on.
        Job.objects.select_for_update().get(pk=row.plan.job_id)
        Session.objects.select_for_update().get(pk=row.plan.session_id)
        FixCheck.objects.filter(
            step__plan_scene=row, verdict=FixCheck.Verdict.FAILED, gone_ahead_at__isnull=True
        ).exclude(step__redraws__status__in=DRAWN_AGAIN).update(gone_ahead_at=timezone.now())
        _carry_on(row)
        _assemble_once_landed(row.plan)


def swapped_in(step: SceneStep) -> None:
    """Once the shop owner swapped a try of `step`'s clip into the ad (#180): a plan's scene
    held on that step's failed fix check goes ahead with it, as their go ahead; else the ad,
    once there is one and no plan is still landing, is assembled again from it, for
    nothing."""
    row = step.plan_scene
    if row is not None and row in held_on_a_check(Session.objects.get(pk=row.plan.session_id)):
        go_ahead(row)
        return
    job = step.scene.job
    if not job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).exists():
        return
    landing = sorted(
        set(
            FixPlanScene.objects.filter(
                plan__job=job, started_at__isnull=False, landed_at__isnull=True, error=""
            ).values_list("scene__number", flat=True)
        )
    )
    if landing:
        # Its plan's ad is assembled once it lands, from the try swapped in; one held on a
        # failed fix check waits for the shop owner, who is told what they wait for.
        named = (
            f"scene {landing[0]}'s fix is"
            if len(landing) == 1
            else f"the fixes of scenes {', '.join(map(str, landing))} are"
        )
        post_notice(
            job,
            f"The ad will be put together again with scene {step.scene.number}'s new clip "
            f"once {named} done.",
            Message.Level.INFO,
        )
        return
    if job.session is None:
        return
    top = work.versions(step.scene)[-1]
    try:
        with transaction.atomic():
            assemble_the_ad(job.session, job, f"scene {step.scene.number} {top.label}")
    except Refused:
        # A scene not finished yet: the ad is assembled once it is, from the try swapped in.
        return
    # Assembly failing for a reason nobody expected doesn't undo the swap: the chat says so.
    except Exception as error:
        logger.exception("Assembling the ad again after a swap in scene %s failed", step.scene_id)
        post_notice(
            job,
            f"The ad couldn't be put together again with scene {step.scene.number}'s new "
            f"clip: {why_it_failed(error, 'it')} Ask for it to be assembled again.",
            Message.Level.PROBLEM,
        )


def held_on_a_check(session: Session, before: int | None = None) -> list[FixPlanScene]:
    """The plan rows whose fix check failed and wait for the shop owner's answer (flow step
    9), oldest first: go ahead carries them on, anything else is planned again. A check
    waits only once its notice is posted, and, with `before`, only for a message numbered
    after that notice: one sent before it isn't an answer to it. A row whose scene a newer
    plan started on since no longer waits."""
    failed = FixCheck.objects.filter(
        step__plan_scene__plan__session=session,
        verdict=FixCheck.Verdict.FAILED,
        gone_ahead_at__isnull=True,
        notice__isnull=False,
    )
    if before is not None:
        failed = failed.filter(notice__seq__lt=before)
    newer = FixPlanScene.objects.filter(
        scene=OuterRef("scene"), started_at__isnull=False, pk__gt=OuterRef("pk")
    )
    return list(
        FixPlanScene.objects.filter(
            pk__in=failed.values("step__plan_scene"), landed_at__isnull=True, error=""
        )
        .exclude(Exists(newer))
        .select_related("scene", "plan")
        .order_by("id")
    )


# --- The worker carries each scene on ---------------------------------------------------------


def step_finished(step: SceneStep, check: FixCheck | None) -> None:
    """For a step a plan started, once it is marked finished, in the transaction that does:
    draw a new starting picture that failed its fix check again, told why, while it has
    redraws left; once it has none, show the shop owner the failed check and tell the
    producer. Then carry its scene on, and assemble the new ad once every scene of the plan
    has landed."""
    row = step.plan_scene
    assert row is not None, "a plan's step runs one of its rows"
    # A newer plan may have planned the scene again while this try was drawn.
    row.refresh_from_db(fields=["error"])
    if check is not None and check.verdict == FixCheck.Verdict.FAILED and not row.error:
        drawn_again = work.to_draw_again(check) and (
            _start(row, row.scene, SceneStep.Kind.STARTING_PICTURE, note=step.note, redraw_of=step)
            is not None
        )
        if not drawn_again:
            work.say_the_fix_check_failed(check)
            _note(row.plan, SystemNote.Kind.CHECK_FAILED, _check_failed(row, check))
    if check is not None and check.verdict == FixCheck.Verdict.UNCHECKED:
        _say_it_wasnt_checked(row, check)
    passed = check is None or check.verdict == FixCheck.Verdict.PASSED
    # A fix row's clip is a try the judge passed: the fixing agent keeps no failed one.
    if (
        step.kind == SceneStep.Kind.CLIP
        and step.scene.shows
        and passed
        and row.action != Action.FIX
    ):
        _say_what_the_judge_kept(row, step)
    _carry_on(row)
    _assemble_once_landed(row.plan)
    # Another plan's row may have waited for this step to free the scene.
    carry_on_scene(step.scene, but=row)


def _say_it_wasnt_checked(row: FixPlanScene, check: FixCheck) -> None:
    """A fix check that couldn't be had passes, and the shop owner is told their change in
    the new version wasn't checked (#131): never silent. The producer is told the chat said so."""
    number = row.scene.number
    what = "starting picture" if check.what == "picture" else "clip"
    post_notice(
        row.plan.job,
        f"Scene {number}'s new {what} couldn't be checked for your change, so it is kept "
        "unchecked.",
        Message.Level.INFO,
    )
    _note(
        row.plan,
        SystemNote.Kind.CHECK_UNCHECKED,
        f"Scene {number}'s fix couldn't be checked in its new {check.what}, so it carries on "
        "unchecked. The chat told the shop owner it wasn't checked.",
    )


def _say_what_the_judge_kept(row: FixPlanScene, step: SceneStep) -> None:
    """A fix's clip the clip judge couldn't check is kept, and the shop owner is told so by
    code, never silent (Julian 2026-10-14, "treat that as pass"; design thread, 05:15 Oct 17).
    So is one it failed that the fixing agent couldn't take over. One the agent gave up on is
    shown with every try, to pick from, and the scene keeps the clip it had before. Nothing
    for the producer (decision 20). A fix check that failed or couldn't be had says so
    already."""
    number = row.scene.number
    if work.agent_fix(step) is not None:
        return
    verdict = (work.kept_clip(step).ruling or {}).get("verdict")
    if verdict == "not_checked":
        text = f"Scene {number}'s new clip couldn't be checked, so it is kept unchecked."
    elif verdict == "fail":
        text = f"Scene {number}'s new clip was kept though the judge found a fault."
    else:
        return
    post_notice(row.plan.job, text, Message.Level.INFO)


def step_failed(step: SceneStep) -> None:
    """For a step a plan started that failed or stopped, once marked so: its scene stops, the
    producer is told why, and the other scenes carry on to the new ad."""
    row = step.plan_scene
    assert row is not None, "a plan's step runs one of its rows"
    before_it = work.the_try_before(step)
    if before_it is not None and not row.error:
        # A picture drawn again for its fix check that couldn't be made leaves the try before
        # it as the last: the shop owner is asked about that one, as when no redraw is left.
        work.say_the_fix_check_failed(before_it)
        _note(row.plan, SystemNote.Kind.CHECK_FAILED, _check_failed(row, before_it))
        carry_on_scene(step.scene, but=row)
        return
    _failed(row, f"Scene {row.scene.number}'s {_step_made(step)} couldn't be made: {step.reason}")
    _assemble_once_landed(row.plan)
    carry_on_scene(step.scene, but=row)


def carry_on_scene(scene: Scene, *, but: FixPlanScene | None = None) -> None:
    """After a step of the scene finishes, fails or stops: a plan row waiting for the scene
    to be free carries on, but the row the step was for, which its step carried on itself."""
    with transaction.atomic():
        for row in FixPlanScene.objects.filter(
            scene=scene, started_at__isnull=False, landed_at__isnull=True, error=""
        ):
            if row != but:
                _carry_on(row)
                _assemble_once_landed(row.plan)


def _carry_on(row: FixPlanScene) -> None:
    """Start the row's next step, once its scene is free for it, or mark it landed once its
    scene's new version is made and passed its checks. A scene whose fix check failed waits
    for the shop owner's go ahead."""
    row = FixPlanScene.objects.select_for_update().select_related("scene", "plan").get(pk=row.pk)
    if row.started_at is None or row.landed_at is not None or row.error:
        return
    if (
        FixCheck.objects.filter(
            step__plan_scene=row, verdict=FixCheck.Verdict.FAILED, gone_ahead_at__isnull=True
        )
        .exclude(step__redraws__status__in=DRAWN_AGAIN)
        .exists()
    ):
        return
    scene = Scene.objects.get(pk=row.scene_id)
    running = set(
        scene.steps.filter(status=SceneStep.Status.RUNNING).values_list("kind", flat=True)
    )
    if row.action == Action.FIX:
        _carry_on_fix(row, scene, running)
        return
    if row.action == Action.GO_BACK and row.brought_back is None:
        if running & {SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP}:
            return
        try:
            if not row.remakes_clip and not work.go_back_is_free(scene, row.go_back_to or 0):
                # The shop owner said yes to going back for nothing: a clip they weren't told
                # of isn't paid for.
                _failed(
                    row,
                    f"Scene {scene.number} couldn't go back to version {row.go_back_to} for "
                    "nothing: its line changed since the plan was shown, so its clip would "
                    "have to be made again.",
                )
                return
            gone = work.go_back(scene, row.go_back_to or 0)
        except work.SceneBusy:
            return
        except work.NoSuchVersion as error:
            _failed(row, f"Scene {scene.number} couldn't go back: {error}.")
            return
        row.brought_back = gone.version.picture
        row.save(update_fields=["brought_back"])
        if not gone.clip_to_make:
            _landed(row)
            return
        scene.refresh_from_db()
    if row.line and not row.steps.exists():
        if running:
            return
        if scene.line != row.line:
            # The shop owner read the line and said yes: they know their product, so it
            # isn't fact checked (Julian, 2026-10-11 22:13, #138).
            scene.change_line(row.line)
            scene.save(update_fields=["line", "shortened_from", "status"])
        # Also when the line already says it: the overlay the plan showed is the one drawn.
        if row.overlay and scene.overlay != row.overlay:
            scene.overlay = row.overlay
            scene.save(update_fields=["overlay"])
    picture = _picture(row, scene, running)
    if row.line and not _line_heard(row, scene, running):
        return
    if picture is None:
        return
    clips = row.steps.filter(
        kind=SceneStep.Kind.CLIP,
        status=SceneStep.Status.FINISHED,
        produced__isnull=False,
        **SceneStep.made_for(scene),
    )
    if row.action == Action.NEW_PICTURE:
        clips = clips.filter(picture_step=picture.step)
    if clips.exists():
        _landed(row)
        return
    if running:
        return
    audio = _audio(scene)
    if audio is None or not _heard(audio):
        _failed(row, f"Scene {scene.number}'s clip couldn't be made: its line has no audio.")
        return
    _start(
        row,
        scene,
        SceneStep.Kind.CLIP,
        note=row.change if row.action == Action.NEW_CLIP else "",
        made_from=audio,
        picture=picture,
        picture_step=picture.step,
        motion_prompt=_kept_motion(row, scene, picture),
    )


def _carry_on_fix(row: FixPlanScene, scene: Scene, running: set[str]) -> None:
    """A fix row (door 2): its scene is handed to the fixing agent, on a clip step of its own
    made from the scene as the shop owner sees it, once the scene is free; the row lands once
    a try passed and is the step's clip. A step that gave up or failed is the worker's to
    end the row with (`agent_gave_up`, `step_failed`)."""
    steps = row.steps.filter(kind=SceneStep.Kind.CLIP)
    # The passing try is filmed in a step of the agent's own, so the row's step holds none.
    if steps.filter(status=SceneStep.Status.FINISHED, agent_fix__passed__isnull=False).exists():
        # The overlay the plan showed is drawn once the scene is fixed: a give-up keeps the
        # scene as it was.
        if row.overlay and scene.overlay != row.overlay:
            scene.overlay = row.overlay
            scene.save(update_fields=["overlay"])
        _landed(row)
        return
    if steps.exists() or running:
        return
    picture = _shown_picture(scene)
    if picture is None:
        _failed(row, f"Scene {scene.number} has no starting picture for the fixing agent.")
        return
    audio = _audio(scene)
    if audio is None or not _heard(audio):
        _failed(row, f"Scene {scene.number}'s clip couldn't be made: its line has no audio.")
        return
    _start(
        row,
        scene,
        SceneStep.Kind.CLIP,
        note=row.change,
        made_from=audio,
        picture=picture,
        picture_step=picture.step,
    )


def agent_gave_up(step: SceneStep, reason: str) -> None:
    """The fixing agent gave up on a fix row's scene (door 2, spec section 5), once its step
    is marked failed: the scene's clip from before the plan is its clip again, for nothing,
    the shop owner is told in one line why, and shown every try to pick from (#180), and the
    row ends with the agent's reason. The other scenes carry on to the new ad."""
    row = step.plan_scene
    assert row is not None, "a fix row's step runs one of its rows"
    scene = Scene.objects.get(pk=step.scene_id)
    before = step.shown_before(ProducedItem.Kind.CLIP)
    made = work.versions(scene)
    if before is not None and made and made[-1].clip != before:
        back_to = next((version.number for version in made if version.clip == before), None)
        work.bring_back_clip(before, back_to=back_to)
    reason = " ".join(reason.split())
    tried = bool(work.clip_tries(step))
    post_notice(
        row.plan.job,
        f"Scene {scene.number} stays as it was: {reason}"
        + (" Watch the tries, and tap one to use it instead." if tried else ""),
        Message.Level.INFO,
        tries_of=step.pk if tried else None,
    )
    row.error = reason
    row.save(update_fields=["error"])
    _note(
        row.plan,
        SystemNote.Kind.ERROR,
        f"The fixing agent gave up on scene {scene.number}'s fix: {reason} The scene stays as "
        "it was, and the chat showed the shop owner its tries.",
    )
    _assemble_once_landed(row.plan)
    carry_on_scene(scene, but=row)


def _kept_motion(row: FixPlanScene, scene: Scene, picture: ProducedItem) -> str:
    """For a new picture's or a new line's clip in a scene that shows the product, how the
    clip it replaces moved: only a new clip chooses a new movement (live ad fix 1; design
    thread, 05:15 Oct 17), a new clip made a new picture for its photo too. Blank otherwise,
    so the clip moves as its picture's step planned."""
    if row.action not in (Action.NEW_PICTURE, Action.NEW_LINE) or row.new_movement:
        return ""
    if not scene.shows:
        return ""
    # A new line's clip replaces the newest, of the same picture.
    before = [
        version.clip
        for version in work.versions(scene)
        if version.clip is not None
        and (row.action == Action.NEW_LINE or version.picture != picture)
    ]
    return work.motion_of(before[-1]) if before else ""


def _picture(row: FixPlanScene, scene: Scene, running: set[str]) -> ProducedItem | None:
    """The starting picture the row's clip is made from, or None while it is to make: for a
    new picture, the one its own step made for the scene as it stands, started here when
    there is none; for a new clip, the scene's newest version's; for a new line, the same,
    kept (Julian, 2026-10-17: "unless the user tells us to change the visuals, we don't need
    to change it"), or drawn as a new picture's when the scene has none; for a go back, the
    one brought back."""
    if row.action == Action.GO_BACK:
        assert row.brought_back is not None, "gone back first"
        return row.brought_back
    if row.action in (Action.NEW_CLIP, Action.NEW_LINE):
        picture = _shown_picture(scene)
        if picture is not None:
            return picture
        if row.action == Action.NEW_CLIP:
            _failed(row, f"Scene {scene.number} has no starting picture to make its clip from.")
            return None
    made_for = SceneStep.made_for(scene, through="step__")
    del made_for["step__line"]
    picture = (
        scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE,
            step__plan_scene=row,
            step__status=SceneStep.Status.FINISHED,
            step__line__in=[scene.line, *scene.shortened_from],
            **made_for,
        )
        # Not one drawn again for its fix check: only the try after it can have a clip.
        .exclude(step__redraws__status__in=DRAWN_AGAIN)
        .order_by("version")
        .last()
    )
    if picture is not None:
        return picture
    # A new picture isn't started while the scene's clip is being made: that clip, of the
    # picture before, would finish the scene once the new picture had un-finished it (#133).
    if not running & {SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP}:
        note = row.change if row.action == Action.NEW_PICTURE else ""
        _start(row, scene, SceneStep.Kind.STARTING_PICTURE, note=note)
    return None


def _shown_picture(scene: Scene) -> ProducedItem | None:
    """The starting picture the shop owner sees in the scene: its newest version's, or, for
    a scene whose versions have none, the one made for what it shows as it stands."""
    made = work.versions(scene)
    return (made[-1].picture if made else None) or _current_picture(scene)


def _line_heard(row: FixPlanScene, scene: Scene, running: set[str]) -> bool:
    """Whether the scene's new line has its audio, transcribed: its audio, then its
    transcript, started here while it hasn't."""
    audio = _audio(scene)
    if audio is None:
        if SceneStep.Kind.LINE_AUDIO not in running:
            voice = work.latest(scene.job, ProducedItem.Kind.VOICE)
            _start(row, scene, SceneStep.Kind.LINE_AUDIO, made_from=voice)
        return False
    if not _heard(audio):
        if SceneStep.Kind.TRANSCRIPT not in running:
            _start(row, scene, SceneStep.Kind.TRANSCRIPT, made_from=audio)
        return False
    return True


def _audio(scene: Scene) -> ProducedItem | None:
    """The scene's audio of its line as it stands, in the person's voice as it stands."""
    return (
        scene.produced.filter(
            kind=ProducedItem.Kind.LINE_AUDIO,
            step__status=SceneStep.Status.FINISHED,
            step__line=scene.line,
            made_from=work.latest(scene.job, ProducedItem.Kind.VOICE),
        )
        .order_by("version")
        .last()
    )


def _heard(audio: ProducedItem) -> bool:
    """Whether the audio was transcribed: a clip is only made from audio heard saying the line."""
    return ProducedItem.objects.filter(
        kind=ProducedItem.Kind.TRANSCRIPT,
        step__status=SceneStep.Status.FINISHED,
        made_from=audio,
    ).exists()


def _start(
    row: FixPlanScene,
    scene: Scene,
    kind: SceneStep.Kind,
    *,
    note: str = "",
    made_from: ProducedItem | None = None,
    picture: ProducedItem | None = None,
    picture_step: SceneStep | None = None,
    motion_prompt: str = "",
    redraw_of: SceneStep | None = None,
) -> SceneStep | None:
    """Start a scene step for the row in the background, from the scene as it stands, on its
    own tool call made by code, unless one of its kind started for the scene since. Gives
    the step started, or None when none was."""
    try:
        with transaction.atomic():
            step = SceneStep.objects.create(
                scene=scene,
                kind=kind,
                tool_call=_code_call(row, _TOOLS[kind]),
                **SceneStep.made_for(scene),
                note=note,
                fix=row.fix,
                plan_scene=row,
                made_from=made_from,
                picture=picture,
                picture_step=picture_step,
                motion_prompt=motion_prompt,
                redraw_of=redraw_of,
            )
            # The shop owner's photos go with every later step of their scene (D1).
            if scene.shows:
                step.reference_photos.set(scene.photos_in_use())
            step_signals.say_started(step)
    except IntegrityError:
        return None
    # tasks.py runs this module's steps, so it imports this module rather than the other way.
    from .tasks import run_scene_step

    transaction.on_commit(lambda: run_scene_step.delay(step.pk))
    return step


# The tool each step stands for, as the producer's tools would have started it.
_TOOLS = {
    SceneStep.Kind.STARTING_PICTURE: "make_starting_picture",
    SceneStep.Kind.LINE_AUDIO: "make_line_audio",
    SceneStep.Kind.TRANSCRIPT: "transcribe_line_audio",
    SceneStep.Kind.CLIP: "make_clip",
}


def _code_call(row: FixPlanScene, tool: str) -> ToolCall:
    """A tool call code makes for the row, so each step's model calls are charged to its own
    (work.py reads what a step paid for by its tool call), and none is the producer's."""
    plan = row.plan
    return ToolCall.objects.create(
        session=plan.session,
        job=plan.job,
        agent=FIX_PLAN,
        tool=tool,
        call_id=f"{FIX_PLAN}-{uuid4().hex}",
        arguments={"plan": plan.pk, "scene": row.scene.number, "action": row.action},
        finished_at=timezone.now(),
    )


def _landed(row: FixPlanScene) -> None:
    row.landed_at = timezone.now()
    row.save(update_fields=["landed_at"])
    _note(
        row.plan,
        SystemNote.Kind.SCENE_DONE,
        f"Scene {row.scene.number}'s {_made(row)} is done. It cost {dollars(row.cost_usd())}.",
    )


def _failed(row: FixPlanScene, why: str) -> None:
    """The row couldn't be made: its scene stays as it was before it, when that is free, so
    the new ad is assembled with the scenes that landed."""
    row.error = why
    row.save(update_fields=["error"])
    _note(row.plan, SystemNote.Kind.ERROR, " ".join(filter(None, [why, _as_it_was(row)])))


def _as_it_was(row: FixPlanScene) -> str:
    """Bring back the row's scene's newest version with a clip that still says its line, for
    nothing, when the row left the scene unfinished (a new picture with no clip yet, a picture
    gone back to whose clip couldn't be made): the ad can't be assembled without it. Nothing
    while another row of the scene is still to land, or the scene is being made. Says what
    was done, or blank. A new line that couldn't be finished has no such version: its scene
    waits for the shop owner's next note."""
    scene = Scene.objects.get(pk=row.scene_id)
    if scene.status == Scene.Status.FINISHED:
        return ""
    if (
        FixPlanScene.objects.filter(
            scene=scene, started_at__isnull=False, landed_at__isnull=True, error=""
        )
        .exclude(pk=row.pk)
        .exists()
    ):
        return ""
    made = work.versions(scene)
    back_to = next(
        (
            version.number
            for version in reversed(made)
            if version.clip is not None and work.go_back_is_free(scene, version.number)
        ),
        None,
    )
    if back_to is None:
        return (
            f"Scene {scene.number} has no clip that says its line as it stands, so the new ad "
            "can't be assembled until it has one."
        )
    try:
        work.go_back(scene, back_to)
    except work.SceneBusy, work.NoSuchVersion:
        return ""
    return f"Scene {scene.number} went back to version {back_to}, for nothing."


def _step_made(step: SceneStep) -> str:
    """What a plan's step makes, as "new starting picture"."""
    return {
        SceneStep.Kind.STARTING_PICTURE: "new starting picture",
        SceneStep.Kind.LINE_AUDIO: "new line's audio",
        SceneStep.Kind.TRANSCRIPT: "new line's transcript",
        SceneStep.Kind.CLIP: "new clip",
    }[SceneStep.Kind(step.kind)]


def _check_failed(row: FixPlanScene, check: FixCheck) -> str:
    picture = check.what == "picture"
    assert check.old is not None, "a fix is checked against the version before it"
    new = work.version_label(check.new)
    return (
        f"Scene {row.scene.number}'s {_step_made(check.step)} ({new}) may not have the change, "
        + ("so its clip wasn't made" if picture else "so the new ad waits")
        + f": {check.reason} The chat showed the shop owner {work.version_label(check.old)} "
        f"and {new}, and waits for their answer."
    )


def _assemble_once_landed(plan: FixPlan) -> None:
    """Assemble the new ad and show it, once, when every scene of the plan has landed or
    couldn't be made, and at least one landed."""
    # Two scenes landing at once: the second waits here for the first's transaction.
    Job.objects.select_for_update().get(pk=plan.job_id)
    rows = [row for row in plan.scenes.all() if _doable(row)]
    if any(row.landed_at is None and not row.error for row in rows):
        return
    if not any(row.landed_at for row in rows):
        return
    if plan.system_notes.filter(kind=SystemNote.Kind.AD_SHOWN).exists():
        return
    session = Session.objects.get(pk=plan.session_id)
    try:
        with transaction.atomic():
            ad, _ = assemble_the_ad(session, plan.job, _made_for(rows))
    except Refused as refused:
        _note(plan, SystemNote.Kind.ERROR, f"The new ad couldn't be assembled: {refused}")
        return
    # Assembly failing for a reason nobody expected doesn't fail the step that landed the
    # last scene: its clip is made and paid for.
    except Exception as error:
        logger.exception("Assembling the ad of fix plan %s failed", plan.pk)
        _note(
            plan,
            SystemNote.Kind.ERROR,
            f"The new ad couldn't be assembled: {why_it_failed(error, 'it')}",
        )
        return
    _note(
        plan,
        SystemNote.Kind.AD_SHOWN,
        f"Every scene of the plan is done, so the new ad (version {ad.version}) was assembled "
        "and shown to the shop owner.",
    )


def _made_for(rows: list[FixPlanScene]) -> str:
    """What a plan's ad is made for, as each landed scene's version label: "scene 2 v2 black
    toilet". Two plans' ads, as when a newer plan changes a scene again before the older's
    clip lands, are each shown as their own version (design thread, 03:15 Oct 17)."""
    made = []
    for row in sorted(rows, key=lambda row: row.scene.number):
        clip = _latest_clip(row.scene) if row.landed_at else None
        if clip is not None:
            made.append(f"scene {row.scene.number} {work.version_label(clip)}")
    return ", ".join(made)


def _note(plan: FixPlan, kind: SystemNote.Kind, text: str) -> None:
    SystemNote.objects.create(session_id=plan.session_id, plan=plan, kind=kind, text=text)
