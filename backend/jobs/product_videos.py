"""Product videos: the videos the shop placed with the product photos in the product's
gallery, as the photo picker picks them off the page. Each is kept with the job as a file
with its first frame, frames spread over it and its length; or, when no file can be had, as
its link with the page's poster or the player's thumbnail as its first frame. A video in an
embedded player (Vimeo, Wistia, Vidyard...) is downloaded from it with yt-dlp, except a
YouTube one: YouTube refuses cloud machines (docs/limitations.md). A page's videos are
fetched all at once. The watcher (jobs.watcher) watches each file."""

import contextvars
import functools
import logging
import mimetypes
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from django.conf import settings

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import IMAGE_TYPES
from gateway.types import LEAST_PIECE_SECONDS, MOST_PIECE_SECONDS, PieceTrim, ReferenceVideo
from jobs import notices, page
from jobs.models import Job, ProductVideo
from jobs.notices import post_notice
from jobs.threads import closing_its_connection

logger = logging.getLogger(__name__)

# Not a cap on videos: length is the only cut (requirement 23, Julian Oct 27 2026 04:41, "no
# byte cap"; WATCH_VIDEO_MOST_SECONDS). These only stop a link that never ends, such as a
# live stream, from filling the disk or holding up the page read: a video fetched to a file
# past this many bytes or seconds is kept as its link, and the owner is told.
NEVER_ENDING_BYTES = 2_000_000_000
NEVER_ENDING_SECONDS = 600
# Seeking this close to a video's end finds no frame: the last frame starts one frame's
# length before the end, and a slow video's frames are far apart.
LAST_FRAME_MARGIN = 0.5
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
# An embedded player's video: the best at most 720p, with its sound when it has any. A
# file whose height the site doesn't give is taken (`<=?`): most players give it.
_PLAYER_FORMAT = "bv*[height<=?720]+ba/b[height<=?720]/bv*[height<=?720]"
# yt-dlp gives up on a site that sends nothing for this long, and tries a file or piece
# being downloaded again this many times (none, by its default when driven from code):
# once, so a site's passing error doesn't lose the video, and a player that stalls still
# ends, the time limit being only checked as the file comes. Its first fetch of the
# player's link, to find what it holds, is never tried again.
_PLAYER_SOCKET_SECONDS = 20
_PLAYER_TRIES_AGAIN = 1


def save_videos(
    job: Job, players: Sequence[dict[str, Any]], *, page_url: str
) -> list[ProductVideo]:
    """Keep each picked video player, as the page script lists it (src, poster, kind), on
    the product page at `page_url`, in the order given, once per video. A page read again
    only adds the videos the job doesn't have yet, after those it has: one at a link already
    kept is left out, and so is one with no link while the job keeps one with none, which
    can't be told apart. Every video is fetched at the same time. Gives back the videos added."""
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
                page_url,
            )
            for position, player in new
        ]
        return [each.result() for each in kept]


def _storable(src: str) -> bool:
    """Whether a player's link can be kept as its video's link: none, a web link or a blob:
    link, short enough for the column. A data: link holds the video itself."""
    longest = ProductVideo._meta.get_field("source_url").max_length or 0
    return (not src or src.startswith(("http://", "https://", "blob:"))) and len(src) <= longest


def keep_video(job: Job, position: int, player: dict[str, Any], page_url: str) -> ProductVideo:
    """Store one video player's video, on the product page at `page_url`, with the job: its
    file and frames when a file can be had, else its link and any first frame."""
    src, poster = str(player.get("src") or ""), str(player.get("poster") or "")
    kind = (
        ProductVideo.Kind.IFRAME
        if player.get("kind") == ProductVideo.Kind.IFRAME
        else ProductVideo.Kind.VIDEO
    )
    folder = f"jobs/{job.pk}/videos/{position}"
    video = ProductVideo(job=job, position=position, source_url=src, kind=kind)
    with tempfile.TemporaryDirectory() as fetched_to:
        file = _fetched(job, position, kind, src, page_url, Path(fetched_to))
        looked = None if file is None else _look_at(file)
        if file is not None and looked is None:
            post_notice(
                job,
                notices.PAGE_VIDEO,
                f"Your page's video {position} ({src}) was fetched, but it isn't a video that "
                "can be read, so it is kept as its link.",
                Message.Level.INFO,
            )
        if file is not None and looked is not None:
            video.file = file_store.save_file(f"{folder}{file.suffix}", file)
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


