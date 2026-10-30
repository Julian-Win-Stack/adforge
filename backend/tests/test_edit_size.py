"""The size a cut-out or a paint is asked for (#273 point 1): the photo's shape, at about as
many pixels as the edit's own 1152 by 2048, each side a multiple of 16, no side longer than
2048 and none more than three times the other, worked by hand; a phone photo's as it is
seen."""

import io
from typing import Any

import PIL.ExifTags
import PIL.Image
import pytest

from adforge import file_store
from gateway.catalog import edit_size
from gateway.fake import FakeModel
from gateway.gateway import edit_picture
from gateway.types import Picture


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


@pytest.mark.django_db
def test_a_phone_photo_stored_on_its_side_is_sent_upright_at_its_upright_shape(
    fake_model: FakeModel,
) -> None:
    # 400 by 300 pixels as stored, with the phone's note that it is turned a quarter: a
    # portrait photo, 300 by 400, as it is seen.
    stored = io.BytesIO()
    exif = PIL.Image.Exif()
    exif[PIL.ExifTags.Base.Orientation] = 6
    PIL.Image.new("RGB", (400, 300), (200, 30, 30)).save(stored, format="JPEG", exif=exif)
    key = file_store.save("phone.jpg", stored.getvalue())
    sent: list[bytes] = []
    edit = fake_model.edit

    def recording(**asked: Any) -> Picture:
        sent.extend(asked["pictures"])
        return edit(**asked)

    fake_model.edit = recording  # type: ignore[method-assign]

    edit_picture(
        job=None,
        purpose="make_product_only",
        prompt="The product.",
        pictures=[key],
        keep_shape=True,
    )

    assert fake_model.edits_asked == [("make_product_only", "1328x1776")]
    with PIL.Image.open(io.BytesIO(sent[0])) as upright:
        assert upright.size == (300, 400)
