import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from adforge import tracing
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.gateway import charged_to
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, UnusableReply
from jobs import owner_parts
from jobs.models import FixCheck, Job, ProducedItem, Scene, SceneStep
from jobs.notices import post_notice
from jobs.picture_check import PictureKeptFailing
from jobs.work import (
    AgentGaveUp,
    agent_fix,
    agent_gave_up,
    check_fix,
    clip_tries,
    end_the_agents_fixing,
    make_clip,
    make_line_audio,
    make_starting_picture,
    say_the_agent_gave_up,
    say_the_fix_check_failed,
    say_the_pictures_kept_failing,
    too_long_for_a_clip,
    transcribe_line_audio,
    version_label,
    what_the_judge_did,
)

from . import loop, page_panel, routing, run_plan, step_signals
from .loop import BLOCKED, EXPECTED_FAILURES, RunStopped, why_it_failed, why_it_failed_plainly
from .money import dollars
from .producer import PRODUCER, STEP_NAMES

logger = logging.getLogger(__name__)


# Never handed out again after a crash: Celery can't be relied on to, so a producer that
# died is noticed by its heartbeat stopping, and started again (see wake_producer).
@shared_task(acks_late=False)
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies, once code has routed the shop
    owner's messages: after the ad's first clip each goes to the decider first, and only
    those it passes on, or none, leave the producer something to answer. If it can't carry
    on, the chat is told why."""
    session = Session.objects.get(pk=session_id)
    try_again = " Send a message to try again."
    beating = _beating(
        settings.PRODUCER_HEARTBEAT_SECONDS,
        lambda: Session.objects.filter(pk=session_id).update(producer_seen_at=timezone.now()),
        f"The producer in session {session_id}",
    )
    try:
        with beating:
            # The producer only stops once it has read everything the user sent and every
            # scene step's result: a message that arrives as it replies is an interrupt, and
            # it works on that too.
            while not _stop_unless_theres_more_to_read(session_id, _work_on(session)):
                pass
    except BlockedBySafetyFilter:
        why = BLOCKED
        try_again = ""
    except RunStopped as error:
        why = str(error)
    except UnusableReply as error:
        why = f"my AI model's answer couldn't be used ({error})"
    except OutsideServiceDown as error:
        why = f"the AI service stayed down after several tries ({error})"
    except Exception:
        logger.exception("The producer stopped in session %s", session_id)
        why = "an unexpected error happened, and the details are in the server log"
    else:
        return
    finally:
        tracing.flush()
    # No producer is working any more, so the next message starts one.
    with transaction.atomic():
        messages.add(
            session,
            role=Message.Role.AGENT,
            text=f"I had to stop: {why}.{try_again}",
        )
        Session.objects.filter(pk=session_id).update(producer_running=False)


def _work_on(session: Session) -> int:
    """Keep any page the shop owner pasted in the page panel, and route what they sent, then
    let the producer work if it has something to answer, or a scene step's result or a system
    note to read.
    Gives the number of the last message read."""
    page_panel.take_pastes(session)
    if (
        routing.route(session)
        or loop.unread_steps(PRODUCER, session).exists()
        or loop.unread_notes(PRODUCER, session).exists()
    ):
        return loop.run(PRODUCER, session, woken_by=_woken_by(session))
    return routing.last_seq(session)


def _woken_by(session: Session) -> list[str]:
    """The scene steps that finished since the producer last read them, by name."""
    unread = loop.unread_steps(PRODUCER, session).select_related("scene")
    return [_step_name(step) for step in unread.order_by("finished_at", "id")]


def _stop_unless_theres_more_to_read(session_id: str, read_up_to: int) -> bool:
    """Mark the producer stopped, unless the user said something after the messages up to
    `read_up_to` it was last given or that code hasn't routed yet, sent a pasted page code
    hasn't looked at, or a scene step finished or a system note came that it hasn't been
    given. Both
    happen under the session's lock, which a message sent and a step finished take before
    they look for a producer, so each is either found here or starts a new producer itself."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.messages.filter(role=Message.Role.USER, seq__gt=read_up_to).exists():
            return False
        if routing.unrouted_messages(session).exists():
            return False
        if page_panel.unlooked_at(session).exists():
            return False
        if loop.unread_steps(PRODUCER, session).exists():
            return False
        # Such as the note of a pause pressed while it worked.
        if loop.unread_notes(PRODUCER, session).exists():
            return False
        session.producer_running = False
        session.save(update_fields=["producer_running"])
        return True


