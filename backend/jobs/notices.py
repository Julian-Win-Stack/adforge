"""Notices: what code posts in the chat when a step falls back to a worse source or fails,
so nothing fails quietly. The producer may leave a failure out of its reply or soften it;
a notice is written by code and says exactly what happened."""

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message

from .models import Job


def post_notice(job: Job, text: str, level: Message.Level) -> None:
    """Tell the user, in the chat, that something fell back or failed. `text` says three
    things: what failed, what was used instead, and what that means for the ad. The notice
    is also kept in `job.warnings`, so the ad can be found later. No model is ever given a
    notice: the producer learns of the failure from the tool's result."""
    warning = {"level": level, "text": text, "at": timezone.now().isoformat()}
    # The job is read again under a lock, so a notice posted through another copy of the job
    # in the meantime isn't overwritten; the warning and the message are kept together.
    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        locked.warnings.append(warning)
        locked.save(update_fields=["warnings"])
        # A job started before sessions existed has no chat to post in.
        if locked.session is not None:
            messages.add(locked.session, role=Message.Role.NOTICE, text=text, level=level)
    job.warnings = locked.warnings
