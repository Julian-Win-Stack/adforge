"""The small functions that reading a page is built from, each with its tricky inputs."""

import io
import json
from typing import Any

import PIL.Image
import PIL.ImageDraw
import pytest

from jobs.page import DECLARED_DATA_HEADING
from jobs.page_text import PRICES_HEADING, match_back, record_prices, without_prices
from jobs.photos import Copies, best_url

from .conftest import photo

# The page a model copied from, as plain text.
STUDIO = "The mug is thrown by hand in our Leeds studio and glazed in sage green."
PAGE = f"""Stoneware Mug
{STUDIO} It's “dishwasher safe”, and it holds 350 ml.
Colours: sage green, cream, charcoal, oat, rust, slate blue, moss, sand, plum, ink
You may also like the Stoneware Bowl, thrown in the same studio."""


@pytest.mark.parametrize(
    ("copied", "kept"),
    [
        pytest.param([STUDIO], [STUDIO], id="a sentence as the page wrote it"),
        pytest.param(
            ['It\'s "dishwasher safe", and it holds 350 ml.'],
            ['It\'s "dishwasher safe", and it holds 350 ml.'],
            id="straight quotes for the page's curly ones",
        ),
        pytest.param(
            ["THE MUG IS THROWN BY HAND IN OUR LEEDS STUDIO AND GLAZED IN SAGE GREEN."],
            ["THE MUG IS THROWN BY HAND IN OUR LEEDS STUDIO AND GLAZED IN SAGE GREEN."],
            id="capital letters",
        ),
        pytest.param(
            ["The mug is  thrown by hand\tin our Leeds studio   and glazed in sage green."],
            ["The mug is  thrown by hand\tin our Leeds studio   and glazed in sage green."],
            id="extra spaces",
        ),
        pytest.param(
            ["The mug is thrown by hand in our Leeds workshops and glazed in sage."],
            [STUDIO],
            id="86% alike: kept as the page's sentence",
        ),
        pytest.param(
            ["The mug is made by hand in our Leeds workshop and glazed in sage green."],
            [],
            id="84% alike: dropped",
        ),
        pytest.param(
            ["cream, charcoal, oat, rust"],
            ["cream, charcoal, oat, rust"],
            id="a sentence inside a long list",
        ),
        pytest.param(
            ["- **Colours:** sage green, [cream](/products/mug-cream), charcoal"],
            ["Colours: sage green, cream, charcoal"],
            id="markdown the page text doesn't have",
        ),
        pytest.param(
            [f"{STUDIO} It comes with a lifetime guarantee against chips."],
            [STUDIO],
            id="a made-up sentence inside a real passage",
        ),
        pytest.param([STUDIO, "Stoneware Mug", STUDIO], [STUDIO, "Stoneware Mug"], id="kept once"),
        pytest.param([], [], id="an empty answer"),
        pytest.param(["", "Sage."], [], id="bits too short to be a sentence"),
    ],
)
def test_a_copied_sentence_is_kept_only_if_the_page_says_it(
    copied: list[str], kept: list[str]
) -> None:
    assert match_back(copied, PAGE) == kept


def option(title: str, amount: object, currency: object = "USD", **more: object) -> dict[str, Any]:
    """One variant of a Firecrawl product record, with its price."""
    return {"title": title, "price": {"amount": amount, "currency": currency}, **more}


SHADES = [option(f"Shade {n}", 32) for n in range(1, 39)]


@pytest.mark.parametrize(
    ("variants", "prices"),
    [
        pytest.param(
            [option("Full Size (10 fl oz)", 34), option("Travel Size (3 fl oz)", 16)],
            "34.00 USD: Full Size (10 fl oz)\n16.00 USD: Travel Size (3 fl oz)",
            id="one line for each price",
        ),
        pytest.param(
            [option("Cream", 445), option("Navy", 445.0), option("Large", "595.00")],
            "445.00 USD: Cream; Navy\n595.00 USD: Large",
            id="options at one price share its line",
        ),
        pytest.param(
            SHADES,
            "32.00 USD: Shade 1; Shade 2; Shade 3; Shade 4; Shade 5; Shade 6; Shade 7; "
            "Shade 8; Shade 9; Shade 10; and 28 more",
            id="a long list of options is cut short",
        ),
        pytest.param(
            [option("Cream", 445, sale={"originalPrice": {"amount": 675, "currency": "USD"}})],
            "445.00 USD, was 675.00 USD: Cream",
            id="a sale keeps its price before",
        ),
        pytest.param([option("Mug", 1299.5, "JPY")], "1,299.50 JPY: Mug", id="any currency"),
        pytest.param([option("Mug", 24, None)], "24.00: Mug", id="no currency"),
        pytest.param(
            [option("Mug", None), option("Mug", "ask us"), option("Mug", True), {"title": "Mug"}],
            "",
            id="no amount that is a number",
        ),
        pytest.param([], "", id="no variants"),
    ],
)
def test_a_records_prices_are_listed_with_the_options_sold_at_each(
    variants: list[dict[str, Any]], prices: str
) -> None:
    listed = record_prices({"title": "Mug", "variants": variants})
    assert listed == (f"{PRICES_HEADING}{prices}" if prices else "")


def test_a_record_without_a_variants_list_has_no_prices() -> None:
    assert record_prices({"title": "Mug", "variants": "none"}) == ""
    assert record_prices({}) == ""


