"""The pictures the photo picker is shown beside the page's screenshot, packed into labelled
grids: the model takes at most 50 pictures a call, and a page can have more. Each tile has
its label (R1, I12 (hidden), V3) on a black bar above it, and each grid is labelled with
the tiles in it. Shown are the shop's official photos from its record of the product, the
numbered pictures the screenshot couldn't draw because a scroll box or carousel slide hides
them, and the first frame of each of the page's video players.

Ported from the tested harness: runs/photo-video-pick-test/run.py (grid, grids, first_frame,
pick)."""

import contextvars
import io
import re
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from django.conf import settings

from adforge.retry import OutsideServiceDown

from . import page, photos
from .product_videos import MAX_VIDEO_BYTES, YOUTUBE_ID, YOUTUBE_THUMBNAIL

# Official photos and video frames go 2 by 2 in tiles this many px a side; hidden pictures,
# which are many, 3 by 3 in smaller tiles.
RECORD_GRID = (2, 600)
HIDDEN_GRID = (3, 400)
VIDEO_GRID = (2, 600)
# Pictures are fetched this many at a time.
FETCHING_TOGETHER = 8
# ffmpeg may take at most this long to get a video's first frame.
FRAME_SECONDS = 120
# Embedded players whose video file the page doesn't link to: their first frame can't be got.
NO_FRAME_PLAYERS = re.compile(r"vimeo|wistia|vidyard|tolstoy|videowise", re.I)


@dataclass(frozen=True)
class Shown:
    """What the picker is shown beside the screenshot: each grid's label and picture, and the
    lines naming what is in them."""

    grids: list[tuple[str, bytes]]
    lines: photos.ShownLines


def beside_the_screenshot(record: dict[str, Any] | None, marks: dict[str, Any]) -> Shown:
    """The official photos, the hidden pictures and the video players' first frames for the
    picker, in that order, in grids. A picture that can't be fetched or opened isn't shown;
    a video player with no frame is still listed by its line."""
    record_urls = photos.record_photo_urls(record) if record else []
    hidden = photos.hidden(marks)
    players = videos(marks)
    record_photos = _fetched(
        _sized_up_picture, [(photos.full_size(url), url) for url in record_urls]
    )
    hidden_photos = _fetched(
        _sized_up_picture,
        [(photos.best_url(picture), photos.widest_url(picture)) for picture in hidden],
    )
    frames = _fetched(first_frame, players)
    record_tiles = [
        (f"R{number}", content)
        for number, content in enumerate(record_photos, 1)
        if content is not None
    ]
    hidden_tiles, hidden_lines = [], []
    for picture, content in zip(hidden, hidden_photos, strict=True):
        if content is not None:
            hidden_tiles.append((f"I{picture['n']} (hidden)", content))
            hidden_lines.append(photos.hidden_line(picture, marks))
    video_tiles, video_lines = [], []
    for player, (frame, how) in zip(players, frames, strict=True):
        if frame is not None:
            video_tiles.append((f"V{player['n']}", frame))
        video_lines.append(player_line(player, marks, frame is not None, how))
    return Shown(
        grids=[
            *grids("Official photos", record_tiles, *RECORD_GRID),
            *grids("Hidden pictures", hidden_tiles, *HIDDEN_GRID),
            *grids("Video players", video_tiles, *VIDEO_GRID),
        ],
        lines=photos.ShownLines(
            hidden_pictures="\n".join(hidden_lines),
            official_photos="\n".join(
                f"R{number}: {url[:120]}"
                for number, (url, content) in enumerate(
                    zip(record_urls, record_photos, strict=True), 1
                )
                if content is not None
            ),
            video_players="\n".join(video_lines),
        ),
    )


def videos(marks: dict[str, Any]) -> list[dict[str, Any]]:
    """The page's video players the picker is shown: each video once, the drawn player kept
    over a hidden one, in page order, at most MOST_VIDEOS."""
    players = [
        player
        for player in marks.get("vids") or []
        if isinstance(player, dict) and isinstance(player.get("n"), int)
    ]
    kept: dict[str, dict[str, Any]] = {}
    for player in sorted(players, key=lambda p: (not p.get("shown", True), p["n"])):
        kept.setdefault(str(player.get("src") or f"V{player['n']}"), player)
    return sorted(kept.values(), key=lambda player: player["n"])[: photos.MOST_VIDEOS]


def player_line(player: dict[str, Any], marks: dict[str, Any], framed: bool, how: str) -> str:
    """What the picker is told of a video player: its kind, size, where it sits and with
    which drawn pictures, how it plays, and how its picture was got."""
    box = player.get("box") or 0
    mates = photos.box_mates(marks, box)
    flags = ", ".join(
        flag for flag in ("autoplay", "loop", "muted", "controls") if player.get(flag)
    )
    return (
        f"V{player['n']}: {player.get('kind') or 'video'}, "
        f"{_number(player, 'w')}x{_number(player, 'h')} px at {_number(player, 'y')} px down "
        f"the page, {'drawn' if player.get('shown', True) else 'hidden'}, box {box} "
        + (
            f"shared with pictures {', '.join(mates)}; "
            if mates
            else "(no drawn picture shares it); "
        )
        + (f"{flags}; " if flags else "")
        + (f"picture: {how}" if framed else f"NO PICTURE ({how})")
        + (f"; aria: {str(player['aria'])[:80]}" if player.get("aria") else "")
    )


