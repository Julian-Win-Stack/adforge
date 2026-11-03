"""Product-link identity checks supporting the chat-seam hard-list tests."""

import pytest

from agents.hard_list import same_product

SHOP = "https://shop.example/products/mug"


@pytest.mark.parametrize(
    "link",
    [
        "http://shop.example/products/mug",
        "https://www.shop.example/products/mug",
        "https://SHOP.example/products/mug/",
        "https://shop.example/products/mug?utm_campaign=autumn&gclid=1",
        "https://shop.example/products/mug#reviews",
        "https://shop.example/products/mug?UTM_Source=mail&FBCLID=2",
    ],
)
def test_another_form_of_a_products_link_is_the_same_product(link: str) -> None:
    assert same_product(link, SHOP)


@pytest.mark.parametrize(
    ("link", "other"),
    [
        ("https://shop.example/products/teapot", SHOP),
        ("https://other.example/products/mug", SHOP),
        # A parameter that picks the product among the shop's is part of which product it is.
        ("https://shop.example/item?id=12", "https://shop.example/item?id=13"),
        # Another port is another site.
        ("https://shop.example:8443/products/mug", SHOP),
        # A parameter is part of the address even when it is blank.
        ("https://shop.example/item?variant=", "https://shop.example/item"),
    ],
)
def test_a_different_address_is_another_product(link: str, other: str) -> None:
    assert not same_product(link, other)


def test_the_same_parameters_in_another_order_are_the_same_product() -> None:
    assert same_product(
        "https://shop.example/item?id=12&size=m", "https://shop.example/item?size=m&id=12"
    )
