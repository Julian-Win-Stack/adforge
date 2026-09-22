from celery import shared_task

from chat.models import Session

from . import loop
from .producer import PRODUCER


@shared_task
def run_producer(session_id: str) -> None:
    """Let the producer work in the session until it replies."""
    loop.run(PRODUCER, Session.objects.get(pk=session_id))
