"""Product videos: the videos the shop placed with the product photos in the product's
gallery, as the photo picker picks them off the page. Each is kept with the job as a file
with its first frame, frames spread over it and its length; or, when no file can be had, as
its link with the page's poster or the player's thumbnail as its first frame. Nothing uses
them yet."""

import mimetypes
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from django.conf import settings

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.gateway import IMAGE_TYPES
from jobs import page
from jobs.models import Job, ProductVideo

# The biggest video file kept. A bigger one is kept as its link.
MAX_VIDEO_BYTES = 50_000_000
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
    the order given, once per video. A page read again keeps its videos afresh."""
    job.videos.all().delete()
    kept: list[ProductVideo] = []
    seen: set[str] = set()
    for player in players:
        src, poster = str(player.get("src") or ""), str(player.get("poster") or "")
        found_by = src or poster
        if not found_by or found_by in seen:
            continue
        seen.add(found_by)
        kept.append(keep_video(job, len(kept) + 1, player))
    return kept


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
            duration, first, frames = looked
            video.file = file_store.save(f"{folder}{extension}", download.content)
            video.duration = duration
            video.first_frame = file_store.save(f"{folder}-first.jpg", first)
            video.frames = [
                file_store.save(f"{folder}-frame-{number}.jpg", frame)
                for number, frame in enumerate(frames, 1)
            ]
    if not video.first_frame:
        video.first_frame = _still(src, poster, folder)
    video.save()
    return video


def _video_file(src: str) -> page.Download | None:
    """The video file at `src`, or None when there is none to keep: a link that isn't a web
    link (blob:), won't download, is over MAX_VIDEO_BYTES, or isn't a video file (a stream's
    playlist, a web page)."""
    try:
        download = page.download(src, max_bytes=MAX_VIDEO_BYTES, what="product video")
    except page.PageUnreadable, OutsideServiceDown:
        return None
    if not download.content_type.startswith("video/"):
        return None
    return download


def _look_at(content: bytes, extension: str) -> tuple[float, bytes, list[bytes]] | None:
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
    return duration, first, frames


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
