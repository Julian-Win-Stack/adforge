import pytest
from pydantic import ValidationError

from jobs.tasks import PageCheckHandoff


def test_a_handoff_with_a_decimal_where_a_whole_number_belongs_is_refused() -> None:
    with pytest.raises(ValidationError):
        PageCheckHandoff(
            product_url="https://shop.example/products/mug",
            page_url="https://shop.example/products/mug",
            page_text="Stoneware Mug",
            photo_count=2.0,  # type: ignore[arg-type]
        )
