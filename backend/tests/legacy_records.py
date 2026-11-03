"""Explicit old records for card/export compatibility; no retired agent loop runs here."""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from django.utils import timezone

from adforge import file_store
from agents.models import ToolCall
from gateway.fake import FakeModel
from gateway.models import ModelCall
from jobs import work
from jobs.models import AgentFix, AgentFixTry, Job, OwnerQuestion, ProducedItem, SceneStep

from .conftest import CLIP_FIXED, HeldSteps

STIRS = "A hand stirs the tea in the mug with a spoon for the whole clip."
POURS = "Tea pours from the teapot into the mug for the whole clip, filling it."
NOTE = "In scene 2 the brush is the wrong colour: it should be the green one."
INSTRUCTION = "Film scene 2 again with the green brush held the way the page shows it."
WITH_IT = "Film the brush as the shop owner's photo shows it."
QUESTION = "Could you send a short video of the lid being screwed onto the mug?"
LOOKED = {
    "sees": "A hand holds a red brush beside the mug from 0 s to 4 s.",
    "wrong": "The brush is red, from 0 s; the shop owner wants the green one.",
    "root_cause": "The start picture was drawn with a red brush.",
    "direction": "Draw the start picture again with the green brush from the shop photos.",
}


def fails_the_judge(problem: str) -> dict[str, Any]:
    from .conftest import CLIP_MOTION_OK

    return {
        **CLIP_MOTION_OK,
        "real_motion": {
            "verdict": "fail",
            "problem": problem,
            "back_to_normal_at": "never",
        },
        "owner_change": CLIP_FIXED,
    }


def legacy_clip_of_scene_2(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    prepared: bool = False,
    run_step: bool = True,
) -> SceneStep:
    """An explicit old-ad fixture: seed its historical checkpoint and run the real worker.

    Live producer tools no longer create this record shape. Keeping it here exercises
    existing ads' cards and fixes without reopening that producer door. With run_step=False
    it leaves the historical checkpoint queued for interruption and recovery cases.
    """
    from uuid import uuid4

    from django.utils import timezone

    from agents.models import ToolCall
    from jobs.models import Scene

    from .test_producer_broll import made_ready, run

    if not prepared:
        made_ready(fake_model, steps, say, (2,))
    scene = Scene.objects.get(number=2)
    assert scene.job.session is not None
    call = ToolCall.objects.create(
        session=scene.job.session,
        job=scene.job,
        agent="producer",
        tool="make_clip",
        call_id=f"historical-clip-{uuid4()}",
        arguments={"scene": 2},
        finished_at=timezone.now(),
    )
    step = SceneStep.objects.create(
        scene=scene,
        tool_call=call,
        kind=SceneStep.Kind.CLIP,
        **SceneStep.snapshot(scene),
        made_from=scene.produced.filter(kind=ProducedItem.Kind.LINE_AUDIO).latest(
            "created_at", "pk"
        ),
    )
    steps.held.append(step.pk)
    if run_step:
        run(fake_model, steps)
    step.refresh_from_db()
    return step


