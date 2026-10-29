"""Product videos: the videos the shop placed with the product photos in the product's
gallery, as the photo picker picks them off the page. Each is kept with the job as a file
with its first frame, frames spread over it and its length; or, when no file can be had, as
its link with the page's poster or the player's thumbnail as its first frame. A page's videos
are fetched all at once. The watcher (jobs.watcher) watches each file."""

import contextvars
import functools
import logging
import mimetypes
import re
import subprocess
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.gateway import IMAGE_TYPES
from gateway.types import LEAST_PIECE_SECONDS, MOST_PIECE_SECONDS, PieceTrim, ReferenceVideo
from jobs import page
from jobs.models import Job, ProductVideo
from jobs.threads import closing_its_connection

logger = logging.getLogger(__name__)

# Not a cap on videos: length is the only cut (requirement 23, Julian Oct 27 2026 04:41, "no
# byte cap"; WATCH_VIDEO_MOST_SECONDS). This only stops a link that never ends, such as a
# live stream, from filling the memory: one bigger is kept as its link.
NEVER_ENDING_BYTES = 2_000_000_000
# How many frames, spread over a video, are kept with it.
FRAMES = 4
# Where YouTube keeps a video's thumbnail, by the video's id.
YOUTUBE_THUMBNAIL = "https://img.youtube.com/vi/{id}/hqdefault.jpg"
YOUTUBE_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{6,})")
# ffmpeg reads a kept file only as a plain video, and reaches nothing else: a file that is
# really a playlist (HLS) would have it fetch the links inside.
_READ_ONLY_THE_FILE = [
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm",
]
_TIMEOUT_SECONDS = 120


def save_videos(job: Job, players: Sequence[dict[str, Any]]) -> list[ProductVideo]:
    """Keep each picked video player, as the page script lists it (src, poster, kind), in
    the order given, once per video. A page read again only adds the videos the job doesn't
    have yet, after those it has: one at a link already kept is left out, and so is one with
    no link while the job keeps one with none, which can't be told apart. Every video is
    fetched at the same time. Gives back the videos added."""
    had = list(job.videos.values_list("source_url", flat=True))
    seen: set[str] = {src for src in had if src}
    position = max(job.videos.values_list("position", flat=True), default=0)
    new: list[tuple[int, dict[str, Any]]] = []
    for player in players:
        src, poster = str(player.get("src") or ""), str(player.get("poster") or "")
        found_by = src or poster
        if not found_by or found_by in seen or not _storable(src):
            continue
        if not src and "" in had:
            continue
        seen.add(found_by)
        position += 1
        new.append((position, player))
    if not new:
        return []
    with ThreadPoolExecutor(max_workers=len(new)) as pool:
        kept = [
            pool.submit(
                contextvars.copy_context().run,
                closing_its_connection,
                keep_video,
                job,
                position,
                player,
            )
            for position, player in new
        ]
        return [each.result() for each in kept]


def _storable(src: str) -> bool:
    """Whether a player's link can be kept as its video's link: none, a web link or a blob:
    link, short enough for the column. A data: link holds the video itself."""
    longest = ProductVideo._meta.get_field("source_url").max_length or 0
    return (not src or src.startswith(("http://", "https://", "blob:"))) and len(src) <= longest


def keep_video(job: Job, position: int, player: dict[str, Any]) -> ProductVideo:
    """Store one video player's video with the job: its file and frames when a file can be
    had, else its link and any first frame."""
    src, poster = str(player.get("src") or ""), str(player.get("poster") or "")
    kind = (
        ProductVideo.Kind.IFRAME
        if player.get("kind") == ProductVideo.Kind.IFRAME
        else ProductVideo.Kind.VIDEO
    )
    folder = f"jobs/{job.pk}/videos/{position}"
    video = ProductVideo(job=job, position=position, source_url=src, kind=kind)
    # An embedded player's link is its web page, never a file.
    if kind == ProductVideo.Kind.VIDEO and (download := _video_file(src)) is not None:
        extension = mimetypes.guess_extension(download.content_type) or ".mp4"
        looked = _look_at(download.content, extension)
        if looked is not None:
            video.file = file_store.save(f"{folder}{extension}", download.content)
            video.duration = looked.duration
            video.first_frame = file_store.save(f"{folder}-first.jpg", looked.first)
            video.frames = [
                file_store.save(f"{folder}-frame-{number}.jpg", frame)
                for number, frame in enumerate(looked.frames, 1)
            ]
    if not video.first_frame:
        video.first_frame = _still(src, poster, folder)
    video.save()
    return video