def first_frame(player: dict[str, Any]) -> tuple[bytes | None, str]:
    """A video player's first picture and how it was got: its poster; for YouTube the
    video's own thumbnail; else the frame half a second into its file, fetched as any page
    picture is. None for an embedded player whose file the page doesn't link to."""
    poster = str(player.get("poster") or "")
    if poster and (content := _picture(poster)) is not None:
        return content, "poster"
    src = str(player.get("src") or "")
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith(("http://", "https://")):
        return None, "no link to its video"
    if found := YOUTUBE_ID.search(src):
        return _picture(YOUTUBE_THUMBNAIL.format(id=found[1])), "youtube thumbnail"
    if NO_FRAME_PLAYERS.search(src):
        return None, "embedded player"
    try:
        video = page.download(src, max_bytes=MAX_VIDEO_BYTES, what="product video")
    except page.PageUnreadable, OutsideServiceDown:
        return None, "video couldn't be fetched"
    frame = _frame_at_half_a_second(video.content)
    return frame, "first frame" if frame is not None else "its frame couldn't be read"


def _frame_at_half_a_second(video: bytes) -> bytes | None:
    """The frame half a second into `video`, else its first, as a JPEG. ffmpeg reads only
    the file here: a playlist naming other links is never followed."""
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "video"
        file.write_bytes(video)
        for start in ("0.5", "0"):
            try:
                ran = subprocess.run(
                    [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
                    + ["-protocol_whitelist", "file", "-ss", start, "-i", str(file)]
                    + ["-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=FRAME_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return None
            if ran.returncode == 0 and _opens(ran.stdout):
                return ran.stdout
    return None


def _sized_up_picture(links: tuple[str, str]) -> bytes | None:
    """The picture at its biggest size, else at the link as given."""
    biggest, as_given = links
    found = _picture(biggest)
    return found if found is not None or as_given == biggest else _picture(as_given)


def _picture(url: str) -> bytes | None:
    """The picture at `url`, or None if it can't be fetched or opened, or is under
    photos.SMALLEST_PHOTO px on a side, too small to be a product photo."""
    try:
        fetched = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
    except page.PageUnreadable, OutsideServiceDown:
        return None
    # Any picture that opens will do: the grid is drawn afresh as a JPEG.
    if not _opens(fetched.content):
        return None
    size = photos.size_of(fetched.content)
    if size is None or min(size) < photos.SMALLEST_PHOTO:
        return None
    return fetched.content


def _fetched[T, Got](fetch: Callable[[T], Got], each: list[T]) -> list[Got]:
    """`fetch` of each, several at a time, in order."""
    with ThreadPoolExecutor(max_workers=FETCHING_TOGETHER) as pool:
        asked = [pool.submit(contextvars.copy_context().run, fetch, one) for one in each]
        return [ask.result() for ask in asked]


def _opens(content: bytes) -> bool:
    try:
        with PIL.Image.open(io.BytesIO(content)) as picture:
            picture.load()
    except OSError, ValueError, PIL.Image.DecompressionBombError:
        return False
    return True


GRID_TILES = re.compile(r"^[^:]+: (.*) \(each labelled in its black bar\)$")


def tiles_of(label: str) -> list[str]:
    """The labels of the tiles in the grid labelled `label`, as `grids` labels it."""
    found = GRID_TILES.match(label)
    return found[1].split(", ") if found else []


def without_tiles(lines: photos.ShownLines, tiles: list[str]) -> photos.ShownLines:
    """`lines` without the lines naming `tiles`, for a picker that isn't shown them."""

    def kept(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if line.partition(": ")[0] not in tiles)

    return photos.ShownLines(
        hidden_pictures=kept(lines.hidden_pictures),
        official_photos=kept(lines.official_photos),
        video_players=kept(lines.video_players),
    )


def grids(
    kind: str, tiles: list[tuple[str, bytes]], across: int, side: int
) -> list[tuple[str, bytes]]:
    """`tiles` packed `across` by `across` into grids, each labelled with its kind and its
    tiles' labels."""
    per_grid = across * across
    return [
        (
            f"{kind}: {', '.join(label for label, _ in tiles[start : start + per_grid])} "
            "(each labelled in its black bar)",
            grid(tiles[start : start + per_grid], across, side),
        )
        for start in range(0, len(tiles), per_grid)
    ]


def grid(tiles: list[tuple[str, bytes]], across: int, side: int) -> bytes:
    """`tiles` in one JPEG, `across` to a row, each picture fitted on white in a square
    `side` px a side under a black bar with its label."""
    font_size = max(22, side // 14)
    font = PIL.ImageFont.load_default(size=font_size)
    bar = font_size + 14
    rows = (len(tiles) + across - 1) // across
    gap = 8
    sheet = PIL.Image.new(
        "RGB",
        (across * side + (across + 1) * gap, rows * (side + bar) + (rows + 1) * gap),
        "#888",
    )
    draw = PIL.ImageDraw.Draw(sheet)
    for k, (label, content) in enumerate(tiles):
        x, y = gap + (k % across) * (side + gap), gap + (k // across) * (side + bar + gap)
        draw.rectangle((x, y, x + side - 1, y + bar - 1), fill="black")
        draw.text((x + 8, y + 6), label, fill="white", font=font)
        with PIL.Image.open(io.BytesIO(content)) as opened:
            # A see-through background counts as white.
            flat = PIL.Image.new("RGBA", opened.size, "white")
            flat.alpha_composite(opened.convert("RGBA"))
            picture = flat.convert("RGB")
        picture.thumbnail((side, side))
        sheet.paste(
            picture, (x + (side - picture.width) // 2, y + bar + (side - picture.height) // 2)
        )
    saved = io.BytesIO()
    sheet.save(saved, "JPEG", quality=85)
    return saved.getvalue()


def _number(player: dict[str, Any], key: str) -> int:
    value = player.get(key)
    return round(value) if isinstance(value, int | float) else 0