def legacy_assemble_through_the_chat(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    """An old-ad fixture: historical B-roll checkpoint, current talking steps and assembly."""
    from .test_producer_broll import calling, made_ready

    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, ("make_clip", {"scene": 1}), ("make_clip", {"scene": 3}))
    legacy_clip_of_scene_2(fake_model, steps, say, prepared=True)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def seed_legacy_fix(
    step: SceneStep,
    *,
    tries: int = 1,
    prompts: Sequence[str] = (),
    rulings: Sequence[dict[str, Any]] = (),
    first_ruling: dict[str, Any] | None = None,
    reason: str = "Recorded legacy fix ended.",
    picked: int | None = None,
    finished: bool = True,
    door: str = "judge",
    note: str = "",
    goal: str = "",
    question: str | None = None,
) -> AgentFix:
    """Seed a historical fix and its tries; only reader/card behavior is under test.

    The old sender checkpoint is created separately. Old tool/model rows are data, not
    calls to the removed film_again tool or the old agent's loop.
    """
    first = step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("pk").first()
    assert first is not None and step.scene.job.session is not None
    if first_ruling is not None:
        from chat.models import Attachment

        # Historical failed first clips were offered with their tries, never separately.
        Attachment.objects.filter(clip=first).delete()
        first.ruling = first_ruling
        first.save(update_fields=["ruling"])
    scene = step.scene
    row = {
        name: getattr(scene, name)
        for name in (
            "line",
            "shows",
            "usage",
            "result",
            "broll_kind",
            "person_shown",
        )
    }
    snapshot = {
        **row,
        **{
            name: getattr(scene, name)
            for name in (
                "status",
                "fact_checked",
                "fact_problems",
                "shortened_from",
            )
        },
    }
    fixing = AgentFix.objects.create(
        step=step,
        door=door,
        first_clip=first,
        ruling=first.ruling,
        note=note,
        goal=goal,
        scene_before=snapshot,
    )
    last = first
    passed = False
    for number in range(1, tries + 1):
        prompt = prompts[number - 1] if number <= len(prompts) else STIRS
        ruling = (
            rulings[number - 1]
            if number <= len(rulings)
            else {
                "verdict": "fail",
                "checks": {},
                "answers": {},
                "failed_answers": 1,
                "failures": [{"check": "motion", "problem": "The recorded try still failed."}],
            }
        )
        passed = ruling.get("verdict") == "pass"
        call = ToolCall.objects.create(
            session=step.tool_call.session,
            job=scene.job,
            agent="fixing_agent",
            agent_fix=fixing,
            tool="film_again",
            call_id=f"legacy-{fixing.pk}-{number}",
            arguments={"video_prompt": prompt},
            answer="made",
            result="Recorded legacy try.",
            finished_at=timezone.now(),
        )
        tried = SceneStep.objects.create(
            scene=scene,
            tool_call=call,
            kind=SceneStep.Kind.CLIP,
            status=SceneStep.Status.FINISHED,
            **SceneStep.snapshot(scene),
            made_from=first.made_from,
            picture=first.picture,
            motion_prompt=prompt,
            finished_at=timezone.now(),
        )
        last = ProducedItem.objects.create(
            job=scene.job,
            scene=scene,
            step=tried,
            kind=ProducedItem.Kind.CLIP,
            version=work._next_version(scene, ProducedItem.Kind.CLIP),
            file=file_store.save(
                f"legacy/{fixing.pk}/try-{number}.mp4", file_store.read(first.file)
            ),
            seconds=first.seconds,
            made_from=first.made_from,
            picture=first.picture,
            remake=number,
            change=f"try {number}",
            ruling=ruling,
            sent=first.sent,
        )
        AgentFixTry.objects.create(
            agent_fix=fixing,
            number=number,
            tool_call=call,
            step=tried,
            picture=first.picture,
            clip=last,
            scene_row=row,
            video_prompt=prompt,
            passed=passed,
            sent=first.sent,
        )
        # Old fixtures recorded nested calls against their parent clip checkpoint.
        for purpose in ("make_broll_clip", "collect_clip", "check_clip_motion"):
            ModelCall.objects.create(
                session=step.tool_call.session,
                job=scene.job,
                tool_call=step.tool_call,
                purpose=purpose,
                provider="fake",
                model="historical-model",
                attempt=1,
                handoff={"prompt": prompt},
                output=ruling if purpose == "check_clip_motion" else {},
                outcome=ModelCall.Outcome.SUCCEEDED,
                cost_usd=Decimal("0"),
                duration_ms=1,
            )
    fixing.picked = picked
    fixing.kept = last
    if finished:
        fixing.finished_at = timezone.now()
        if passed:
            fixing.passed = last
        else:
            fixing.gave_up = reason
    fixing.save()
    if finished:
        step.result = (
            "Background step finished:"
            + work.what_the_judge_did(step, last)
            + f" Scene {scene.number} is finished."
        )
        step.producer_read_at = None
        step.save(update_fields=["result", "producer_read_at"])
        if not passed:
            work.say_the_agent_gave_up(step)
    if question is not None:
        ask = ToolCall.objects.create(
            session=step.tool_call.session,
            job=scene.job,
            agent="fixing_agent",
            agent_fix=fixing,
            tool="ask_owner",
            call_id=f"legacy-question-{fixing.pk}",
            arguments={"question": question, "wants": "video"},
            result="The recorded owner question.",
            answer="made",
            finished_at=timezone.now(),
        )
        asked = OwnerQuestion.objects.create(
            scene=scene,
            agent_fix=fixing,
            tool_call=ask,
            question=question,
            wants="video",
        )
        work.say_the_agent_asked(asked)
    return fixing