def _video_file(src: str) -> page.Download | None:
    """The video file at `src`, or None when there is none to keep: a link that isn't a web
    link (blob:), won't download, never ends (NEVER_ENDING_BYTES), or isn't a video file (a
    stream's playlist, a web page)."""
    try:
        download = page.download(src, max_bytes=NEVER_ENDING_BYTES, what="product video")
    except page.PageUnreadable, OutsideServiceDown:
        return None
    if not download.content_type.startswith("video/"):
        return None
    return download


@dataclass(frozen=True)
class _Looked:
    duration: float
    first: bytes
    frames: list[bytes]


def _look_at(content: bytes, extension: str) -> _Looked | None:
    """The video's length in seconds, its first frame and FRAMES frames spread over it, as
    JPEGs; None if ffmpeg can't read it as a video."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / f"video{extension}"
        file.write_bytes(content)
        try:
            duration = _duration(file)
            if duration is None:
                return None
            first = _frame(file, first_frame_time(duration))
            frames = [_frame(file, at) for at in frame_times(duration)]
        except RuntimeError, subprocess.TimeoutExpired:
            return None
        return _Looked(duration, first, frames)


def first_frame_time(duration: float) -> float:
    """When the opening picture is taken: half a second in, past a fade from black, or
    halfway through a shorter video."""
    return min(0.5, duration / 2)


def frame_times(duration: float) -> list[float]:
    """When each of the FRAMES frames is taken: the middle of each of FRAMES equal parts."""
    return [round(duration * (number + 0.5) / FRAMES, 3) for number in range(FRAMES)]


def _duration(file: Path) -> float | None:
    probed = subprocess.run(
        [settings.FFPROBE, "-v", "error", *_READ_ONLY_THE_FILE]
        + ["-show_entries", "format=duration", "-of", "csv=p=0", str(file)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    try:
        duration = float(probed.stdout.strip())
    except ValueError:
        return None
    return duration if probed.returncode == 0 and duration > 0 else None


def frame_of(video: ProductVideo, second: float) -> bytes:
    """The frame `second` seconds into the video's file, at its full size, as a JPEG."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / f"video{Path(video.file).suffix}"
        file.write_bytes(file_store.read(video.file))
        return _frame(file, second)


