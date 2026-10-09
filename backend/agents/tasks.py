import logging
import threading
from collections.abc import Callable, Iterator
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
from gateway.types import BlockedBySafetyFilter, UnusableReply
from jobs.models import ProducedItem, Scene, SceneStep
from jobs.work import (
    make_clip,
    make_line_audio,
    make_starting_picture,
    too_long_for_a_clip,
    transcribe_line_audio,
)

from . import loop
from .loop import BLOCKED, EXPECTED_FAILURES, why_it_failed
from .producer import PRODUCER

logger = logging.getLogger(__name__)


# Never handed out again after a crash: Celery can't be relied on to, so a producer that
# died is noticed by its heartbeat stopping, and started again (see wake_producer).
@shared_task(acks_late=False)
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies. If it can't carry on, the
    chat is told why."""
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
            while not _stop_unless_theres_more_to_read(
                session_id, loop.run(PRODUCER, session, woken_by=_woken_by(session))
            ):
                pass
    except BlockedBySafetyFilter:
        why = BLOCKED
        try_again = ""
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


def _woken_by(session: Session) -> list[str]:
    """The scene steps that finished since the producer last read them, by name."""
    unread = loop.unread_steps(PRODUCER, session).select_related("scene")
    return [_step_name(step) for step in unread.order_by("finished_at", "id")]


def _stop_unless_theres_more_to_read(session_id: str, read_up_to: int) -> bool:
    """Mark the producer stopped, unless the user said something after the messages up to
    `read_up_to` it was last given, or a scene step finished that it hasn't been given. Both
    happen under the session's lock, which a message sent and a step finished take before
    they look for a producer, so each is either found here or starts a new producer itself."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.messages.filter(role=Message.Role.USER, seq__gt=read_up_to).exists():
            return False
        if loop.unread_steps(PRODUCER, session).exists():
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
    return (
        f"Background step finished: scene {step.scene.number}'s starting picture is ready "
        f"(version {picture.version}), and is shown to the shop owner in the chat. "
        f"Photo {step.photo.position} was used: "
        f"{step.photo_reason} Tell the shop owner."
    )


def _audio_finished(step: SceneStep, audio: ProducedItem | None) -> str:
    assert audio is not None, "a line's audio step makes its audio"
    number = step.scene.number
    # A B-roll line too long for any clip was shortened, or its scene is now said to camera:
    # see make_line_audio. The shop owner isn't told: the chat says so if the scene changed.
    scene = Scene.objects.get(pk=step.scene_id)
    too_long = (
        f"Background step finished: scene {number}'s line's audio takes {audio.seconds:g} "
        "seconds to say, too long for any clip. "
    )
    if too_long_for_a_clip(step, audio) and not scene.shows:
        return (
            f"{too_long}Scene {number} is now a talking scene. Make its starting picture "
            "again, then its audio and its clip."
        )
    if too_long_for_a_clip(step, audio) and step.line in scene.shortened_from:
        return (
            f"{too_long}Scene {number}'s line was shortened to fit its clip. Make its audio again."
        )
    if too_long_for_a_clip(step, audio) and step.line == scene.line:
        # Every scene between the first and the last is B-roll, so it isn't said to camera.
        return (
            f"{too_long}Its line couldn't be shortened to fit, and scene {number} shows the "
            "product, so it isn't said to camera. Ask the shop owner for a shorter line of "
            "their own."
        )
    return (
        f"Background step finished: scene {step.scene.number}'s line's audio is ready "
        f"(version {audio.version}, {audio.seconds:g} seconds). It isn't shown to the shop "
        "owner. Transcribe it next. Tell the shop owner."
    )


def _transcript_finished(step: SceneStep, transcript: ProducedItem | None) -> str:
    assert transcript is not None and transcript.made_from is not None, (
        "a transcript is made from audio"
    )
    return (
        f"Background step finished: scene {step.scene.number}'s audio (version "
        f"{transcript.made_from.version}) was transcribed (version {transcript.version}). It "
        f'was heard as: "{transcript.text}" Tell the shop owner.'
    )


def _clip_finished(step: SceneStep, clip: ProducedItem | None) -> str:
    assert clip is not None and clip.made_from is not None, "a clip is made from its audio"
    number = step.scene.number
    # A B-roll clip made before every B-roll scene got a picture may have none.
    picture = f"starting picture version {clip.picture.version} and " if clip.picture else ""
    return (
        f"Background step finished: scene {number}'s clip is ready (version {clip.version}, "
        f"{clip.seconds:g} seconds), made from {picture}audio version "
        f"{clip.made_from.version}. Scene {number} is finished. Tell the shop owner."
    )


