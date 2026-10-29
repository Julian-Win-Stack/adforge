"""Locks that make one job's work of one kind run one at a time, across workers."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection

from .models import Job

# The job's library: what is checked, labelled and added to it (jobs.library_items).
LIBRARY_LOCK = 5
# The product-only versions of the library's photos: which are made (jobs.library_items).
PRODUCT_ONLY_LOCK = 6


@contextmanager
def one_job_at_a_time(lock: int, job: Job) -> Iterator[None]:
    """Wait for, then hold while the block runs, the Postgres advisory lock `lock` for `job`,
    so work done under it for the same job runs one at a time. It is held by the database
    connection, not a transaction, so what is done under it is kept as it happens."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", [lock, str(job.pk)])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", [lock, str(job.pk)])
