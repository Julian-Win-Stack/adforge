import logging
import threading
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.gateway import charged_to
from gateway.types import UnusableReply
from jobs.models import SceneStep
from jobs.work import make_starting_picture

from . import loop
from .loop import EXPECTED_FAILURES, why_it_failed
from .producer import PRODUCER

logger = logging.getLogger(__name__)


# Never handed out again after a crash: Celery can't be relied on to, so a producer that
# died is noticed by its heartbeat stopping, and started again (see wake_producer).
@shared_task(acks_late=False)
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies. If it can't carry on, the
    chat is told why."""
    session = Session.objects.get(pk=session_id)
    stop_beating = threading.Event()
    heartbeat = threading.Thread(target=_beat, args=(session_id, stop_beating), daemon=True)
    heartbeat.start()
    try:
        # The producer only stops once it has read everything the user sent and every
        # scene step's result: a message that arrives as it replies is an interrupt, and it
        # works on that too.
        while not _stop_unless_theres_more_to_read(session_id, loop.run(PRODUCER, session)):
            pass
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
        stop_beating.set()
        heartbeat.join()
    # No producer is working any more, so the next message starts one.
    with transaction.atomic():
        messages.add(
            session,
            role=Message.Role.AGENT,
            text=f"I had to stop: {why}. Send a message to try again.",
        )
        Session.objects.filter(pk=session_id).update(producer_running=False)


def _stop_unless_theres_more_to_read(session_id: str, read_up_to: int) -> bool:
    """Mark the producer stopped, unless the user said something after the messages up to
    `read_up_to` it was last given, or a scene step finished that it hasn't been given. Both
    happen under the session's lock, which a message sent and a step finished take before
    they look for a producer, so each is either found here or starts a new producer itself."""
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        if session.messages.filter(role=Message.Role.USER, seq__gt=read_up_to).exists():
            return False
        if SceneStep.objects.filter(
            scene__job__session=session, finished_at__isnull=False, producer_read_at__isnull=True
        ).exists():
            return False
        session.producer_running = False
        session.save(update_fields=["producer_running"])
        return True


def _beat(session_id: str, stop: threading.Event) -> None:
    """Say the producer is still alive every PRODUCER_HEARTBEAT_SECONDS, until `stop`.
    Runs on its own thread, so on its own database connection, which it closes."""
    try:
        while not stop.wait(settings.PRODUCER_HEARTBEAT_SECONDS):
            # One beat that fails mustn't stop the rest: a producer that stops beating is
            # taken for dead, and a second one started while it still works.
            try:
                Session.objects.filter(pk=session_id).update(producer_seen_at=timezone.now())
            except Exception:
                logger.exception("The producer's heartbeat failed in session %s", session_id)
                connection.close()
    finally:
        connection.close()


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


@shared_task
def run_scene_step(step_id: int) -> None:
    """Do a scene step's work in the background, then have the producer told: it reads the
    result on its next turn, and is started if it isn't working."""
    step = SceneStep.objects.select_related("scene__job", "tool_call__session").get(pk=step_id)
    scene = step.scene
    session = step.tool_call.session
    try:
        with charged_to(step.tool_call):
            picture = make_starting_picture(step)
    except Exception as error:
        if not isinstance(error, EXPECTED_FAILURES):
            logger.exception("Scene step %s failed", step_id)
        step.status = SceneStep.Status.FAILED
        step.reason = why_it_failed(error, "the step")
        step.result = (
            f"Background step failed: scene {scene.number}'s starting picture couldn't be "
            f"made: {step.reason} Tell the shop owner what went wrong."
        )
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "reason", "result", "finished_at"])
    else:
        assert step.photo is not None, "a starting picture is made from a photo"
        # Shown and finished together, so a step run again never shows the picture twice.
        with transaction.atomic():
            messages.add(
                session,
                role=Message.Role.AGENT,
                carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, picture.file)],
            )
            step.status = SceneStep.Status.FINISHED
            step.result = (
                f"Background step finished: scene {scene.number}'s starting picture is ready "
                f"(version {picture.version}), and is shown to the shop owner in the chat. "
                f"Photo {step.photo.position} was used: "
                f"{step.photo_reason} Tell the shop owner."
            )
            step.finished_at = timezone.now()
            step.save(update_fields=["status", "result", "finished_at"])
    # Only once the result is stored: a producer that is stopping either finds it, or has
    # stopped by the time this looks, and is started again.
    wake_producer(str(session.pk))


@shared_task
def restart_dead_producers() -> None:
    """Start the producer again in every session where it died, so the session carries on
    without the user having to send anything. Run every minute."""
    for session_id in Session.objects.filter(producer_running=True).values_list("pk", flat=True):
        wake_producer(str(session_id))