@dataclass(frozen=True)
class StepWork:
    """What one kind of scene step does: its name in what the producer is told, the work
    itself, what the producer is told when it finishes, and what the chat shows then."""

    name: str
    # Gives what it made.
    make: Callable[[SceneStep], ProducedItem | None]
    finished: Callable[[SceneStep, ProducedItem | None], str]
    shown: Callable[[ProducedItem | None], list[messages.AttachedFile]]


STEP_WORK = {
    SceneStep.Kind.STARTING_PICTURE: StepWork(
        name="starting picture",
        make=make_starting_picture,
        finished=_picture_finished,
        shown=lambda picture: (
            [messages.AttachedFile(Attachment.Kind.PICTURE, picture.file)] if picture else []
        ),
    ),
    # The line's audio and its transcript are the producer's to judge, not the shop owner's.
    SceneStep.Kind.LINE_AUDIO: StepWork(
        name="line's audio",
        make=make_line_audio,
        finished=_audio_finished,
        shown=lambda _: [],
    ),
    SceneStep.Kind.TRANSCRIPT: StepWork(
        name="transcript",
        make=transcribe_line_audio,
        finished=_transcript_finished,
        shown=lambda _: [],
    ),
    # Clips are shown once the whole ad is put together from them.
    SceneStep.Kind.CLIP: StepWork(
        name="clip",
        make=make_clip,
        finished=_clip_finished,
        shown=lambda _: [],
    ),
}


def _step_name(step: SceneStep) -> str:
    """Such as "scene 1's starting picture"."""
    return f"scene {step.scene.number}'s {STEP_WORK[SceneStep.Kind(step.kind)].name}"


@shared_task
def run_scene_step(step_id: int) -> None:
    """Do a scene step's work in the background, then have the producer told: it reads the
    result on its next turn, and is started if it isn't working."""
    step = SceneStep.objects.select_related("scene__job", "tool_call__session").get(pk=step_id)
    # Celery may hand a step out again after a crash. One that has finished or failed since
    # is left as it is, so nothing is shown or paid for twice.
    if step.status != SceneStep.Status.RUNNING:
        return
    session = step.tool_call.session
    work = STEP_WORK[SceneStep.Kind(step.kind)]
    try:
        with (
            _beating(
                settings.STEP_HEARTBEAT_SECONDS,
                lambda: SceneStep.objects.filter(pk=step_id).update(seen_at=timezone.now()),
                f"Scene step {step_id}",
            ),
            charged_to(step.tool_call),
            tracing.scene_step(
                _step_name(step),
                session_id=str(session.pk),
                trace_id=step.tool_call.trace_id,
                parent_id=step.tool_call.observation_id,
            ),
        ):
            made = work.make(step)
        tracing.flush()
        # Shown and finished together, so a step run again never shows anything twice. The
        # producer is woken in the same transaction, so its session never looks idle
        # between the two, when another session could start.
        with transaction.atomic():
            shown = work.shown(made)
            if shown:
                messages.add(session, role=Message.Role.AGENT, carrying=shown)
            step.status = SceneStep.Status.FINISHED
            step.result = work.finished(step, made)
            step.finished_at = timezone.now()
            step.save(update_fields=["status", "result", "finished_at"])
            wake_producer(str(session.pk))
    # Whatever stops the step, it isn't left running: the producer is told why.
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("Scene step %s failed", step_id)
        tracing.flush()
        with transaction.atomic():
            _fail(step, why_it_failed(error, "the step"))
            wake_producer(str(session.pk))


def _fail(step: SceneStep, reason: str) -> None:
    """Mark the step failed for `reason`, and word what the producer is told."""
    step.status = SceneStep.Status.FAILED
    step.reason = reason
    step.result = (
        f"Background step failed: {_step_name(step)} couldn't be made: {reason} "
        "Tell the shop owner what went wrong."
    )
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "result", "finished_at"])


@shared_task
def restart_dead_producers() -> None:
    """Start the producer again in every session where it died, so the session carries on
    without the user having to send anything. Run every minute."""
    for session_id in Session.objects.filter(producer_running=True).values_list("pk", flat=True):
        wake_producer(str(session_id))


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
        for step in SceneStep.objects.filter(status=SceneStep.Status.RUNNING).select_related(
            "scene", "tool_call"
        ):
            _fail(step, "The worker restarted while it was being made.")
            sessions.add(step.tool_call.session_id)
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
