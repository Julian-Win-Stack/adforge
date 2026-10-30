"""Locks that make one job's work of one kind run one at a time, across workers."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection

from .models import Job

# The job's library: what is checked, labelled and added to it (jobs.library_items).
LIBRARY_LOCK = 5
# The product-only versions of the library's photos: which are made (jobs.library_items).
PRODUCT_ONLY_LOCK = 6
# One library picture recoloured one colour (jobs.recolour): made, checked and added once.
RECOLOUR_LOCK = 7
# An owner part's picture (jobs.owner_parts): drawn, labelled and kept once per job at a time.
PART_PICTURE_LOCK = 8
# One library photo's stranger's face changed to the presenter's (jobs.face_swap): made,
# checked and added once per portrait.
FACE_SWAP_LOCK = 9


@contextmanager
def one_job_at_a_time(lock: int, job: Job) -> Iterator[None]:
    """Wait for, then hold while the block runs, the Postgres advisory lock `lock` for `job`,
    so work done under it for the same job runs one at a time. It is held by the database
    connection, not a transaction, so what is done under it is kept as it happens."""
    with one_at_a_time(lock, str(job.pk)):
        yield


@contextmanager
def one_at_a_time(lock: int, key: str) -> Iterator[None]:
    """one_job_at_a_time for work keyed by `key`, such as one job's one picture, rather than
    the whole job."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", [lock, key])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", [lock, key])