@contextmanager
def _beating(every_seconds: float, beat: Callable[[], object], what: str) -> Iterator[None]:
    """Say something is still alive by calling `beat` every `every_seconds`, for as long as
    the block runs. Beats on its own thread, so on its own database connection, which it
    closes."""
    stop = threading.Event()

    def beating() -> None:
        try:
            while not stop.wait(every_seconds):
                # One beat that fails mustn't stop the rest: what stops beating is taken for
                # dead while it still works.
                try:
                    beat()
                except Exception:
                    logger.exception("%s's heartbeat failed", what)
                    connection.close()
        finally:
            connection.close()

    thread = threading.Thread(target=beating, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def wake_producer(session_id: str) -> None:
    """Start the producer in the session, unless one is already working there. A working
    producer reads what the user sent when its model call returns, so it isn't started
    twice. One that hasn't been seen for too long has died, and is started again."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        dead_after = timedelta(seconds=settings.PRODUCER_DEAD_AFTER_SECONDS)
        seen = session.producer_seen_at
        if session.producer_running and seen is not None and seen > timezone.now() - dead_after:
            return
        session.producer_running = True
        session.producer_seen_at = timezone.now()
        session.save(update_fields=["producer_running", "producer_seen_at"])
        transaction.on_commit(lambda: run_producer.delay(session_id))


# Which Postgres advisory lock stands for "one session works at a time". Any number will
# do, as long as nothing else takes the same one.
ONE_AT_A_TIME_LOCK = 1


def hold_one_at_a_time_lock() -> None:
    """Wait for, then hold until the transaction ends, the lock every message sent takes
    before it looks for another busy session. Two messages sent to two sessions at the same
    moment then look one after the other, so they can't both start a producer."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ONE_AT_A_TIME_LOCK])


def another_session_is_busy(session: Session) -> bool:
    """Whether a session other than `session` is working, so `session` mustn't start.
    A producer marked running counts however long ago it was seen: one that died is
    always started again, so it works or soon will. Nothing starts a scene step again, so a
    running step only counts while its heartbeat is recent."""
    others = Session.objects.exclude(pk=session.pk)
    if others.filter(producer_running=True).exists():
        return True
    alive_since = timezone.now() - timedelta(seconds=settings.STEP_DEAD_AFTER_SECONDS)
    return (
        SceneStep.objects.filter(status=SceneStep.Status.RUNNING, seen_at__gt=alive_since)
        .exclude(tool_call__session=session)
        .exists()
    )


def _picture_finished(step: SceneStep, picture: ProducedItem | None) -> str:
    assert step.photo is not None, "a starting picture is made from a photo"
    assert picture is not None, "a starting picture step makes its picture"
    number = step.scene.number
    # A finished scene is planned again here, in the transaction that finishes the step and
    # tells the producer, so a worker that stops first leaves the scene with its old clip.
    # Its clip is made by code, never by the producer (decision 2).
    unfinished = (
        f"Scene {number} is no longer finished. "
        if Scene.objects.get(pk=step.scene_id).plan_again()
        else ""
    )
    return (
        f"Background step finished: scene {number}'s starting picture is ready "
        f"(version {picture.version}), and is shown to the shop owner in the chat. "
        f"Photo {step.photo.position} was used: "
        f"{step.photo_reason}{_cost(step)} {unfinished}{_needs_a_yes(step)}{_told_it_waits(step)}"
        "Tell the shop owner."
    )


def _told_it_waits(step: SceneStep) -> str:
    """What the producer is told when the chat has just told the shop owner which scenes wait
    for their yes, as when this picture was the last thing being made."""
    return "".join(
        f"The chat told the shop owner that scene {scene.number} waits for their yes on "
        f"the picture of {part.what}, so the ad can't be assembled yet: don't tell them "
        "again. "
        for scene, part in say_what_waits_on_the_shop_owner(step.scene.job, step)
    )


def _needs_a_yes(step: SceneStep) -> str:
    """What the producer is told about a starting picture of an owner part, or blank for a
    picture of none: the shop owner's yes is on the part's own picture, never on this one. A
    part they already said yes to is settled: they aren't asked about it again."""
    if step.owner_part is None:
        return ""
    part = owner_parts.OwnerPart.model_validate(step.owner_part)
    if owner_parts.is_approved(step.scene):
        return (
            f"The picture shows {part.what}, which the shop owner already said yes to: don't "
            "ask them about it again. "
        )
    return (
        f"The picture shows {part.what}, which no product photo shows. The clip waits for the "
        f"shop owner's yes on the picture of {part.what} itself, shown to them in the chat, "
        "not on this one: don't ask them about this picture. "
    )


def _cost(step: SceneStep) -> str:
    """What the step's work cost, the fixing agent's work on it included, as a sentence to
    follow another."""
    return f" It cost {dollars(step.cost_usd())}."


def _audio_finished(step: SceneStep, audio: ProducedItem | None) -> str:
    assert audio is not None, "a line's audio step makes its audio"
    number = step.scene.number
    # A B-roll line too long for any clip was shortened, or its scene is now said to camera:
    # see make_line_audio. The shop owner isn't told: the chat says so if the scene changed.
    scene = Scene.objects.get(pk=step.scene_id)
    too_long = (
        f"Background step finished: scene {number}'s line's audio takes {audio.seconds:g} "
        f"seconds to say, too long for any clip.{_cost(step)} "
    )
    if too_long_for_a_clip(step, audio) and not scene.shows:
        return (
            f"{too_long}Scene {number} is now a talking scene. Make its starting picture "
            "again, then its audio and its clip."
        )
    if too_long_for_a_clip(step, audio) and step.line in scene.shortened_from:
        # A line the shop owner said yes to in a fix was theirs, so they're told it changed.
        tell_owner = (
            f'It isn\'t the line the shop owner said yes to: it is now "{scene.line}" Tell the '
            "shop owner. "
            if step.fix is not None
            else ""
        )
        return (
            f"{too_long}Scene {number}'s line was shortened to fit its clip. {tell_owner}Make its "
            "audio again."
        )
    if too_long_for_a_clip(step, audio) and step.line == scene.line:
        # Only when the scene changed while its audio was made, so it wasn't said to camera.
        return (
            f"{too_long}Its line couldn't be shortened to fit, and scene {number} shows the "
            "product, so it isn't said to camera. Ask the shop owner for a shorter line of "
            "their own."
        )
    return (
        f"Background step finished: scene {step.scene.number}'s line's audio is ready "
        f"(version {audio.version}, {audio.seconds:g} seconds).{_cost(step)} It isn't shown "
        "to the shop owner. Transcribe it next. Tell the shop owner."
    )


def _transcript_finished(step: SceneStep, transcript: ProducedItem | None) -> str:
    assert transcript is not None and transcript.made_from is not None, (
        "a transcript is made from audio"
    )
    return (
        f"Background step finished: scene {step.scene.number}'s audio (version "
        f"{transcript.made_from.version}) was transcribed (version {transcript.version}). It "
        f'was heard as: "{transcript.text}"{_cost(step)} Tell the shop owner.'
    )


def _clip_finished(step: SceneStep, clip: ProducedItem | None) -> str:
    assert clip is not None and clip.made_from is not None, "a clip is made from its audio"
    number = step.scene.number
    ended = agent_fix(step)
    # Only a give-up from before 2026-10-23, run again, is said to camera: one since keeps
    # its last try, and its scene is finished as below.
    if ended is not None and ended.said_to_camera:
        return (
            f"Background step finished: scene {number}'s clip failed the clip judge, and the "
            f"fixing agent gave up on it after {len(clip_tries(step)) - 1} tries, saying: "
            f'"{ended.gave_up}"{_cost(step)} Scene {number} is now a talking scene. Make its '
            "starting picture again, then its audio and its clip. The chat showed the shop "
            "owner every try, so they can tap one to use it instead, for nothing. Tell the "
            "shop owner."
        )
    # Finished here, in the transaction that finishes the step, never as its first clip is kept:
    # a step that stops before then leaves its scene unfinished, as a picture's does.
    Scene.objects.filter(pk=step.scene_id).update(status=Scene.Status.FINISHED)
    # A B-roll clip made before every B-roll scene got a picture may have none.
    picture = f"starting picture version {clip.picture.version} and " if clip.picture else ""
    note = f', with its movement rewritten from the note "{step.note}"' if step.note else ""
    fixed = (
        f" Scene {number}'s fix has cost {dollars(step.fix.cost_usd())} so far."
        if step.fix is not None
        else ""
    )
    waiting = "".join(
        f" The chat told the shop owner that scene {scene.number} waits for their yes on "
        f"{part.what} in its starting picture, so the ad can't be assembled yet: don't tell "
        "them again."
        for scene, part in say_what_waits_on_the_shop_owner(step.scene.job, step)
    )
    return (
        f"Background step finished: scene {number}'s clip is ready (version {clip.version}, "
        f"{clip.seconds:g} seconds), made from {picture}audio version "
        f"{clip.made_from.version}{note}.{_cost(step)}{what_the_judge_did(step, clip)} "
        f"Scene {number} is finished.{fixed}{waiting} "
        "Tell the shop owner."
    )


def say_what_waits_on_the_shop_owner(
    job: Job, finishing: SceneStep | None = None
) -> list[tuple[Scene, owner_parts.OwnerPart]]:
    """Once every scene is finished but those waiting for the shop owner's yes on their owner
    part, tell the shop owner in the chat which scenes wait and why, once each, ever. Gives
    the scenes told of now, each with its part. `finishing` is the step whose end is being
    told: it counts as done."""
    running = SceneStep.objects.filter(status=SceneStep.Status.RUNNING)
    if finishing is not None:
        running = running.exclude(pk=finishing.pk)
    scenes = list(job.scenes.order_by("number"))
    waiting = [
        (scene, part)
        for scene in scenes
        if scene.status != Scene.Status.FINISHED
        and (part := owner_parts.owner_part(scene)) is not None
        and not owner_parts.is_approved(scene)
        and owner_parts.picture_is_current(scene)
        and not running.filter(scene=scene).exists()
    ]
    if len(waiting) + sum(scene.status == Scene.Status.FINISHED for scene in scenes) != len(scenes):
        return []
    # Read again: a notice posted since the job was read is in the stored warnings only.
    told = {warning["text"] for warning in Job.objects.get(pk=job.pk).warnings}
    newly = [(scene, part) for scene, part in waiting if _waits(scene, part) not in told]
    for scene, part in newly:
        post_notice(job, _waits(scene, part), Message.Level.INFO)
    return newly


def _waits(scene: Scene, part: owner_parts.OwnerPart) -> str:
    """What the shop owner is told when the rest of the ad is made and `scene` waits."""
    drawn = owner_parts.drawn_by(part)
    return (
        f"Scene {scene.number} waits for your yes on the picture of {part.what}: no photo "
        f"showed it, so it was drawn {drawn}. Say yes if it looks right, or tell us what "
        "to change, or send a photo of it. Every scene that isn't waiting on you is made, and "
        "the ad is put together once this scene's clip is made."
    )


@dataclass(frozen=True)
class StepWork:
    """What one kind of scene step does: the work itself, what the producer is told when it
    finishes, and what the chat shows then."""

    # Gives what it made.
    make: Callable[[SceneStep], ProducedItem | None]
    finished: Callable[[SceneStep, ProducedItem | None], str]
    shown: Callable[[SceneStep, ProducedItem | None], list[messages.AttachedFile]]


STEP_WORK = {
    SceneStep.Kind.STARTING_PICTURE: StepWork(
        make=make_starting_picture,
        finished=_picture_finished,
        shown=lambda step, picture: (
            [
                messages.AttachedFile(
                    Attachment.Kind.PICTURE, picture.file, step.scene.number, version_label(picture)
                )
            ]
            if picture
            else []
        ),
    ),
    # The line's audio and its transcript are the producer's to judge, not the shop owner's.
    SceneStep.Kind.LINE_AUDIO: StepWork(
        make=make_line_audio,
        finished=_audio_finished,
        shown=lambda _step, _audio: [],
    ),
    SceneStep.Kind.TRANSCRIPT: StepWork(
        make=transcribe_line_audio,
        finished=_transcript_finished,
        shown=lambda _step, _transcript: [],
    ),
    # Each clip is shown with its scene number as it is made, so the shop owner sees each
    # scene on its own before the whole ad is put together from them.
    # One the fixing agent gave up on shows every try instead, in its own notice.
    SceneStep.Kind.CLIP: StepWork(
        make=make_clip,
        finished=_clip_finished,
        shown=lambda step, clip: (
            [
                messages.AttachedFile(
                    Attachment.Kind.VIDEO,
                    clip.file,
                    step.scene.number,
                    version_label(clip),
                    clip=clip,
                )
            ]
            if clip and not agent_gave_up(step)
            else []
        ),
    ),
}


def _step_name(step: SceneStep) -> str:
    """Such as "scene 1's starting picture"."""
    return f"scene {step.scene.number}'s {STEP_NAMES[SceneStep.Kind(step.kind)]}"


@shared_task
def run_scene_step(step_id: int) -> None:
    """Do a scene step's work in the background, then have the producer told: it reads the
    result on its next turn, and is started if it isn't working."""
    step = SceneStep.objects.select_related("scene__job", "tool_call__session").get(pk=step_id)
    # Celery may hand a step out again after a crash. One that has finished or failed since
    # is left as it is, so nothing is shown or paid for twice; so is one the shop owner's
    # pause cancelled before it was taken.
    if step.status != SceneStep.Status.RUNNING:
        return
    # Taken: from now on its heartbeat says whether it is alive (see fail_dead_steps). Only
    # while it is still running, in one statement, so a pause cancelling it at the same
    # moment either cancels it first, and it is left, or finds it taken, and keeps it.
    now = timezone.now()
    if not SceneStep.objects.filter(pk=step_id, status=SceneStep.Status.RUNNING).update(
        taken_at=now, seen_at=now
    ):
        return
    step.taken_at = now
    session = step.tool_call.session
    work = STEP_WORK[SceneStep.Kind(step.kind)]
    try:
        # Its trace part is open until the step is finished, so it shows what was handed back.
        with tracing.scene_step(
            kind=step.kind,
            scene=step.scene.number,
            session_id=str(session.pk),
            trace_id=step.tool_call.trace_id,
            parent_id=step.tool_call.observation_id,
        ) as traced:
            # Before the work, so a step that fails still shows what it was asked.
            _asked(traced, step, prompt="")
            with (
                _beating(
                    settings.STEP_HEARTBEAT_SECONDS,
                    lambda: SceneStep.objects.filter(pk=step_id).update(seen_at=timezone.now()),
                    f"Scene step {step_id}",
                ),
                charged_to(step.tool_call),
            ):
                made = work.make(step)
                checked = check_fix(step, made)
            # Again once the work has written the prompt its model was given.
            _asked(traced, step, prompt=_prompt_given(step))
            _finish(step, work, made, checked, session, traced)
        tracing.flush()
    except AgentGaveUp as gave_up:
        # The fixing agent gave up on a fix plan's fix row: code says so in the chat and keeps
        # the scene as it was, in the same transaction, so nothing is said twice.
        tracing.flush()
        with transaction.atomic():
            Job.objects.select_for_update().get(pk=step.scene.job_id)
            if not _still_running(step):
                return
            step.status = SceneStep.Status.FAILED
            step.reason = gave_up.reason
            step.finished_at = timezone.now()
            step.save(update_fields=["status", "reason", "finished_at"])
            run_plan.agent_gave_up(step, gave_up.reason)
    except PictureKeptFailing as error:
        # It stops the whole run. The chat is shown why in the same transaction, so a step
        # run again after a crash never shows it twice.
        tracing.flush()
        with transaction.atomic():
            if not _still_running(step):
                return
            say_the_pictures_kept_failing(step.scene.job, error)
            _stop(step, str(error))
            _told_it_ended(step)
    # Whatever stops the step, it isn't left running: the producer is told why.
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("Scene step %s failed", step_id)
        tracing.flush()
        with transaction.atomic():
            if not _still_running(step):
                return
            _fail(step, why_it_failed(error, "the step"), why_it_failed_plainly(error))
            _told_it_ended(step)


def _asked(traced: tracing.Traced, step: SceneStep, *, prompt: str) -> None:
    """Record on a scene step's trace what it was asked, with the prompt its model was given."""
    tracing.step_asked(
        traced,
        kind=step.kind,
        scene=step.scene.number,
        line=step.line,
        note=step.note,
        prompt=prompt,
    )


def _prompt_given(step: SceneStep) -> str:
    """The prompt the scene step's work last sent a model, word for word, as its record keeps
    it: such as a starting picture's or a clip's. Blank for a step whose models are sent
    none, as a line's audio."""
    sent = (
        ModelCall.objects.filter(
            tool_call=step.tool_call,
            created_at__gte=step.taken_at,
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__has_key="prompt",
        )
        .values_list("handoff", flat=True)
        .last()
    )
    return str(sent["prompt"]) if isinstance(sent, dict) else ""


def _finish(
    step: SceneStep,
    work: StepWork,
    made: ProducedItem | None,
    checked: FixCheck | None,
    session: Session,
    traced: tracing.Traced,
) -> None:
    """Show what a scene step made and finish it, then tell whoever started it."""
    # Shown and finished together, so a step run again never shows anything twice. The
    # producer is woken in the same transaction, so its session never looks idle
    # between the two, when another session could start.
    with transaction.atomic():
        # The job, then the chat, as every writer of a plan locks them: a notice said
        # here locks the job after the chat's message, so the job is locked first.
        Job.objects.select_for_update().get(pk=step.scene.job_id)
        # Failed as dead while it worked, as when its heartbeat stopped for a while: the
        # producer was told then, and its scene may be made again, so it is left failed.
        if not _still_running(step):
            return
        shown = work.shown(step, made)
        if shown:
            messages.add(
                session,
                role=Message.Role.AGENT,
                carrying=shown,
                step=step_signals.signal(step, Message.StepState.MADE),
            )
        # A fix plan's step is shown its failed check by code, once it isn't drawn again.
        if (
            checked is not None
            and checked.verdict == FixCheck.Verdict.FAILED
            and step.plan_scene_id is None
        ):
            say_the_fix_check_failed(checked)
        step.status = SceneStep.Status.FINISHED
        # Inside the transaction: a picture's message plans its scene again.
        step.result = work.finished(step, made)
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "result", "finished_at"])
        tracing.handed_back(traced, step.result)
        # Once its step is finished, so its tries are versions.
        if step.kind == SceneStep.Kind.CLIP and step.shows and agent_gave_up(step):
            say_the_agent_gave_up(step)
        # A fix plan's step is carried on by code, and never wakes the producer: a
        # fix's picture that passed its checks queues its clip here (decision 2).
        if step.plan_scene_id is not None:
            run_plan.step_finished(step, checked)
            return
        wake_producer(str(session.pk))
        # A fix plan's scene that waited for this one's step to finish carries on.
        run_plan.carry_on_scene(step.scene)


