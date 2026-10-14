"""Work run in a thread of its own."""

from collections.abc import Callable
from typing import Any

from django.db import connection


def closing_its_connection[Out](work: Callable[..., Out], *args: Any) -> Out:
    """Run `work` in a thread of its own, then close the database connection the thread
    opened."""
    try:
        return work(*args)
    finally:
        connection.close()
