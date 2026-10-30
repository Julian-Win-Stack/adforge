"""The size a cut-out or a paint is asked for (#273 point 1): the photo's shape, at about as
many pixels as the edit's own 1152 by 2048, each side a multiple of 16, no side longer than
2048 and none more than three times the other. Worked by hand."""

import pytest

from gateway.catalog import edit_size


@pytest.mark.parametrize(
    ("width", "height", "size"),
    [
        (112, 140, "1376x1712"),  # 4:5
        (300, 400, "1328x1776"),  # 3:4
        (500, 500, "1536x1536"),  # square
        (1152, 2048, "1152x2048"),  # the edit's own shape
        (2048, 1152, "2048x1152"),  # on its side
        (100, 500, "688x2048"),  # taller than 1:3 is drawn at 1:3, its longest side 2048
        (500, 100, "2048x688"),
    ],
)
def test_the_size_keeps_the_photos_shape(width: int, height: int, size: str) -> None:
    assert edit_size(width, height) == size