def _still_running(step: SceneStep) -> bool:
    """Whether the step is still running, its row locked until the transaction ends, so it
    can't be failed as dead while it finishes, nor finished once it was."""
    return (
        SceneStep.objects.select_for_update()
        .filter(pk=step.pk, status=SceneStep.Status.RUNNING)
        .exists()
    )


def _told_it_ended(step: SceneStep) -> None:
    """Tell whoever started a step that failed or stopped: the producer, woken to read it, or,
    for a fix plan's step, code, which tells the producer with a system note."""
    if step.plan_scene_id is not None:
        run_plan.step_failed(step)
    else:
        wake_producer(str(step.tool_call.session_id))
        # A fix plan's scene that waited for this one's step to end carries on.
        run_plan.carry_on_scene(step.scene)


def _fail(step: SceneStep, reason: str, plainly: str) -> None:
    """Mark the step failed for `reason`, word what the producer is told, and tell the chat
    nothing was made and why, `plainly`."""
    step.status = SceneStep.Status.FAILED
    step.reason = reason
    step.result = (
        f"Background step failed: {_step_name(step)} couldn't be made: {reason} "
        "Tell the shop owner what went wrong."
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "result", "finished_at"])
    step_signals.say_not_made(step, plainly)


def _stop(step: SceneStep, reason: str) -> None:
    """Mark the step stopped for `reason`, stopping the run with it: the producer takes no
    turn until the user speaks again (see loop.RunStopped)."""
    step.status = SceneStep.Status.STOPPED
    step.reason = reason
    step.result = (
        f"Background step stopped the run: {_step_name(step)} couldn't be made: {reason}. "
        "The chat showed the shop owner why."
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "result", "finished_at"])