def _frame(file: Path, at: float) -> bytes:
    """The frame `at` seconds into the video, as a JPEG."""
    ran = subprocess.run(
        [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{at:g}"]
        + [*_READ_ONLY_THE_FILE, "-i", str(file), "-frames:v", "1", "-f", "image2"]
        + ["-vcodec", "mjpeg", "pipe:1"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
    )
    if ran.returncode != 0 or not ran.stdout:
        raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
    return ran.stdout


def _still(src: str, poster: str, folder: str) -> str:
    """The key of a first frame had without the video's file: the page's poster for it,
    else the player's thumbnail for a YouTube player; "" if neither can be had."""
    links = [poster] if poster else []
    if found := YOUTUBE_ID.search(src):
        links.append(YOUTUBE_THUMBNAIL.format(id=found[1]))
    for link in links:
        try:
            still = page.download(link, max_bytes=page.MAX_PHOTO_BYTES, what="video poster")
        except page.PageUnreadable, OutsideServiceDown:
            continue
        if still.content_type in IMAGE_TYPES:
            extension = mimetypes.guess_extension(still.content_type) or ""
            return file_store.save(f"{folder}-first{extension}", still.content)
    return ""


@dataclass(frozen=True)
class PieceWanted:
    """A piece of a page video to send as a reference video: the video by its key in the
    file store, and the seconds of it wanted, as the watcher gave a moment's."""

    video: str
    start: float
    end: float


@dataclass(frozen=True)
class LeftOutPiece:
    """A piece wanted that isn't sent, and why."""

    wanted: PieceWanted
    reason: str


@dataclass(frozen=True)
class PiecesCut:
    """The pieces cut, in the order wanted, ready to send; and those left out."""

    sent: list[ReferenceVideo]
    left_out: list[LeftOutPiece]


def cut_pieces(wanted: Sequence[PieceWanted]) -> PiecesCut:
    """Cut each piece wanted from its page video with ffmpeg, as its own file kept beside
    the video, ready to send to the B-roll video model as a reference video. Never sped up.
    A piece cut once is found again rather than cut twice, so a clip asked for again is
    sent the very same file."""
    sent: list[ReferenceVideo] = []
    left_out = []
    room = float(MOST_PIECE_SECONDS)
    for piece in wanted:
        if len(sent) == settings.BROLL_MOST_PIECES:
            most = settings.BROLL_MOST_PIECES
            left_out.append(
                LeftOutPiece(piece, f"a clip is sent at most {most} of the pieces wanted")
            )
            continue
        if room < LEAST_PIECE_SECONDS:
            left_out.append(
                LeftOutPiece(
                    piece,
                    f"the pieces before it fill the {MOST_PIECE_SECONDS} seconds of video a "
                    "clip is sent",
                )
            )
            continue
        start, end = piece.start, piece.end
        if end - start < LEAST_PIECE_SECONDS:
            left_out.append(LeftOutPiece(piece, _too_short(piece)))
            continue
        trim: PieceTrim | None = None
        # The backstop: a moment longer than the video model takes is cut to its first
        # seconds, and pieces longer than it takes in all have the last cut to fit; either
        # way the piece says so.
        if end - start > room:
            trim = "moment_over_limit" if room == MOST_PIECE_SECONDS else "pieces_over_limit"
            end = start + room
        room -= end - start
        file = file_store.save_once(
            _piece_name(piece.video, start, end),
            functools.partial(_cut, piece.video, start, end),
            whole=_is_video,
        )
        sent.append(ReferenceVideo(file=file, video=piece.video, start=start, end=end, trim=trim))
    return PiecesCut(sent=sent, left_out=left_out)


def _too_short(piece: PieceWanted) -> str:
    """Why a moment shorter than the video model takes isn't sent. Not approved yet: it is
    left out rather than widened past its own seconds, which the sent list never holds."""
    return (
        f"its moment is {piece.end - piece.start:g} s long, and the video model takes pieces of "
        f"at least {LEAST_PIECE_SECONDS} s"
    )


def _piece_name(video: str, start: float, end: float) -> str:
    """Where the piece of `video` from `start` to `end` is kept: beside the video, named by
    its seconds."""
    return f"{Path(video).with_suffix('')}-piece-{start:g}-{end:g}.mp4"


def _cut(video: str, start: float, end: float) -> bytes:
    """The seconds from `start` to `end` of the video kept as `video`, as an MP4 of the
    picture only: a piece shows how the product moves, and the clip made from it has no
    sound of its own."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video"
        file.write_bytes(file_store.read(video))
        piece = Path(folder) / "piece.mp4"
        ran = subprocess.run(
            [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
            + ["-ss", f"{start:g}", *_READ_ONLY_THE_FILE, "-i", str(file)]
            + ["-t", f"{end - start:g}"]
            + ["-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            + ["-movflags", "+faststart", str(piece)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
        if ran.returncode != 0 or not piece.is_file():
            raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
        return piece.read_bytes()


def _is_video(content: bytes) -> bool:
    """Whether `content` is a whole video ffprobe can read the length of."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video.mp4"
        file.write_bytes(content)
        return _duration(file) is not None
