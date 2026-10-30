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
from typing import Any, NamedTuple

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
from django.conf import settings

from adforge.retry import OutsideServiceDown

from . import page, photos
from .product_videos import YOUTUBE_ID, YOUTUBE_THUMBNAIL

# Official photos and video frames go 2 by 2 in tiles this many px a side; hidden pictures,
# which are many, 3 by 3 in smaller tiles.
RECORD_GRID = (2, 600)
HIDDEN_GRID = (3, 400)
VIDEO_GRID = (2, 600)
# Each kind of grid by its name, the start of its label, in the order the picker is shown them.
GRID_SHAPES = {
    "Official photos": RECORD_GRID,
    "Hidden pictures": HIDDEN_GRID,
    "Video players": VIDEO_GRID,
}
# Pictures are fetched this many at a time.
FETCHING_TOGETHER = 8
# ffmpeg may take at most this long to get a video's first frame.
FRAME_SECONDS = 120
# The biggest video fetched only for its first frame, for the picker to see: a bigger one is
# listed without a picture. Not a cap on the videos kept (product_videos.NEVER_ENDING_BYTES).
FRAME_VIDEO_BYTES = 50_000_000
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
            *grids("Official photos", record_tiles, *GRID_SHAPES["Official photos"]),
            *grids("Hidden pictures", hidden_tiles, *GRID_SHAPES["Hidden pictures"]),
            *grids("Video players", video_tiles, *GRID_SHAPES["Video players"]),
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


def measured(marks: dict[str, Any]) -> dict[str, Any]:
    """`marks` with each numbered picture whose size the page reader doesn't know
    (photos.size_unknown) fetched at its biggest size, as hidden pictures are, and its size
    kept as its `measured`: so one under photos.SMALLEST_PHOTO px is never listed for the
    picker (photos.too_small). One that can't be fetched or opened stays of unknown size."""
    pictures = [picture for picture in marks.get("imgs") or [] if isinstance(picture, dict)]
    unknown = [
        index
        for index, picture in enumerate(pictures)
        if isinstance(picture.get("n"), int) and photos.size_unknown(picture)
    ]
    sizes = _fetched(
        _size_at,
        [(photos.best_url(pictures[i]), photos.widest_url(pictures[i])) for i in unknown],
    )
    for index, size in zip(unknown, sizes, strict=True):
        if size is not None:
            pictures[index] = {**pictures[index], "measured": list(size)}
    return {**marks, "imgs": pictures}


def _size_at(links: tuple[str, str]) -> tuple[int, int] | None:
    """The size of the picture at its biggest size, else at the link as given; None when
    neither can be fetched and opened."""
    for url in dict.fromkeys(links):
        try:
            fetched = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
        except page.PageUnreadable, OutsideServiceDown:
            continue
        if (size := photos.size_of(fetched.content)) is not None:
            return size
    return None


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
        video = page.download(src, max_bytes=FRAME_VIDEO_BYTES, what="product video")
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


def grid(
    tiles: list[tuple[str, bytes]], across: int, side: int, *, label_under: bool = False
) -> bytes:
    """`tiles` in one JPEG, `across` to a row, each picture fitted on white in a square
    `side` px a side with its label on a black bar: above it, or with `label_under` under it,
    the label wrapped onto as many lines as it takes, every bar in the grid as tall as the
    tallest."""
    font_size = _font_size(side, label_under=label_under)
    font = PIL.ImageFont.load_default(size=font_size)
    draw = PIL.ImageDraw.Draw(PIL.Image.new("RGB", (1, 1)))
    texts = [
        _wrapped(label, font, side - 16, draw) if label_under else [label] for label, _ in tiles
    ]
    line = font_size + 6
    bar = _bar(max(len(lines) for lines in texts), font_size) if texts else 0
    rows = (len(tiles) + across - 1) // across
    sheet = PIL.Image.new(
        "RGB",
        (across * side + (across + 1) * _GAP, rows * (side + bar) + (rows + 1) * _GAP),
        "#888",
    )
    draw = PIL.ImageDraw.Draw(sheet)
    for k, ((_, content), lines) in enumerate(zip(tiles, texts, strict=True)):
        x, y = _corner(k, across, side, bar)
        top = y + side if label_under else y
        draw.rectangle((x, top, x + side - 1, top + bar - 1), fill="black")
        for n, text in enumerate(lines):
            draw.text((x + 8, top + 4 + n * line), text, fill="white", font=font)
        with PIL.Image.open(io.BytesIO(content)) as opened:
            # A see-through background counts as white.
            flat = PIL.Image.new("RGBA", opened.size, "white")
            flat.alpha_composite(opened.convert("RGBA"))
            picture = flat.convert("RGB")
        picture.thumbnail((side, side))
        below = y if label_under else y + bar
        sheet.paste(
            picture, (x + (side - picture.width) // 2, below + (side - picture.height) // 2)
        )
    saved = io.BytesIO()
    sheet.save(saved, "JPEG", quality=85)
    return saved.getvalue()


# The grey gap between and around a grid's tiles, in px.
_GAP = 8


def _font_size(side: int, *, label_under: bool) -> int:
    return max(18, side // 22) if label_under else max(22, side // 14)


def _bar(lines: int, font_size: int) -> int:
    """How tall a black bar holding `lines` lines of labels is, in px."""
    return lines * (font_size + 6) + 8


def _corner(k: int, across: int, side: int, bar: int) -> tuple[int, int]:
    """Where the `k`th tile of a grid, its bar included, starts."""
    return _GAP + (k % across) * (side + _GAP), _GAP + (k // across) * (side + bar + _GAP)


class CutTile(NamedTuple):
    """A tile cut back out of the picker's grid: the grid's kind, its label and its picture."""

    kind: str
    label: str
    picture: bytes


def kind_of(label: str) -> str:
    """The kind of the picker's grid labelled `label`, as `grids` labels it."""
    return label.partition(": ")[0]


def tiles_cut_from(label: str, content: bytes) -> list[CutTile]:
    """The tiles of the picker's grid labelled `label`, as `grids` drew it, each picture cut
    back out of `content` without its bar, for a grid drawn again with fewer of them."""
    kind = kind_of(label)
    across, side = GRID_SHAPES[kind]
    # A label over a picture is one line on its bar, as `grid` draws it.
    bar = _bar(1, _font_size(side, label_under=False))
    cut = []
    with PIL.Image.open(io.BytesIO(content)) as sheet:
        for k, tile in enumerate(tiles_of(label)):
            x, y = _corner(k, across, side, bar)
            saved = io.BytesIO()
            sheet.crop((x, y + bar, x + side, y + bar + side)).save(saved, "JPEG", quality=95)
            cut.append(CutTile(kind, tile, saved.getvalue()))
    return cut


def _wrapped(
    text: str,
    font: PIL.ImageFont.FreeTypeFont | PIL.ImageFont.ImageFont,
    width: int,
    draw: PIL.ImageDraw.ImageDraw,
) -> list[str]:
    """`text` broken between words into lines no wider than `width` px, a word wider than
    that on a line of its own."""
    lines: list[str] = []
    for word in text.split():
        if lines and draw.textlength(f"{lines[-1]} {word}", font=font) <= width:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines or [""]


def _number(player: dict[str, Any], key: str) -> int:
    value = player.get(key)
    return round(value) if isinstance(value, int | float) else 0
