"""Product videos: the videos the shop placed with the product photos in the product's
gallery, as the photo picker picks them off the page. Each is kept with the job as a file
with its first frame, frames spread over it, its length and the frames it is thinned to
(video_frames); or, when no file can be had, as its link with the page's poster or the
player's thumbnail as its first frame. What a file says is written down as its transcript.
The video reader (video_reader) reads them for the planner."""

import logging
import mimetypes
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat.models import Message
from gateway.gateway import IMAGE_TYPES, transcribe
from jobs import page, video_frames
from jobs.models import Job, ProductVideo
from jobs.notices import post_notice

logger = logging.getLogger(__name__)

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
_SCENE_SCORE = re.compile(r"lavfi\.scene_score=([0-9.]+)")
# A video's transcript when nothing is said in it.
MUSIC_ONLY = "music only"


def save_videos(job: Job, players: Sequence[dict[str, Any]]) -> list[ProductVideo]:
    """Keep each picked video player, as the page script lists it (src, poster, kind), in
    the order given, once per video. A page read again keeps its videos afresh."""
    job.videos.all().delete()
    kept: list[ProductVideo] = []
    seen: set[str] = set()
    for player in players:
        src, poster = str(player.get("src") or ""), str(player.get("poster") or "")
        found_by = src or poster
        if not found_by or found_by in seen or not _storable(src):
            continue
        seen.add(found_by)
        kept.append(keep_video(job, len(kept) + 1, player))
    return kept


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
            video.frames_picked = [
                {"key": file_store.save(f"{folder}-pick-{number}.jpg", frame), "second": second}
                for number, (second, frame) in enumerate(looked.picked, 1)
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


@dataclass(frozen=True)
class _Looked:
    duration: float
    first: bytes
    frames: list[bytes]
    # The video thinned (video_frames): each frame kept, with the second it is at.
    picked: list[tuple[float, bytes]]


def _look_at(content: bytes, extension: str) -> _Looked | None:
    """The video's length in seconds, its first frame, FRAMES frames spread over it and the
    frames it is thinned to, as JPEGs; None if ffmpeg can't read it as a video."""
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
        return _Looked(duration, first, frames, _thinned(file, Path(folder) / "frames"))


def _thinned(file: Path, folder: Path) -> list[tuple[float, bytes]]:
    """The video thinned to the frames worth looking at, each with its second; none when
    that fails, so the video is kept but not read."""
    try:
        every_frame, scene_scores = _every_frame(file, folder)
        return [
            (number / video_frames.FRAMES_PER_SECOND, every_frame[number].read_bytes())
            for number in video_frames.thin(every_frame, scene_scores)
        ]
    except Exception:
        logger.exception("Product video %s couldn't be thinned", file)
        return []


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


def _every_frame(file: Path, folder: Path) -> tuple[list[Path], list[float]]:
    """The video's frames at full size, taken FRAMES_PER_SECOND times a second, as JPEG
    files in `folder`, first to last; and ffmpeg's scene score between each frame and the
    one before it."""
    folder.mkdir()
    scores = folder / "scene-scores.txt"
    every = (
        f"fps={video_frames.FRAMES_PER_SECOND},select='gte(scene,0)',"
        f"metadata=print:key=lavfi.scene_score:file={scores}"
    )
    ran = subprocess.run(
        [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
        + [*_READ_ONLY_THE_FILE, "-i", str(file), "-vf", every, "-fps_mode", "passthrough"]
        + ["-q:v", "2", str(folder / "%05d.jpg")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
    )
    frames = sorted(folder.glob("*.jpg"))
    if ran.returncode != 0 or not frames:
        raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
    return frames, [float(score) for score in _SCENE_SCORE.findall(scores.read_text())]


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


def transcribe_videos(job: Job) -> list[str]:
    """Write down the words said in each of the job's video files, through the app's
    speech-to-text, as the video's transcript: MUSIC_ONLY when nothing is said. A video
    whose sound can't be written down is left without one, and the page is read as ever.
    Gives back a line for the producer for each such video."""
    problems: list[str] = []
    for video in job.videos.exclude(file=""):
        try:
            video.transcript = _transcript(job, video)
        except Exception as error:
            # The failed call is recorded with its error; the ad is planned without it.
            logger.exception("Product video %s couldn't be written down", video.pk)
            problems.append(
                f"What the product's video {video.position} says couldn't be written down "
                f"({error}), so the ad is planned without it."
            )
            post_notice(
                job,
                f"Your product's video {video.position} couldn't be heard, so the ad is "
                "planned without what it says.",
                Message.Level.PROBLEM,
            )
            continue
        video.save(update_fields=["transcript"])
    return problems


def _transcript(job: Job, video: ProductVideo) -> str:
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / f"video{Path(video.file).suffix}"
        file.write_bytes(file_store.read(video.file))
        # A video with no sound track has nothing said in it.
        if not _has_sound(file):
            return MUSIC_ONLY
        sound = Path(folder) / "sound.wav"
        _sound(file, sound)
        audio_key = file_store.save(
            f"jobs/{job.pk}/videos/{video.position}-sound.wav", sound.read_bytes()
        )
    heard = transcribe(job=job, purpose="transcribe_video", audio_key=audio_key)
    return heard.text.strip() or MUSIC_ONLY


def _has_sound(file: Path) -> bool:
    probed = subprocess.run(
        [settings.FFPROBE, "-v", "error", *_READ_ONLY_THE_FILE, "-select_streams", "a"]
        + ["-show_entries", "stream=index", "-of", "csv=p=0", str(file)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    if probed.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {probed.stderr[-2000:]}")
    return bool(probed.stdout.strip())


def _sound(file: Path, wav: Path) -> None:
    """Write the video's sound to `wav`, as a mono WAV speech-to-text hears well."""
    ran = subprocess.run(
        [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
        + [*_READ_ONLY_THE_FILE, "-i", str(file), "-vn", "-ac", "1", "-ar", "16000"]
        + ["-c:a", "pcm_s16le", str(wav)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
    )
    if ran.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {ran.stderr.decode(errors='replace')[-2000:]}")