def keep_owner_video(job: Job, file: str) -> ProductVideo | None:
    """Keep a video the shop owner sent, already in the file store at `file`, with the job's
    videos at the next position, its length, first frame and frames read from it, so the
    watcher watches it as it watches the page's. The one kept already for the same file, if
    any. None when ffmpeg can't read it as a video."""
    kept = job.videos.filter(file=file).first()
    if kept is not None:
        return kept
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / f"video{Path(file).suffix}"
        copy.write_bytes(file_store.read(file))
        looked = _look_at(copy)
    if looked is None:
        return None
    position = max(job.videos.values_list("position", flat=True), default=0) + 1
    named = f"jobs/{job.pk}/videos/{position}"
    return ProductVideo.objects.create(
        job=job,
        position=position,
        kind=ProductVideo.Kind.VIDEO,
        file=file,
        duration=looked.duration,
        first_frame=file_store.save(f"{named}-first.jpg", looked.first),
        frames=[
            file_store.save(f"{named}-frame-{number}.jpg", frame)
            for number, frame in enumerate(looked.frames, 1)
        ],
    )


def _fetched(
    job: Job, position: int, kind: ProductVideo.Kind, src: str, page_url: str, folder: Path
) -> Path | None:
    """The video's file, fetched into `folder`: a video player's from its link, an embedded
    player's with yt-dlp. None when there is none to keep."""
    if kind == ProductVideo.Kind.VIDEO:
        given = folder / "video"
        content_type = _video_file(job, position, src, given)
        if content_type is None:
            return None
        return given.rename(given.with_suffix(mimetypes.guess_extension(content_type) or ".mp4"))
    if YOUTUBE_ID.search(src):
        post_notice(
            job,
            notices.YOUTUBE_NOT_DOWNLOADED,
            f"Your page's video {position} ({src}) is a YouTube player, which isn't downloaded, "
            "so it is kept as its link and thumbnail.",
            Message.Level.INFO,
        )
        return None
    # A player drawn inside the page has no link of its own to download from: kept as it
    # was, its own design not yet made (#300, "not in this ticket").
    if not src:
        return None
    return _player_file(job, position, src, page_url, folder)


