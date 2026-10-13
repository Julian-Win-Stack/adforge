"""ffmpeg and ffprobe runs replayed from an earlier run whose every input was the same.

Most chat tests build the same ad from the same fake clips, voices and music, and running
ffmpeg for each of them took about half the suite's time. A run is replayed only when its
arguments match an earlier one's and every file it can read is byte for byte the same:
every file in every folder its arguments name, and every file named inside a small text
file it reads (a list of clips to join, a subtitles file). It then writes the same files
the earlier run wrote and says the same things. A run that failed is never kept, so a
failure is always ffmpeg's own. Each test worker keeps its own replays."""

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

_run = subprocess.run

# A path written inside an argument, as in a filter's `subtitles=/tmp/x/line.ass`.
_PATH = re.compile(r"/[^\s:'\",;=\[\]|]+")
# A text file this small may name the files ffmpeg is to read.
_LISTS_UP_TO_BYTES = 65_536


class _Replay:
    def __init__(self, wrote: dict[str, bytes], stdout: Any, stderr: Any) -> None:
        self.wrote = wrote
        self.stdout = stdout
        self.stderr = stderr


_replays: dict[str, _Replay] = {}
# Shared by every test worker, which add and remove files there as they please: a run that
# reads from it is never replayed.
_SHARED = Path(tempfile.gettempdir()).resolve()


def _hash(file: Path) -> str:
    # Hashed every time, never remembered by its time and size: a file rewritten within one
    # tick of a coarse clock keeps both.
    return hashlib.sha256(file.read_bytes()).hexdigest()


def _files_named_in(text: str) -> list[Path]:
    return [Path(path) for path in _PATH.findall(text) if os.path.isfile(path)]


def _files_read(arguments: list[str]) -> list[Path]:
    named = []
    for argument in arguments:
        if os.path.isfile(argument):
            named.append(Path(argument))
        named += _files_named_in(argument)
    for file in list(named):
        if file.stat().st_size <= _LISTS_UP_TO_BYTES:
            try:
                named += _files_named_in(file.read_text())
            except UnicodeDecodeError:
                pass
    return named


def _folder_files(folders: list[Path]) -> dict[Path, str]:
    files = {}
    for folder in folders:
        for file in folder.iterdir():
            try:
                if file.is_file():
                    files[file] = _hash(file)
            except FileNotFoundError:
                # Gone while the folder was read: a file nothing here can still be reading.
                pass
    return files


def _shared(folder: Path) -> bool:
    return folder.resolve() == _SHARED


def run(arguments: Any, *args: Any, **kwargs: Any) -> Any:
    """subprocess.run, replaying an ffmpeg or ffprobe run seen before with the same inputs."""
    if not (
        isinstance(arguments, list)
        and arguments
        and arguments[0] in (settings.FFMPEG, settings.FFPROBE)
    ):
        return _run(arguments, *args, **kwargs)
    read = _files_read(arguments[1:])
    folders = {file.parent for file in read}
    if arguments[-1].startswith("/"):
        folders.add(Path(arguments[-1]).parent)
    in_order = sorted((folder for folder in folders if folder.is_dir()), key=str)
    if any(_shared(folder) for folder in in_order):
        return _run(arguments, *args, **kwargs)
    # Each folder by its place, so runs in two tests' own temporary folders match.
    places = {str(folder): f"<folder {i}>" for i, folder in enumerate(in_order)}

    def placed(text: str) -> str:
        for folder in sorted(places, key=len, reverse=True):
            text = text.replace(folder, places[folder])
        return text

    def unplaced(text: str) -> Path:
        for folder, place in places.items():
            text = text.replace(place, folder)
        return Path(text)

    before = _folder_files(in_order)
    key = hashlib.sha256(
        repr(
            (
                [placed(argument) for argument in arguments],
                bool(kwargs.get("text")),
                sorted((placed(str(file)), digest) for file, digest in before.items()),
            )
        ).encode()
    ).hexdigest()
    if key in _replays:
        replay = _replays[key]
        for name, data in replay.wrote.items():
            unplaced(name).write_bytes(data)
        return subprocess.CompletedProcess(arguments, 0, replay.stdout, replay.stderr)
    ran = _run(arguments, *args, **kwargs)
    if ran.returncode == 0:
        after = _folder_files(in_order)
        wrote = {
            placed(str(file)): file.read_bytes()
            for file, digest in after.items()
            if before.get(file) != digest
        }
        _replays[key] = _Replay(wrote, ran.stdout, ran.stderr)
    return ran