@shared_task
def restart_dead_producers() -> None:
    """Start the producer again in every session where it died, so the session carries on
    without the user having to send anything. Run every minute."""
    for session_id in Session.objects.filter(producer_running=True).values_list("pk", flat=True):
        wake_producer(str(session_id))


def _end_stopped(
    steps: Iterable[SceneStep], reason: str, tell: Callable[[SceneStep], None] | None = None
) -> list[SceneStep]:
    """Fail each of `steps` whose work stopped for `reason`, as the fixing agent's give-up
    leaves its scene, tell the chat, and `tell` whoever started it, together. Each on its
    own: one whose fixing can't be ended is still failed, with a red notice, so it never
    stays running; the rest are ended either way. Gives the steps failed."""
    stopped = []
    for step in steps:
        try:
            with transaction.atomic():
                if not _still_running(step):
                    continue
                # Before the step fails, so its scene is as the fixing agent's give-up
                # leaves it.
                end_the_agents_fixing(step)
                _fail(step, reason, "it was stopped partway.")
                if tell is not None:
                    tell(step)
        except Exception:
            logger.exception("Scene step %s couldn't be ended", step.pk)
            try:
                with transaction.atomic():
                    if not _still_running(step):
                        continue
                    _fail(step, reason, "it was stopped partway.")
                    post_notice(
                        step.scene.job,
                        f"{_step_name(step).capitalize()} stopped partway, and the fixing "
                        "agent's tries on it couldn't be wrapped up. Make it again.",
                        Message.Level.PROBLEM,
                    )
                    if tell is not None:
                        tell(step)
            except Exception:
                logger.exception("Scene step %s couldn't be failed either", step.pk)
                continue
        stopped.append(step)
    return stopped


