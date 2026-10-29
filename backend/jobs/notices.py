"""Notices: what code posts in the chat when a step falls back to a worse source or fails,
so nothing fails quietly. The producer may leave a failure out of its reply or soften it;
a notice is written by code and says exactly what happened."""

from collections.abc import Sequence

from django.conf import settings
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
    tries_of: int | None = None,
    owner_question: int | None = None,
    pasted_page_result: str = "",
    wants_pasted_page: bool | None = None,
    wants_more_photos: bool = False,
) -> Message | None:
    """Tell the user, in the chat, that something fell back or failed, with any files that
    show it. `text` says three things: what failed, what was used instead, and what that
    means for the ad. The notice is also kept in `job.warnings`, so the ad can be found
    later. No model is ever given a notice: the producer learns of the failure from the
    tool's result. A notice about a scene's starting picture or clip carries its `step`, and one
    may suggest a note on a scene; one showing every
    try of a clip the judge failed names its clip step, `tries_of`; one saying the fixing
    agent asked the shop owner a question names it, `owner_question`; one about the product
    page says whether it wants the page panel, `wants_pasted_page`; one answering the page panel's
    message carries its `pasted_page_result`, and one keeping a pasted page with too few
    photos `wants_more_photos`. Gives the message
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
                tries_of=tries_of,
                owner_question=owner_question,
                pasted_page_result=pasted_page_result,
                wants_pasted_page=wants_pasted_page,
                wants_more_photos=wants_more_photos,
            )
    job.warnings = locked.warnings
    return posted


def left_out_by_the_filter(pictures: Sequence[str], done: str) -> str:
    """The notice for work a model's safety filter refused until `pictures` were left out
    of it (requirement 22): which it went without, and why. `done` says what was done
    without them, such as "the ad was planned"."""
    named = pictures[0] if len(pictures) == 1 else f"{', '.join(pictures[:-1])} and {pictures[-1]}"
    return (
        f"The AI model's safety filter refused to look at {named}, so {done} without "
        f"{'it' if len(pictures) == 1 else 'them'}."
    )


def post_left_out_by_the_filter(job: Job, pictures: Sequence[str], done: str) -> None:
    """Tell the chat in red, with its warning line, that a model's safety filter refused
    work until `pictures` were left out of it (see left_out_by_the_filter). Told once: the
    same work made again past the same refusal, as on a redraw or a run again after a
    worker stopped, adds nothing."""
    text = left_out_by_the_filter(pictures, done)
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, text, Message.Level.PROBLEM)


def refused_by_the_filter(what: str, then: str) -> str:
    """The notice for work a model's safety filter refused over `what` itself, which can't be
    left out of it (requirement 22): `then` says what happened instead, such as "it is kept
    unchecked"."""
    return f"The AI model's safety filter refused to look at {what}, so {then}."


def post_refused_by_the_filter(job: Job, what: str, then: str) -> None:
    """Tell the chat in red, with its warning line, that a model's safety filter refused to
    look at `what` (see refused_by_the_filter). Told once, as post_left_out_by_the_filter."""
    text = refused_by_the_filter(what, then)
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, text, Message.Level.PROBLEM)


def pictures_moved_to_azure() -> str:
    """The notice for a session whose pictures move to our Azure copy of the picture model,
    because OpenAI refused one for money (Julian 2026-10-29)."""
    return (
        "OpenAI refused to draw a picture because our OpenAI account is out of money (its "
        "spend limit or quota is reached), so this session's pictures are drawn on our Azure "
        "copy of the same picture model from now on. Azure draws at most "
        f"{settings.AZURE_PICTURES_PER_MINUTE} a minute, so pictures may take longer."
    )


def post_pictures_moved_to_azure(job: Job) -> None:
    """Tell the chat in red, with its warning line, that the session's pictures are drawn on
    Azure from now on (see pictures_moved_to_azure). Told once."""
    text = pictures_moved_to_azure()
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, text, Message.Level.PROBLEM)
