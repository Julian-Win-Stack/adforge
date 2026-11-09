"""A planned still shot is filmed as a small camera move pinned at both ends: its first and
last frames are the starting picture and a crop cut from its exact pixels, so nothing in the
scene can move on its own (physics try, Nov 9 2026: H3 doesn't make still shots; left with
nothing to move, it moved a drawer)."""

import io

import PIL.Image

from adforge import file_store
from agents.scene_forms import NOT_STILL, PULL_BACK, PUSH_IN, StillShot

__all__ = ["NOT_STILL", "PULL_BACK", "PUSH_IN", "StillShot", "crop"]

# The crop keeps this share of the picture's width and height: about a third closer. A small
# move only, as a bigger crop goes soft once it is scaled back up.
CROP_SHARE = 2 / 3


def crop(key: str, centre_x: int, centre_y: int) -> str:
    """Cut a crop of the picture `key` around the point `centre_x`, `centre_y` (percent of
    its width from the left and its height from the top), kept inside the picture, scaled
    back to the picture's size. Returns the crop's key."""
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        picture = opened.convert("RGB")
    width, height = picture.size
    crop_width, crop_height = round(width * CROP_SHARE), round(height * CROP_SHARE)
    left = round(width * min(max(centre_x, 0), 100) / 100 - crop_width / 2)
    top = round(height * min(max(centre_y, 0), 100) / 100 - crop_height / 2)
    left = min(max(left, 0), width - crop_width)
    top = min(max(top, 0), height - crop_height)
    cut = picture.crop((left, top, left + crop_width, top + crop_height)).resize(
        (width, height), PIL.Image.Resampling.LANCZOS
    )
    out = io.BytesIO()
    cut.save(out, format="PNG")
    return file_store.save("still_shot_crop.png", out.getvalue())
