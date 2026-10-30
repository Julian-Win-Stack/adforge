"""The hard list's refusals and the cut's answer (spec #247 point 13), in support of the
chat-seam tests in test_tool_answers.py and test_producer_owner_part.py."""

import pytest
from pytest_django import Settings

from agents.hard_list import cut_answer, same_product
from jobs.cut_and_send import Excluded, PieceLeftOut, Sending
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


def test_the_cut_answer_names_a_drawn_picture_an_excluded_item_and_a_piece_cut_to_fit(
    settings: Settings,
) -> None:
    settings.BROLL_MOST_PIECES = 1
    cut = sending(
        left_out=[
            LeftOut(
                tier=3, library_number=None, drawn="room", for_=None, label="The kitchen.", file="a"
            )
        ],
        excluded=[Excluded(number=4, label="Its box.", tier=4)],
        pieces=[
            SentPiece(
                number=2,
                video=1,
                moment=3,
                moment_start=10,
                moment_end=18,
                start=10,
                end=14,
                trim="pieces_over_limit",
                file="c",
            )
        ],
    )

    assert cut_answer(1, cut) == (
        "The video model takes at most 9 pictures, and the 9 that come first in the send order "
        "filled it, so 1 picture was left out of scene 1's clip: \"The kitchen.\" (the ad's "
        "room alone, taken as the drawn picture of the place). Not sent, as excluded for scene "
        '1: "Its box." (library item 4, another side of the product). Video 2 of scene 1\'s '
        "clip (video 1, moment 3) was cut to 10 to 14 s of its 10 to 18 s: the video model "
        "takes at most 15 s of video over up to 1 piece."
    )
