"""A planned still shot is filmed as a small camera move pinned at both ends: one end is the
starting picture, the other a crop cut from its own pixels, so nothing in the scene can move
by itself (physics try, Nov 9 2026: H3 won't film a still shot; left with nothing to move,
it slid a drawer open)."""

import io

import PIL.Image

from adforge import file_store
from agents.scene_forms import NOT_STILL, PULL_BACK, PUSH_IN, StillShot

__all__ = ["NOT_STILL", "PULL_BACK", "PUSH_IN", "StillShot", "crop", "frames"]

# The crop keeps this share of the picture's width and height, about a third closer: a small
# move only, as a tighter crop goes soft once it is scaled back up.
CROP_SHARE = 2 / 3


def _start(size: int, kept: int, centre: int) -> int:
    """Where a span of `kept` pixels centred at `centre` percent of `size` starts, kept
    inside the picture."""
    start = round(size * min(max(centre, 0), 100) / 100 - kept / 2)
    return min(max(start, 0), size - kept)


def crop(key: str, centre_x: int, centre_y: int) -> str:
    """A crop of the picture `key` around `centre_x`, `centre_y` (percent of its width from
    the left and of its height from the top), kept inside the picture and scaled back to its
    size. Returns the crop's key."""
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        picture = opened.convert("RGB")
    width, height = picture.size
    kept_width, kept_height = round(width * CROP_SHARE), round(height * CROP_SHARE)
    left = _start(width, kept_width, centre_x)
    top = _start(height, kept_height, centre_y)
    cut = picture.crop((left, top, left + kept_width, top + kept_height)).resize(
        (width, height), PIL.Image.Resampling.LANCZOS
    )
    out = io.BytesIO()
    cut.save(out, format="PNG")
    return file_store.save("still_shot_crop.png", out.getvalue())


def frames(picture: str, shot: StillShot, centre_x: int, centre_y: int) -> tuple[str, str]:
    """The clip's first and last frames for a still shot of `picture`: a pull back starts on
    the crop and ends on the whole picture; a push in is the other way round."""
    assert shot != NOT_STILL
    cut = crop(picture, centre_x, centre_y)
    return (cut, picture) if shot == PULL_BACK else (picture, cut)
