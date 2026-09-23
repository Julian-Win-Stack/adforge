import logging
import threading
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message, Session
from gateway.types import UnusableReply

from . import loop
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
        loop.run(PRODUCER, session)
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


def _beat(session_id: str, stop: threading.Event) -> None:
    """Say the producer is still alive every PRODUCER_HEARTBEAT_SECONDS, until `stop`.
    Runs on its own thread, so on its own database connection, which it closes."""
    try:
        while not stop.wait(settings.PRODUCER_HEARTBEAT_SECONDS):
            Session.objects.filter(pk=session_id).update(producer_seen_at=timezone.now())
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
def restart_dead_producers() -> None:
    """Start the producer again in every session where it died, so the session carries on
    without the user having to send anything. Run every minute."""
    for session_id in Session.objects.filter(producer_running=True).values_list("pk", flat=True):
        wake_producer(str(session_id))
