from django.db import transaction
from django.db.models import Max

from .models import ActivityEntry, Job


def record(job: Job, message: str, *, reason: str, status: Job.Status | None = None) -> None:
    """Add a step to the job's activity view, optionally moving the job to a new status.

    Entries are numbered 1, 2, 3... per job. The job row is locked while numbering, so
    entries always become visible in number order. Without that, a poll could see #12
    before #11 is saved, move its "since" marker past 11, and never show #11."""
    with transaction.atomic():
        locked = Job.objects.select_for_update().get(pk=job.pk)
        last = locked.activity.aggregate(last=Max("seq"))["last"] or 0
        ActivityEntry.objects.create(job=locked, seq=last + 1, message=message, reason=reason)
        # The status changes in the same transaction as its entry, so a poll that sees a
        # finished job has already been able to see every entry.
        if status is not None:
            job.status = status
            job.save(update_fields=["status"])