def _player_file(job: Job, position: int, src: str, page_url: str, folder: Path) -> Path | None:
    """Download the video in the embedded player at `src` into `folder` with yt-dlp, as the
    product page at `page_url` shows it, and give back its file: an mp4 at most 720p, with
    its sound. One that won't download, is live, or never ends (NEVER_ENDING_BYTES,
    NEVER_ENDING_SECONDS), is None, with a notice saying why. Only `src` is checked for
    where it points: the links yt-dlp follows from it are not."""
    started = time.monotonic()
    # The bytes of each file yt-dlp is fetching so far: a player's picture and its sound
    # can come as two, and the cap is on both together.
    fetched: dict[str, int] = {}

    def within_limits(progress: dict[str, Any]) -> None:
        # Called as each piece of a file comes, so one stuck or endless player can't hold up
        # the page or fill the disk. Never yt-dlp's own max_filesize: on a stream of pieces
        # it leaves out the pieces too big and keeps the rest, a video cut short.
        fetched[str(progress.get("filename"))] = int(progress.get("downloaded_bytes") or 0)
        if sum(fetched.values()) > NEVER_ENDING_BYTES:
            raise page.TooLongToFetch(
                f"{src} is too big to be a product video, at over {NEVER_ENDING_BYTES:,} bytes."
            )
        if time.monotonic() - started > NEVER_ENDING_SECONDS:
            raise page.TooLongToFetch(
                f"{src} took over {NEVER_ENDING_SECONDS:g} s to download as a product video."
            )

    options: dict[str, Any] = {
        "format": _PLAYER_FORMAT,
        "merge_output_format": "mp4",
        # A single file that isn't an mp4 is made one.
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        # ffmpeg records a live stream until it ends, telling no progress on the way, so
        # the limits above never see it: one is never downloaded.
        "match_filter": lambda info, incomplete: "live" if _is_live(info) else None,
        # A stream of pieces missing one is a video cut short: never kept as if whole.
        "skip_unavailable_fragments": False,
        "outtmpl": str(folder / "video.%(ext)s"),
        "http_headers": {"Referer": page_url},
        "progress_hooks": [within_limits],
        "socket_timeout": _PLAYER_SOCKET_SECONDS,
        "retries": _PLAYER_TRIES_AGAIN,
        # yt-dlp takes ffmpeg only as a path, never a name to look up. With none found, the
        # name is passed on, and yt-dlp's error says so in the notice below.
        "ffmpeg_location": shutil.which(settings.FFMPEG) or settings.FFMPEG,
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YtDlpLog(),
    }
    try:
        # A link to this machine or a private network is never fetched.
        page.check_where_it_points(src)
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(src, download=True)
        if _is_live(info):
            raise _NoFile("it is a live stream, which can't be fetched whole.")
        filepath = (info.get("requested_downloads") or [{}])[0].get("filepath")
        if not filepath:
            raise _NoFile("yt-dlp gave back no file.")
    except Exception as error:
        # yt-dlp reads hundreds of sites, each failing its own way: whatever stops one
        # player's download, the page is read without its file, never quietly.
        logger.warning("Product video %s not downloaded from its player: %s", position, error)
        post_notice(
            job,
            notices.PAGE_VIDEO,
            f"Your page's video {position} ({src}) couldn't be downloaded from its player, so "
            f"it is kept as its link: {error}",
            Message.Level.INFO,
        )
        return None
    return Path(filepath)


def _is_live(info: dict[str, Any]) -> bool:
    """Whether yt-dlp found a live stream, or one yet to start. Most players say neither."""
    return bool(info.get("is_live")) or info.get("live_status") == "is_upcoming"


class _NoFile(Exception):
    """yt-dlp ended without a file, as when it skips a download."""


class _YtDlpLog:
    """Where yt-dlp's own lines go: the app's log, below its errors. A player that fails is
    reported once, by its notice; yt-dlp's error line for it would be a second report."""

    def debug(self, line: str) -> None:
        logger.debug("yt-dlp: %s", line)

    def info(self, line: str) -> None:
        logger.debug("yt-dlp: %s", line)

    def warning(self, line: str) -> None:
        logger.info("yt-dlp: %s", line)

    def error(self, line: str) -> None:
        logger.info("yt-dlp: %s", line)


def _video_file(job: Job, position: int, src: str, file: Path) -> str | None:
    """Fetch the video file at `src` into `file` and give back its type, or None when there
    is none to keep: a link that isn't a web link (blob:), won't download, or isn't a video
    file (a stream's playlist, a web page). One that never ends (NEVER_ENDING_BYTES,
    NEVER_ENDING_SECONDS) is left out, and the owner is told which."""
    try:
        fetched = page.download_to(
            src,
            file,
            max_bytes=NEVER_ENDING_BYTES,
            most_seconds=NEVER_ENDING_SECONDS,
            what="product video",
        )
    except page.TooLongToFetch as error:
        logger.warning("Product video %s left out: %s", position, error)
        post_notice(
            job,
            notices.PAGE_VIDEO,
            f"Your page's video {position} ({src}) couldn't be fetched whole, so it is left "
            f"out of the ad: {error}",
            Message.Level.INFO,
        )
        return None
    except page.PageUnreadable, OutsideServiceDown:
        return None
    return fetched.content_type if fetched.content_type.startswith("video/") else None


