"""The hard list's refusals and the cut's answer (spec #247 point 13), in support of the
chat-seam tests in test_tool_answers.py and test_producer_owner_part.py."""

import pytest
from pytest_django import Settings

from agents.hard_list import cut_answer, same_product
from jobs.cut_and_send import PieceLeftOut, Sending
from jobs.library import LeftOut, SentPiece

SHOP = "https://shop.example/products/mug"


@pytest.mark.parametrize(
    "link",
    [
        "http://shop.example/products/mug",
        "https://www.shop.example/products/mug",
        "https://SHOP.example/products/mug/",
        "https://shop.example/products/mug?utm_campaign=autumn&gclid=1",
        "https://shop.example/products/mug#reviews",
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
    ],
)
def test_a_different_address_is_another_product(link: str, other: str) -> None:
    assert not same_product(link, other)


def sending(**changes: object) -> Sending:
    made = {
        "colour": "white",
        "pictures": [],
        "pieces": [],
        "left_out": [],
        "recolours": [],
        "reference_videos": [],
        **changes,
    }
    return Sending(**made)  # type: ignore[arg-type]


def test_nothing_cut_gives_no_answer() -> None:
    assert cut_answer(2, sending()) == ""


def test_the_cut_answer_names_each_picture_and_piece_left_out_and_why(settings: Settings) -> None:
    settings.BROLL_MOST_PIECES = 3
    cut = sending(
        left_out=[
            LeftOut(
                tier=2, library_number=12, drawn=None, for_=None, label="The lid off.", file="a"
            ),
            LeftOut(tier=4, library_number=3, drawn=None, for_=None, label="Its back.", file="b"),
        ],
        pieces=[
            SentPiece(
                number=1,
                video=1,
                moment=2,
                moment_start=0,
                moment_end=20,
                start=0,
                end=15,
                trim="moment_over_limit",
                file="c",
            )
        ],
        pieces_left_out=[
            PieceLeftOut(
                video=2,
                moment=1,
                start=4,
                end=9,
                reason="the pieces before it fill the 15 seconds of video a clip is sent",
            )
        ],
    )

    assert cut_answer(3, cut) == (
        "The video model takes at most 9 pictures, and the 9 that come first in the send order "
        'filled it, so 2 pictures were left out of scene 3\'s clip: "The lid off." (library '
        'item 12, taken as a scene picture); "Its back." (library item 3, taken as another '
        "side of the product). The piece of video 2, moment 1 (4 to 9 s), was left out of "
        "scene 3's clip: the pieces before it fill the 15 seconds of video a clip is sent. "
        "Video 1 of scene 3's clip (video 1, moment 2) was cut to 0 to 15 s of its 0 to 20 s: "
        "the video model takes at most 15 s of video over up to 3 pieces."
    )
