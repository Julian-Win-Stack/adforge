"""Fitting a shop's own photo to the tall frame by cropping keeps its pixels (#772)."""

import io

import PIL.ExifTags
import PIL.Image
import PIL.ImageChops
import pytest

from adforge import file_store
from jobs import real_photo
from jobs.assembly import FRAME_HEIGHT, FRAME_WIDTH


def saved(picture: PIL.Image.Image) -> str:
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return file_store.save("photo.png", out.getvalue())


def opened(key: str) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as picture:
        return picture.convert("RGB")


def striped(width: int, height: int) -> PIL.Image.Image:
    """A photo whose every column has its own colour, so a crop shows where it was taken."""
    picture = PIL.Image.new("RGB", (width, height))
    for x in range(width):
        picture.paste((x % 256, (x * 7) % 256, 90), (x, 0, x + 1, height))
    return picture


@pytest.mark.parametrize(
    ("width", "height", "focus_x", "box"),
    [
        # Taller than the frame: the whole width, the middle of the height.
        (900, 2000, 50, (0, 200, 900, 1800)),
        # A little wider: the whole height, the width around the focus.
        (1200, 1800, 100, (188, 0, 1200, 1800)),
    ],
)
def test_a_photo_is_cropped_to_the_tall_frame_with_its_own_pixels(
    width: int, height: int, focus_x: int, box: tuple[int, int, int, int]
) -> None:
    photo = striped(width, height)

    fitted = real_photo.fit(None, saved(photo), focus_x=focus_x)  # type: ignore[arg-type]

    assert fitted.how == "cropped"
    expected = photo.crop(box).resize((FRAME_WIDTH, FRAME_HEIGHT), PIL.Image.Resampling.LANCZOS)
    assert PIL.ImageChops.difference(opened(fitted.file), expected).getbbox() is None


def test_a_photo_too_small_for_the_frame_is_not_fitted() -> None:
    with pytest.raises(real_photo.PhotoNotFitted, match="300 pixels wide"):
        real_photo.fit(None, saved(striped(300, 600)))  # type: ignore[arg-type]


def test_a_photo_too_tall_to_crop_without_losing_the_product_is_not_fitted() -> None:
    with pytest.raises(real_photo.PhotoNotFitted, match="so tall"):
        real_photo.fit(None, saved(striped(900, 4000)))  # type: ignore[arg-type]


def test_a_phone_photo_is_fitted_upright() -> None:
    # Stored sideways, 2,000 x 900, with a note to turn it a quarter: upright it is tall.
    photo = striped(900, 2000).rotate(90, expand=True)
    exif = PIL.Image.Exif()
    exif[PIL.ExifTags.Base.Orientation] = 6
    out = io.BytesIO()
    photo.save(out, format="JPEG", exif=exif)

    fitted = real_photo.fit(None, file_store.save("phone.jpg", out.getvalue()))  # type: ignore[arg-type]

    assert fitted.how == "cropped"


def test_a_see_through_background_is_laid_on_white() -> None:
    photo = PIL.Image.new("RGBA", (900, 1600), (0, 0, 0, 0))
    photo.paste((200, 30, 30, 255), (300, 600, 600, 1000))

    fitted = real_photo.fit(None, saved(photo))  # type: ignore[arg-type]

    assert opened(fitted.file).getpixel((5, 5)) == (255, 255, 255)


def test_an_extended_picture_of_the_wrong_shape_has_no_pixels_pasted_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    square = saved(PIL.Image.new("RGB", (1024, 1024), "grey"))
    monkeypatch.setattr(real_photo, "edit_picture", lambda **_: square)

    with pytest.raises(real_photo.PixelsNotPasted, match="1024x1024"):
        real_photo.fit(None, saved(striped(1600, 1200)))  # type: ignore[arg-type]
