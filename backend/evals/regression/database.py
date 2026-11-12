"""Fresh eval databases and durable dumps; never repoint a running Django process."""

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql


class DatabaseError(RuntimeError):
    """A database command failed; its message omits credential-bearing arguments."""


def url_for_database(name: str, connection_url: str | None = None) -> str:
    base = connection_url or os.environ.get("DATABASE_URL")
    if not base:
        raise ValueError("Set DATABASE_URL to the Postgres server used for eval databases.")
    parts = urlsplit(base)
    if parts.scheme not in ("postgres", "postgresql"):
        raise ValueError("Regression evals need a Postgres DATABASE_URL.")
    return urlunsplit(parts._replace(path="/" + quote(name, safe="")))


def _pg(*args: str) -> None:
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        # CalledProcessError's own text includes the full URL and its password.
        raise DatabaseError(error.stderr.strip() or f"{args[0]} failed.") from None


@contextmanager
def isolated_database(
    source: Path | None = None, *, connection_url: str | None = None
) -> Iterator[str]:
    """Yield a new database's URL, optionally restored. Drop only our generated DB."""
    name = "reg_eval_" + uuid4().hex
    url = url_for_database(name, connection_url)
    admin = url_for_database("postgres", connection_url)
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        if source is not None:
            _pg("pg_restore", "--no-owner", "--no-privileges", "--dbname", url, str(source))
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def dump_database(url: str, into: Path) -> None:
    """Publish a custom-format dump only after pg_dump and the disk flush succeeded."""
    into.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=into.parent, prefix=".db-", suffix=".dump", delete=False
    ) as file:
        temporary = Path(file.name)
    try:
        _pg(
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(temporary),
            url,
        )
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        os.replace(temporary, into)
        descriptor = os.open(into.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def child_environment(url: str, media_dir: Path) -> dict[str, str]:
    """Set up a child while retaining the parent's service credentials and network trust."""
    return {
        **os.environ,
        "DATABASE_URL": url,
        "MEDIA_ROOT": str(media_dir.resolve()),
        "DJANGO_SETTINGS_MODULE": "adforge.settings",
        "DJANGO_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "QUALITY_CHECKS": "1",
        "ONE_AD_AT_A_TIME": "0",
    }