def test_declared_data_keeps_everything_but_its_prices() -> None:
    mug = {
        "@type": "Product",
        "name": "Stoneware Mug",
        "brand": {"@type": "Brand", "name": "Kiln & Co"},
        "offers": [
            {
                "@type": "Offer",
                "price": "24.00",
                "priceCurrency": "USD",
                "priceValidUntil": "2026-12-31",
                "priceSpecification": {"@type": "UnitPriceSpecification", "price": 24},
                "availability": "https://schema.org/InStock",
            },
            {"@type": "AggregateOffer", "lowPrice": 19, "highPrice": 24, "offerCount": 2},
        ],
    }
    bowl = {"@type": "Product", "name": "Bowl", "offers": {"price": 30, "sku": "B1"}}
    declared = DECLARED_DATA_HEADING + "\n".join(json.dumps(p) for p in (mug, bowl))

    kept = without_prices(declared)

    heading, listed = kept[: len(DECLARED_DATA_HEADING)], kept[len(DECLARED_DATA_HEADING) :]
    assert heading == DECLARED_DATA_HEADING
    assert [json.loads(line) for line in listed.split("\n")] == [
        {
            "@type": "Product",
            "name": "Stoneware Mug",
            "brand": {"@type": "Brand", "name": "Kiln & Co"},
            "offers": [
                {"@type": "Offer", "availability": "https://schema.org/InStock"},
                {"@type": "AggregateOffer", "offerCount": 2},
            ],
        },
        {"@type": "Product", "name": "Bowl", "offers": {"sku": "B1"}},
    ]


def test_no_declared_data_stays_none() -> None:
    assert without_prices("") == ""


CLOUDINARY = "https://res.cloudinary.com/shop/image/upload"


@pytest.mark.parametrize(
    ("picture", "biggest"),
    [
        pytest.param(
            {"src": "https://shop.com/cdn/shop/files/front.jpg?v=17&width=200"},
            "https://shop.com/cdn/shop/files/front.jpg?v=17",
            id="Shopify asked for 200 px wide",
        ),
        pytest.param(
            {"src": "https://m.media-amazon.com/images/I/71Q2x._AC_SX300_.jpg"},
            "https://m.media-amazon.com/images/I/71Q2x.jpg",
            id="Amazon's size part",
        ),
        pytest.param(
            {
                "src": "https://shop.com/img/front-400.jpg",
                "srcset": "https://shop.com/img/front-400.jpg 400w, "
                "https://shop.com/img/front-1600.jpg 1600w, "
                "https://shop.com/img/front-800.jpg 800w",
            },
            "https://shop.com/img/front-1600.jpg",
            id="the widest in a srcset of several sizes",
        ),
        pytest.param(
            {
                "src": f"{CLOUDINARY}/w_400,h_500,c_fill/front.jpg",
                "srcset": f"{CLOUDINARY}/w_400,h_500,c_fill/front.jpg 400w, "
                f"{CLOUDINARY}/w_1200,h_1500,c_fill/front.jpg 1200w",
            },
            f"{CLOUDINARY}/w_1200,h_1500,c_fill/front.jpg",
            id="links with commas in them",
        ),
        pytest.param(
            {
                "src": "",
                "srcset": "//cdn.shopify.com/s/files/front.jpg?v=3&width=180 180w, "
                "//cdn.shopify.com/s/files/front.jpg?v=3&width=1080 1080w",
            },
            "https://cdn.shopify.com/s/files/front.jpg?v=3",
            id="a link starting //",
        ),
        pytest.param(
            {"src": "https://example.com/img/front.jpg?width=200"},
            "https://example.com/img/front.jpg?width=200",
            id="an unknown shop's link, left as it is",
        ),
    ],
)
def test_a_picture_is_fetched_at_its_biggest_size(picture: dict[str, str], biggest: str) -> None:
    assert best_url(picture) == biggest


def bottle(colour: str, background: str = "white") -> bytes:
    """A bottle on a background, in `colour`: the same photo in another shade."""
    image = PIL.Image.new("RGBA", (200, 300), background)
    PIL.ImageDraw.Draw(image).rectangle((70, 60, 130, 260), fill=colour)
    PIL.ImageDraw.Draw(image).rectangle((90, 30, 110, 60), fill="black")
    file = io.BytesIO()
    image.save(file, "PNG")
    return file.getvalue()


SHOPIFY = "https://cdn.shopify.com/s/files/1/front"


@pytest.mark.parametrize(
    ("photos", "kept"),
    [
        pytest.param(
            [
                ("https://shop.com/front-small.png", photo(1, 64, 80)),
                ("https://shop.com/front-big.png", photo(1, 640, 800)),
            ],
            1,
            id="the same photo at two sizes",
        ),
        pytest.param(
            [(f"{SHOPIFY}.jpg?v=1", photo(1)), (f"{SHOPIFY}_600x.jpg?v=1", photo(2))],
            1,
            id="the same name with a _600x size part",
        ),
        pytest.param(
            [("https://shop.com/front.png", photo(1)), ("https://shop.com/side.png", photo(2))],
            2,
            id="two different photos",
        ),
        # The fingerprint ignores colour, so one product's shades are kept once: accepted.
        pytest.param(
            [
                ("https://shop.com/rose.png", bottle("#c0506a")),
                ("https://shop.com/navy.png", bottle("#1f2a5a")),
            ],
            1,
            id="two shades of one product, differing only in colour",
        ),
        pytest.param(
            [
                ("https://shop.com/rose.png", bottle("#c0506a", "#00000000")),
                ("https://shop.com/rose-on-white.png", bottle("#c0506a")),
            ],
            1,
            id="a see-through background and a white one",
        ),
    ],
)
def test_copies_of_one_photo_are_kept_once(photos: list[tuple[str, bytes]], kept: int) -> None:
    copies = Copies()
    assert sum(copies.is_new(url, content) for url, content in photos) == kept
