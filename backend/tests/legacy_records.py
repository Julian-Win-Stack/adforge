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


def historical_snapshot(scene: Any) -> dict[str, Any]:
    """Fields the old sender saved, without current scene-agent metadata."""
    return {
        "line": scene.line,
        "shows": scene.shows,
        "portrait": scene.portrait_shown(),
        "broll_kind": scene.broll_kind,
        "person_shown": scene.person_shown,
        "usage": scene.usage,
        "broll_result": scene.result,
    }


def legacy_clip_of_scene_2(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    prepared: bool = False,
    run_step: bool = True,
    scene_number: int = 2,
) -> SceneStep:
    """Seed a historical clip, then run only its retained reader/judge checkpoint.

    The old sender never runs. Queued lifecycle tests use the current scene-agent
    helper instead; run_step=False is retained until those callers are migrated.
    """
    from uuid import uuid4

    from django.utils import timezone

    from agents.models import ToolCall
    from jobs.models import Scene

    from .test_producer_broll import made_ready

    if not prepared:
        made_ready(fake_model, steps, say, (2,))
    scene = Scene.objects.get(number=scene_number)
    if scene.kind == Scene.Kind.TALKING:
        # Before H3, talking scenes had no action or person-shown labels.
        scene.shows = ""
        scene.person_shown = ""
        scene.save(update_fields=["shows", "person_shown"])
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
    audio = scene.produced.filter(kind=ProducedItem.Kind.LINE_AUDIO).last()
    if audio is None:
        audio_step = SceneStep.objects.create(
            scene=scene,
            tool_call=call,
            kind=SceneStep.Kind.LINE_AUDIO,
            status=SceneStep.Status.FINISHED,
            **historical_snapshot(scene),
        )
        audio = scene.produced.create(
            job=scene.job,
            step=audio_step,
            kind=ProducedItem.Kind.LINE_AUDIO,
            seconds=5,
            text=scene.line,
            version=1,
        )
    step = SceneStep.objects.create(
        scene=scene,
        tool_call=call,
        kind=SceneStep.Kind.CLIP,
        **historical_snapshot(scene),
        made_from=audio,
    )
    if run_step:
        clip = seed_legacy_clip(step)
        step.status = SceneStep.Status.FINISHED
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "finished_at"])
        scene.status = Scene.Status.FINISHED
        scene.save(update_fields=["status"])
        transcript_step = SceneStep.objects.create(
            scene=scene,
            tool_call=call,
            kind=SceneStep.Kind.TRANSCRIPT,
            status=SceneStep.Status.FINISHED,
            **historical_snapshot(scene),
        )
        scene.produced.create(
            job=scene.job,
            step=transcript_step,
            kind=ProducedItem.Kind.TRANSCRIPT,
            version=1,
            made_from=audio,
            text=scene.line,
            words=[
                {"text": word, "start": at * 0.4, "end": (at + 1) * 0.4}
                for at, word in enumerate(scene.line.split())
            ],
        )
        from chat import messages
        from chat.models import Attachment, Message

        messages.add(
            scene.job.session,
            role=Message.Role.AGENT,
            carrying=[
                messages.AttachedFile(
                    Attachment.Kind.VIDEO,
                    clip.file,
                    scene.number,
                    work.version_label(clip),
                    clip=clip,
                )
            ],
        )
    else:
        steps.held.append(step.pk)
    step.refresh_from_db()
    return step


def seed_legacy_clip(step: SceneStep) -> ProducedItem:
    """A stored clip and its old sent list, independent of the removed video sender."""
    import math

    from jobs.library import SentList, SentPicture, keep_sent_list, library_of

    from .conftest import a_video, photo
    from .test_producer_broll import sent_prompt

    scene = step.scene
    audio = step.made_from
    assert audio is not None and audio.seconds is not None
    product = library_of(scene.job)[0]
    room = file_store.save(f"legacy/{step.pk}/room.png", photo(2))
    sent = SentList(
        colour=scene.job.product_colour,
        pictures=[
            SentPicture(
                image=1,
                tier=1,
                library_number=product.number,
                drawn=None,
                for_=None,
                label="the product from the front",
                file=product.file,
            ),
            SentPicture(
                image=2,
                tier=3,
                library_number=None,
                drawn="room",
                for_=None,
                label="the room",
                file=room,
            ),
        ],
        pieces=[],
        left_out=[],
        recolours=[],
        prompt=sent_prompt(),
    )
    seconds = max(5, math.ceil(audio.seconds))
    clip = ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.CLIP,
        version=work._next_version(scene, ProducedItem.Kind.CLIP),
        file=file_store.save(
            f"legacy/{step.pk}/clip.mp4", a_video(180, 320, sound=True, seconds=seconds)
        ),
        seconds=seconds,
        made_from=audio,
    )
    keep_sent_list(clip, sent)
    for purpose in ("make_broll_clip", "collect_broll_clip"):
        ModelCall.objects.create(
            session=step.tool_call.session,
            job=scene.job,
            tool_call=step.tool_call,
            purpose=purpose,
            provider="fake",
            model="creatify/boreal-h3",
            attempt=1,
            handoff={
                "prompt": sent.prompt,
                "example_pictures": [p.file for p in sent.pictures],
                "seconds": seconds,
            },
            output={"video_id": f"historical-{step.pk}", "file": clip.file},
            outcome=ModelCall.Outcome.SUCCEEDED,
            cost_usd=Decimal("0"),
            duration_ms=1,
        )
    return clip


def legacy_assemble_through_the_chat(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    """An old-ad fixture: historical B-roll checkpoint, current talking steps and assembly."""
    from .test_producer_broll import calling

    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    for number in (1, 2, 3):
        legacy_clip_of_scene_2(fake_model, steps, say, prepared=True, scene_number=number)
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


def historical_question(session_id: str) -> OwnerQuestion:
    """Stored data from an old worker, without invoking the retired fixing loop."""
    from django.utils import timezone

    from agents.models import ToolCall
    from chat.models import Session
    from jobs.models import AgentFix, AgentFixTry, OwnerQuestion, Scene, SceneStep

    scene = Scene.objects.get(number=2)
    call = ToolCall.objects.create(
        session=Session.objects.get(pk=session_id),
        job=scene.job,
        agent="producer",
        tool="make_clip",
        call_id="historical-scene",
        arguments={"scene": 2},
        finished_at=timezone.now(),
    )
    step = SceneStep.objects.create(
        scene=scene,
        tool_call=call,
        kind="clip",
        status="finished",
        finished_at=timezone.now(),
        **SceneStep.snapshot(scene),
    )
    fixing = AgentFix.objects.create(
        step=step, door="judge", gave_up="Need a missing view.", finished_at=timezone.now()
    )
    asked = ToolCall.objects.create(
        session=call.session,
        job=scene.job,
        agent="fixing_agent",
        agent_fix=fixing,
        tool="ask_owner",
        call_id="historical-question",
        arguments={},
        finished_at=timezone.now(),
    )
    AgentFixTry.objects.create(agent_fix=fixing, number=1, tool_call=asked, scene_row={})
    return OwnerQuestion.objects.create(
        scene=scene,
        agent_fix=fixing,
        tool_call=asked,
        question="Can you send the missing view?",
        wants="photo",
    )