@dataclass(frozen=True)
class _Looked:
    duration: float
    first: bytes
    frames: list[bytes]


def _look_at(file: Path) -> _Looked | None:
    """The video's length in seconds, its first frame and FRAMES frames spread over it, as
    JPEGs; None if ffmpeg can't read it as a video."""
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


def frame_second(video: ProductVideo, second: float) -> float:
    """The second a frame is pulled at for `second`: never in the video's last
    LAST_FRAME_MARGIN seconds, where seeking finds no frame to pull."""
    return max(0.0, min(second, (video.duration or 0) - LAST_FRAME_MARGIN))


def frames_of(video: ProductVideo, seconds: Sequence[float]) -> list[bytes]:
    """The frames at `seconds` into the video's file (each at its frame_second), at full
    size, as JPEGs, pulled from one copy of the file."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / f"video{Path(video.file).suffix}"
        file.write_bytes(file_store.read(video.file))
        return [_frame(file, frame_second(video, second)) for second in seconds]


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
        trim: PieceTrim | None = None
        # A moment can run past its video's end, as seconds counted to the second can: the
        # piece holds, and says, only the seconds there are.
        length = _length_of(piece.video)
        if end > length:
            trim, end = "video_ended", length
        if end - start < LEAST_PIECE_SECONDS:
            left_out.append(LeftOutPiece(piece, _too_short(start, end)))
            continue
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


def _too_short(start: float, end: float) -> str:
    """Why a moment holding fewer seconds of video than the video model takes isn't sent.
    Not approved yet: it is left out rather than widened past its own seconds, which the
    sent list never holds."""
    return (
        f"it holds {max(0.0, end - start):g} s of video, and the video model takes pieces of "
        f"at least {LEAST_PIECE_SECONDS} s"
    )


def _length_of(video: str) -> float:
    """How many seconds long the video kept as `video` is. Raises RuntimeError for one
    ffprobe can't read."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video"
        file.write_bytes(file_store.read(video))
        length = _duration(file)
    if length is None:
        raise RuntimeError(f"ffprobe couldn't read the length of the page video {video}")
    return length


def _piece_name(video: str, start: float, end: float) -> str:
    """Where the piece of `video` from `start` to `end` is kept: beside the video, named by
    its seconds."""
    return f"{Path(video).with_suffix('')}-piece-{start:g}-{end:g}.mp4"


def cut_part(video: str, start: float, end: float, *, sound: bool) -> str:
    """The seconds from `start` to `end` of the video kept as `video`, with its sound or
    without it, as an MP4 kept beside it: made once. Gives its key."""
    name = f"{Path(video).with_suffix('')}-part-{start:g}-{end:g}{'' if sound else '-silent'}.mp4"
    return file_store.save_once(
        name, functools.partial(_cut, video, start, end, sound=sound), whole=_is_video
    )


def _cut(video: str, start: float, end: float, *, sound: bool = False) -> bytes:
    """The seconds from `start` to `end` of the video kept as `video`, as an MP4, of the
    picture only unless `sound`: a piece shows how the product moves, and the clip made from
    it has no sound of its own."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video"
        file.write_bytes(file_store.read(video))
        piece = Path(folder) / "piece.mp4"
        ran = subprocess.run(
            [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
            + ["-ss", f"{start:g}", *_READ_ONLY_THE_FILE, "-i", str(file)]
            + ["-t", f"{end - start:g}"]
            + (["-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac"] if sound else ["-an"])
            + ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
            + ["-movflags", "+faststart", str(piece)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
        if ran.returncode != 0 or not piece.is_file():
            raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
        # Never a file that holds no video, sent and paid for as if it did.
        if _duration(piece) is None:
            raise RuntimeError(f"ffmpeg cut {start:g}-{end:g} s of {video} into no video")
        return piece.read_bytes()


def _is_video(content: bytes) -> bool:
    """Whether `content` is a whole video ffprobe can read the length of."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video.mp4"
        file.write_bytes(content)
        return _duration(file) is not None
