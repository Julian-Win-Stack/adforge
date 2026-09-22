import logging

from celery import shared_task

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message, Session
from gateway.types import UnusableReply

from . import loop
from .producer import PRODUCER

logger = logging.getLogger(__name__)


@shared_task
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies. If it can't carry on, the
    chat is told why."""
    session = Session.objects.get(pk=session_id)
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
    messages.add(
        session,
        role=Message.Role.AGENT,
        text=f"I had to stop: {why}. Send a message to try again.",
    )
