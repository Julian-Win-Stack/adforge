"""The one piece of code that touches files. Local disk for now (MEDIA_ROOT); moving to
object storage means changing the STORAGES setting, not the code that calls this."""

from collections.abc import Callable

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def save(name: str, data: bytes) -> str:
    """Store `data` and return the key to find it again. Never overwrites: if `name` is
    already taken, the key gets a unique suffix."""
    return default_storage.save(name, ContentFile(data))


def save_once(name: str, make: Callable[[], bytes]) -> str:
    """Store what `make` gives as `name`, unless something is already kept there: for a file
    whose name says what is in it, so asking for it again finds the one already made."""
    if default_storage.exists(name):
        return name
    return save(name, make())


def read(key: str) -> bytes:
    with default_storage.open(key, "rb") as stored:
        data: bytes = stored.read()
    return data


def url(key: str) -> str:
    return default_storage.url(key)
