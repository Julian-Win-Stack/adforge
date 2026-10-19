"""Notices: what code posts in the chat when a step falls back to a worse source or fails,
so nothing fails quietly. The producer may leave a failure out of its reply or soften it;
a notice is written by code and says exactly what happened."""

from collections.abc import Sequence

from django.db import transaction
from django.utils import timezone

from chat import messages
from chat.models import Message

from .models import Job


def post_notice(
    job: Job,
    text: str,
    level: Message.Level,
    carrying: Sequence[messages.AttachedFile] = (),
    step: messages.StepSignal | None = None,
    suggested: messages.SuggestedNote | None = None,
    plan: messages.PlanSignal | None = None,
    tries_of: int | None = None,
    pasted_page_result: str = "",
) -> Message | None:
    """Tell the user, in the chat, that something fell back or failed, with any files that
    show it. `text` says three things: what failed, what was used instead, and what that
    means for the ad. The notice is also kept in `job.warnings`, so the ad can be found
    later. No model is ever given a notice: the producer learns of the failure from the
    tool's result. A notice about a scene's starting picture or clip carries its `step`, and one
    may suggest a note on a scene; one about a fix plan carries its `plan`; one showing every
    try of a clip the judge failed names its clip step, `tries_of`; one answering the page
    panel's message carries its `pasted_page_result`. Gives the message
    posted, or None for a job with no chat."""
    warning = {"level": level, "text": text, "at": timezone.now().isoformat()}
    # The job is read again under a lock, so a notice posted through another copy of the job
    # in the meantime isn't overwritten; the warning and the message are kept together.
    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        locked.warnings.append(warning)
        locked.save(update_fields=["warnings"])
        # A job started before sessions existed has no chat to post in.
        posted = None
        if locked.session is not None:
            posted = messages.add(
                locked.session,
                role=Message.Role.NOTICE,
                text=text,
                level=level,
                carrying=carrying,
                step=step,
                suggested=suggested,
                plan=plan,
                tries_of=tries_of,
                pasted_page_result=pasted_page_result,
            )
    job.warnings = locked.warnings
    return posted
