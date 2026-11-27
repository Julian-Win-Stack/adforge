"""A silent scene's starting picture made from the shop's own product photo (#772): the photo
fitted to the tall frame by code, its pixels kept. A photo a little too wide or tall is
cropped; a much wider one (a square gallery photo) is put whole in the tall frame and the
picture model extends the background above and below it, then code pastes the photo's own
pixels back over the middle, so the product is never redrawn."""

import io
from dataclasses import dataclass

import PIL.Image

from adforge import file_store
from gateway.gateway import edit_picture
from jobs.models import Job

# The tall frame every clip is filmed in.
FRAME_WIDTH, FRAME_HEIGHT = 1080, 1920
TALL = FRAME_WIDTH / FRAME_HEIGHT
# A crop may keep as little as this share of the photo's width before the background is
# extended instead: cropping a square photo to the tall frame would keep only 56% of it and
# could cut the product off.
LEAST_WIDTH_KEPT = 0.8
# A photo narrower than this can't fill the frame without going soft.
SMALLEST_WIDTH = 400

EXTEND_PROMPT = (
    "Image 1 is a product photo placed in the middle of a tall frame, with empty grey bands "
    "above and below it. Fill only the empty bands: continue the photo's own background, "
    "surface, light and colours outward, seamlessly, so the whole frame reads as one tall "
    "photo. Add no new objects, no text, no people and no second product. Leave the photo in "
    "the middle exactly as it is."
)


class PhotoNotFitted(Exception):
    """The photo can't be fitted to the tall frame: say why."""


class PixelsNotPasted(Exception):
    """The extended picture came back in a shape the photo's pixels can't be pasted on."""


@dataclass(frozen=True)
class Fitted:
    """The starting picture made from the photo, and how: "cropped" by code alone, or
    "extended" by the picture model with the photo's pixels pasted back."""

    file: str
    how: str


def _opened(key: str) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        return opened.convert("RGB")


def _saved(picture: PIL.Image.Image, name: str) -> str:
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return file_store.save(name, out.getvalue())


def _start(size: int, kept: int, centre: int) -> int:
    start = round(size * min(max(centre, 0), 100) / 100 - kept / 2)
    return min(max(start, 0), size - kept)


def fit(job: Job, photo: str, focus_x: int = 50, focus_y: int = 50) -> Fitted:
    """The photo at `photo` fitted to the tall frame, cropped around `focus_x`, `focus_y`
    (percent of its width and height) where a crop keeps enough of it, else extended."""
    picture = _opened(photo)
    width, height = picture.size
    if width < SMALLEST_WIDTH:
        raise PhotoNotFitted(f"it is only {width} pixels wide, too small to fill a tall frame")
    ratio = width / height
    if ratio <= TALL:
        # Taller than the frame: keep the whole width, crop the height.
        kept = round(width / TALL)
        top = _start(height, kept, focus_y)
        cut = picture.crop((0, top, width, top + kept))
        return Fitted(
            _saved(cut.resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS), "rp.png"),
            "cropped",
        )
    kept_width = round(height * TALL)
    if kept_width >= width * LEAST_WIDTH_KEPT:
        left = _start(width, kept_width, focus_x)
        cut = picture.crop((left, 0, left + kept_width, height))
        return Fitted(
            _saved(cut.resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS), "rp.png"),
            "cropped",
        )
    return Fitted(_extended(job, picture), "extended")


def _extended(job: Job, picture: PIL.Image.Image) -> str:
    """`picture` whole across the tall frame's width, the bands above and below drawn by the
    picture model, and its own pixels pasted back over the middle."""
    shown_height = round(picture.height * FRAME_WIDTH / picture.width)
    shown = picture.resize((FRAME_WIDTH, shown_height), PIL.Image.Resampling.LANCZOS)
    top = (FRAME_HEIGHT - shown_height) // 2
    canvas = PIL.Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (128, 128, 128))
    canvas.paste(shown, (0, top))
    key = edit_picture(
        job=job,
        purpose="draw_scene_picture",
        prompt=EXTEND_PROMPT,
        pictures=[_saved(canvas, "rp-canvas.png")],
        keep_shape=True,
    )
    drawn = _opened(key)
    if abs(drawn.width / drawn.height - TALL) > 0.05:
        raise PixelsNotPasted(
            f"the extended picture came back {drawn.width}x{drawn.height}, not tall"
        )
    drawn = drawn.resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS)
    # The photo's own pixels go back over the middle, feathered over a few rows at each seam
    # so the join doesn't show as a line.
    mask = PIL.Image.new("L", shown.size, 255)
    feather = min(24, shown_height // 10)
    for row in range(feather):
        value = round(255 * (row + 1) / (feather + 1))
        for y in (row, shown_height - 1 - row):
            mask.paste(value, (0, y, FRAME_WIDTH, y + 1))
    drawn.paste(shown, (0, top), mask)
    return _saved(drawn, "rp-extended.png")
