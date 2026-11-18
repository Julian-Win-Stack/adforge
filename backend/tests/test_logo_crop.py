"""Check the logo crop's padding, bounds and enlargement on known pixels."""

import io

import PIL.Image
import pytest

from agents.scene_agent.product_logo import BadLogoBox, crop_logo

from .conftest import photo


def test_padding_stays_inside_the_photo_and_large_crops_keep_their_pixels() -> None:
    source = photo(9, width=1600, height=800)
    # The box is 1200 x 400. Padding reaches 300 px left and 100 px up, off the photo.
    cropped = crop_logo(source, [0, 0, 750, 500])
    with (
        PIL.Image.open(io.BytesIO(source)) as original,
        PIL.Image.open(io.BytesIO(cropped)) as cut,
    ):
        assert cut.size == (1500, 500)
        assert cut.tobytes() == original.crop((0, 0, 1500, 500)).tobytes()


def test_small_crops_are_enlarged_to_1024_on_their_long_side() -> None:
    source = photo(8, width=400, height=200)
    crop = crop_logo(source, [250, 250, 750, 750])
    with PIL.Image.open(io.BytesIO(crop)) as cut:
        assert cut.size == (1024, 512)


@pytest.mark.parametrize("box", [[], [100, 100, 100, 200], [1001, 0, 1100, 200]])
def test_an_empty_or_off_photo_box_has_no_crop(box: list[int]) -> None:
    with pytest.raises(BadLogoBox):
        crop_logo(photo(9), box)