@shared_task
def fail_dead_steps() -> None:
    """Fail every scene step whose worker was killed: one taken, whose heartbeat hasn't been
    seen for STEP_FAILED_AFTER_SECONDS. Nothing else ever ends it, and while it runs its
    scene can't be made again (round 6). Its producer is told, as for any failed step, in
    the same transaction. Run every minute."""
    dead_since = timezone.now() - timedelta(seconds=settings.STEP_FAILED_AFTER_SECONDS)
    dead = SceneStep.objects.filter(
        status=SceneStep.Status.RUNNING, taken_at__isnull=False, seen_at__lt=dead_since
    ).select_related("scene", "tool_call")
    _end_stopped(
        dead, "Its work stopped partway, and it was never heard from again.", _told_it_ended
    )


def carry_on_after_the_worker_starts() -> None:
    """Deal with whatever the worker was doing when it stopped. There is one worker, so as
    it starts nothing from before can still be running. Each scene step still marked running
    is failed, and its producer told: running it again could run it twice at once, as
    Celery may hand it out again too. Each producer that should be working is started again
    straight away rather than once its heartbeat has been missed, unless another session
    started while the worker was down."""
    with transaction.atomic():
        hold_one_at_a_time_lock()
        sessions = set(Session.objects.filter(producer_running=True).values_list("pk", flat=True))
        stopped = _end_stopped(
            SceneStep.objects.filter(status=SceneStep.Status.RUNNING).select_related(
                "scene", "tool_call"
            ),
            "The worker restarted while it was being made.",
        )
        # Every step is failed first, so a fix plan's scene isn't carried on while another
        # of its steps still looks running. Each on its own, so one that can't carry on
        # leaves the rest failed and told.
        for step in stopped:
            try:
                with transaction.atomic():
                    if step.plan_scene_id is not None:
                        run_plan.step_failed(step)
                    else:
                        sessions.add(step.tool_call.session_id)
                        run_plan.carry_on_scene(step.scene)
            except Exception:
                logger.exception("Scene step %s's end couldn't be carried on", step.pk)
        for session in Session.objects.filter(pk__in=sessions):
            if another_session_is_busy(session):
                continue
            # Not seen since the worker stopped, so wake_producer starts it again.
            Session.objects.filter(pk=session.pk).update(producer_seen_at=None)
            wake_producer(str(session.pk))


@worker_ready.connect
def _when_the_worker_starts(**_: object) -> None:
    carry_on_after_the_worker_starts()
    connection.close()
