"""The one piece of code that touches files. Local disk for now (MEDIA_ROOT); moving to
object storage means changing the STORAGES setting, not the code that calls this."""

import io
from collections.abc import Callable
from pathlib import Path

import PIL.Image
from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage


def save(name: str, data: bytes) -> str:
    """Store `data` and return the key to find it again. Never overwrites: if `name` is
    already taken, the key gets a unique suffix."""
    return default_storage.save(name, ContentFile(data))


def save_file(name: str, path: Path) -> str:
    """Store the file at `path`, read as it is copied, never whole in memory; as `save`."""
    with path.open("rb") as given:
        return default_storage.save(name, File(given))


def save_upload(name: str, upload: File[bytes]) -> str:
    """Store a file sent to the app, copied in chunks from where it was received (its
    temporary file for a big one), never whole in memory; as `save`."""
    return default_storage.save(name, upload)


def save_once(name: str, make: Callable[[], bytes], *, whole: Callable[[bytes], bool]) -> str:
    """Store what `make` gives as `name`, unless something `whole` is already kept there: for a
    file whose name says what is in it, so asking for it again finds the one already made. A
    file left half-written (a worker stopped while saving it) is made again."""
    if default_storage.exists(name):
        if whole(read(name)):
            return name
        default_storage.delete(name)
    return save(name, make())


def read(key: str) -> bytes:
    with default_storage.open(key, "rb") as stored:
        data: bytes = stored.read()
    return data


def url(key: str) -> str:
    return default_storage.url(key)


def exists(key: str) -> bool:
    return bool(default_storage.exists(key))


def whole_picture(data: bytes) -> bool:
    """Whether `data` decodes as a whole picture, not one a stopped worker left half-saved: a
    `whole` check for `save_once`."""
    try:
        PIL.Image.open(io.BytesIO(data)).load()
    except OSError, PIL.Image.DecompressionBombError:
        return False
    return True
