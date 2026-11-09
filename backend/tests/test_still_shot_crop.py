"""A still shot's crop stays inside its starting picture, even centred on an edge."""

import io

import PIL.Image
import pytest

from adforge import file_store
from agents.scene_agent.still_shot import crop

SIZE = 300


def _gradient() -> str:
    """A picture whose red grows left to right and green top to bottom."""
    image = PIL.Image.new("RGB", (SIZE, SIZE))
    image.putdata(
        [
            (x * 255 // (SIZE - 1), y * 255 // (SIZE - 1), 0)
            for y in range(SIZE)
            for x in range(SIZE)
        ]
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return file_store.save("gradient.png", out.getvalue())


def _opened(key: str) -> PIL.Image.Image:
    with PIL.Image.open(io.BytesIO(file_store.read(key))) as opened:
        return opened.convert("RGB")


@pytest.mark.parametrize(
    ("centre", "top_left", "bottom_right"),
    [
        # Centred on the top-left corner: the crop starts there, a third short of the far edges.
        (0, (0, 0), (170, 170)),
        # Centred on the bottom-right corner: the crop ends at the corner.
        (100, (85, 85), (255, 255)),
    ],
    ids=["top-left", "bottom-right"],
)
def test_a_crop_centred_on_an_edge_is_kept_inside_the_picture(
    centre: int, top_left: tuple[int, int], bottom_right: tuple[int, int]
) -> None:
    cut = _opened(crop(_gradient(), centre, centre))

    assert cut.size == (SIZE, SIZE)
    for (x, y), (red, green) in [((0, 0), top_left), ((SIZE - 1, SIZE - 1), bottom_right)]:
        got = cut.getpixel((x, y))
        assert isinstance(got, tuple)
        assert abs(got[0] - red) <= 6 and abs(got[1] - green) <= 6, (x, y, got)
