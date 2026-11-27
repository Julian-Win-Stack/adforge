"""The end card's layout and the words drawn on it (spec #783), as pure logic: where each part
goes, how text that would overflow is made smaller, and when each row pops in."""

import re
from datetime import date
from pathlib import Path
from typing import Any

import PIL.ImageFont
import pytest

from jobs import assembly
from jobs.card_parts import (
    NEAR_BLACK,
    CardParts,
    Part,
    PartKind,
    Size,
    badge_colour,
    card_colour,
    contrast,
    sale_end_words,
)

K = PartKind


def a_card(sale: bool = False, accent: str = NEAR_BLACK, **more: dict[str, Any]) -> CardParts:
    """Today's card for a mug, with a sale and its end date when `sale`, and any `more`
    parts' contents."""
    contents: dict[PartKind, dict[str, Any]] = {
        K.PHOTO: {"file": "photo.png"},
        K.HEADLINE: {"text": "Stoneware Mug"},
        K.PRICE: {"text": "$24.00", **({"old_price": "$30.00", "percent": 20} if sale else {})},
        K.SMALL_LINE: {"text": "Hand-thrown, holds 350 ml, dishwasher safe."},
        K.SHOP_LINE: {"text": "kilnandco.com"},
    }
    if sale:
        contents[K.SALE_END] = {"ends": date(2026, 12, 1)}
    contents.update({PartKind(kind): content for kind, content in more.items()})
    return CardParts.of(contents, accent=accent)


def changed(card: CardParts, kind: PartKind, **change: Any) -> CardParts:
    return card.model_copy(
        update={
            "parts": tuple(
                part.model_copy(update=change) if part.kind == kind else part for part in card.parts
            )
        }
    )


def place(layout: assembly.CardLayout, kind: PartKind) -> assembly.PartPlace:
    (found,) = [part for part in layout.parts if part.kind == kind]
    return found


def words_of(layout: assembly.CardLayout) -> list[str]:
    return [words.text for words in layout.words]


def assert_fits(layout: assembly.CardLayout) -> None:
    """Every part inside the card's room in the safe area, in order, none over another, and
    every row of words no wider than the card, measured with its font."""
    for part in layout.parts:
        assert assembly.CARD_TOP - 0.5 <= part.top <= part.bottom <= assembly.CARD_BOTTOM + 0.5
    rows: dict[float, list[assembly.PartPlace]] = {}
    for part in layout.parts:
        rows.setdefault(part.top, []).append(part)
    stacked = sorted(rows.values(), key=lambda row: row[0].top)
    for upper, lower in zip(stacked, stacked[1:], strict=False):
        assert upper[0].bottom <= lower[0].top + 0.5
    for words in layout.words:
        margin = assembly.CARD_MARGIN
        assert margin - 0.5 <= words.left and words.right <= assembly.FRAME_WIDTH - margin + 0.5
        (room,) = {
            (part.top, part.bottom)
            for part in layout.parts
            if part.top - 0.5 <= words.middle_y <= part.bottom + 0.5
        }
        assert room[0] - 0.5 <= words.top and words.bottom <= room[1] + 0.5
    for box in layout.pictures:
        assert assembly.SAFE_TOP <= box.top and box.top + box.height <= assembly.SAFE_BOTTOM


def test_todays_card_shows_the_photo_then_the_headline_price_small_line_and_shop_line() -> None:
    layout = assembly.end_card_layout(a_card())

    assert [part.kind for part in layout.parts] == [
        K.PHOTO,
        K.HEADLINE,
        K.PRICE,
        K.SMALL_LINE,
        K.SHOP_LINE,
    ]
    assert words_of(layout) == [
        "Stoneware Mug",
        "$24.00",
        "Hand-thrown, holds 350 ml, dishwasher safe.",
        "kilnandco.com",
    ]
    assert_fits(layout)
    # The photo fills the height left above the words, on top, the card's largest part.
    photo = place(layout, K.PHOTO)
    assert photo.top == assembly.CARD_TOP
    assert photo.bottom - photo.top >= 500
    assert place(layout, K.HEADLINE).top == pytest.approx(photo.bottom + 40)
    (box,) = layout.pictures
    assert (box.file, box.left, box.width) == ("photo.png", 60, 960)
    # The words take the spec's sizes, fonts and colours: the accent for headline and price.
    looks = {words.text: (words.font, words.size, words.colour) for words in layout.words}
    assert looks["Stoneware Mug"] == ("Inter ExtraBold", 76, NEAR_BLACK)
    assert looks["$24.00"] == ("Inter ExtraBold", 122, NEAR_BLACK)
    assert looks["kilnandco.com"] == ("Inter Medium", 44, "#4a4f4c")


def test_a_sale_draws_the_old_price_crossed_out_a_badge_and_the_end_date() -> None:
    layout = assembly.end_card_layout(a_card(sale=True, accent="#1f4a39"))

    by_text = {words.text: words for words in layout.words}
    price, old, badge = by_text["$24.00"], by_text["$30.00"], by_text["20% off"]
    # Side by side on one row, in that order, centred across the card.
    assert price.middle_y == old.middle_y == badge.middle_y
    assert price.right < old.left < old.right < badge.left
    assert (price.left + badge.right) / 2 == pytest.approx(
        assembly.CARD_MIDDLE, abs=2 * assembly.BADGE_PADDING[1]
    )
    assert old.struck and not price.struck
    assert old.colour == "#8b918d"
    assert (badge.colour, badge.pill) == ("#ffffff", badge_colour("#1f4a39"))
    ends = by_text["Sale ends Dec 1"]
    assert (ends.font, ends.size, ends.colour) == (
        "Inter SemiBold",
        42,
        badge_colour("#1f4a39"),
    )
    assert place(layout, K.SALE_END).top == pytest.approx(place(layout, K.PRICE).bottom + 10)
    assert_fits(layout)


def test_an_extra_line_a_second_photo_and_a_logo_are_drawn() -> None:
    card = a_card(
        sale=True,
        extra_line={"text": "Free shipping"},
        second_photo={"file": "second.png"},
        logo={"file": "logo.png"},
    )

    layout = assembly.end_card_layout(card)

    assert "Free shipping" in words_of(layout)
    photo, second, logo = layout.pictures
    # The second photo sits beside the first, sharing the row; the logo under the words.
    assert (photo.file, second.file, logo.file) == ("photo.png", "second.png", "logo.png")
    assert photo.top == second.top and photo.height == second.height
    assert photo.left + photo.width < second.left
    assert logo.top > max(words.bottom for words in layout.words)
    assert_fits(layout)


def test_a_hidden_or_empty_part_leaves_no_gap() -> None:
    hidden = assembly.end_card_layout(changed(a_card(), K.SMALL_LINE, shown=False))
    empty = assembly.end_card_layout(changed(a_card(), K.SMALL_LINE, text=""))
    without = assembly.end_card_layout(
        a_card().model_copy(
            update={"parts": tuple(p for p in a_card().parts if p.kind != K.SMALL_LINE)}
        )
    )

    assert hidden == empty == without
    assert K.SMALL_LINE not in [part.kind for part in hidden.parts]
    assert place(hidden, K.SHOP_LINE).top == pytest.approx(place(hidden, K.PRICE).bottom + 34)


def test_a_card_with_no_photo_has_its_words_in_the_middle() -> None:
    layout = assembly.end_card_layout(changed(a_card(), K.PHOTO, file=""))

    assert not layout.pictures
    top, bottom = layout.parts[0].top, layout.parts[-1].bottom
    assert top - assembly.CARD_TOP == pytest.approx(assembly.CARD_BOTTOM - bottom, abs=1)


def test_a_changed_order_is_drawn_in_that_order() -> None:
    card = a_card()
    order = [K.PRICE, K.HEADLINE, K.PHOTO, K.SHOP_LINE, K.SMALL_LINE]
    reordered = card.model_copy(
        update={"parts": tuple(sorted(card.parts, key=lambda p: (order + list(K)).index(p.kind)))}
    )

    layout = assembly.end_card_layout(reordered)

    assert [part.kind for part in layout.parts] == order
    assert words_of(layout)[:2] == ["$24.00", "Stoneware Mug"]
    assert_fits(layout)


@pytest.mark.parametrize("kind", [K.HEADLINE, K.PRICE, K.SMALL_LINE, K.SHOP_LINE])
def test_a_size_step_changes_that_parts_size(kind: PartKind) -> None:
    def size(step: Size) -> int:
        layout = assembly.end_card_layout(changed(a_card(), kind, size=step))
        (first, *_) = [w for w in layout.words if place(layout, kind).top <= w.middle_y]
        return first.size

    assert size(Size.SMALL) < size(Size.NORMAL) < size(Size.LARGE)


def test_a_size_step_changes_the_photos_and_the_logos_size() -> None:
    def photo_height(step: Size) -> int:
        return assembly.end_card_layout(changed(a_card(), K.PHOTO, size=step)).pictures[0].height

    def logo_height(step: Size) -> int:
        card = changed(a_card(logo={"file": "logo.png"}), K.LOGO, size=step)
        return assembly.end_card_layout(card).pictures[-1].height

    assert photo_height(Size.SMALL) < photo_height(Size.NORMAL) < photo_height(Size.LARGE)
    assert logo_height(Size.SMALL) < logo_height(Size.NORMAL) < logo_height(Size.LARGE)


@pytest.mark.parametrize(
    "more",
    [
        {
            K.HEADLINE: {"text": "The Extraordinarily Long Named Hand Thrown Stoneware Mug"},
            K.SMALL_LINE: {
                "text": "Every single one is thrown by hand in our small studio, glazed in "
                "sage green and fired twice, so it holds heat for longer than any mug."
            },
            K.PRICE: {"text": "$1,299.99", "old_price": "$1,499.99", "percent": 13},
            K.SHOP_LINE: {
                "text": "the-very-long-named-pottery-studio-and-homeware-shop.example.co.uk"
            },
            K.EXTRA_LINE: {"text": "Free shipping on every order over fifty dollars, worldwide"},
        },
        {K.HEADLINE: {"text": "Supercalifragilisticexpialidociousnessfullyhandthrownmugs"}},
    ],
)
def test_text_that_would_overflow_is_made_smaller_and_never_overlaps(
    more: dict[PartKind, dict[str, Any]],
) -> None:
    card = a_card(sale=True, logo={"file": "logo.png"}, second_photo={"file": "s.png"})
    for kind, content in more.items():
        card = changed(card, kind, **content)
    for kind in (K.HEADLINE, K.PRICE, K.SMALL_LINE):
        card = changed(card, kind, size=Size.LARGE)

    layout = assembly.end_card_layout(card)

    assert_fits(layout)
    # No letter is lost: a word too wide for a row at the smallest size runs on to the next.
    for kind, content in more.items():
        if kind in (K.HEADLINE, K.SMALL_LINE, K.SHOP_LINE, K.EXTRA_LINE):
            shown = [w.text for w in layout.words if place(layout, kind).top <= w.middle_y]
            joined = "".join("".join(shown).split())
            assert joined.startswith("".join(content["text"].split()))
    # The headline keeps to its 2 rows, drawn smaller rather than taking more.
    headline = place(layout, K.HEADLINE)
    rows = [w for w in layout.words if headline.top <= w.middle_y <= headline.bottom]
    assert len(rows) <= assembly.MOST_ROWS[K.HEADLINE]


def test_row_widths_are_measured_with_the_real_font() -> None:
    # Twenty narrow letters fit on a row a guess per letter would wrap; twenty wide ones
    # don't. Both are measured.
    narrow = "il1. " * 14
    wide = "WMWM " * 14

    narrow_rows = assembly.end_card_layout(changed(a_card(), K.SMALL_LINE, text=narrow))
    wide_rows = assembly.end_card_layout(changed(a_card(), K.SMALL_LINE, text=wide))

    def rows(layout: assembly.CardLayout) -> int:
        part = place(layout, K.SMALL_LINE)
        return sum(1 for w in layout.words if part.top <= w.middle_y <= part.bottom)

    assert rows(narrow_rows) == 1
    assert rows(wide_rows) == 2
    font = PIL.ImageFont.truetype(assembly.FONTS / "Inter-Medium.ttf", 44)
    assert assembly.text_width(wide, "Inter Medium", 44) == font.getlength(wide)


def test_each_part_of_words_pops_in_after_the_one_before(tmp_path: Path) -> None:
    card = a_card(sale=True)
    end_card = assembly.EndCard(card, 10.0, 13.0)
    ass = tmp_path / "text.ass"

    assembly.write_text_to_draw([], [], ass, end_card)

    drawn = [line for line in ass.read_text().splitlines() if line.startswith("Dialogue")]
    starts: dict[str, int] = {}
    for line in drawn:
        # Shown for the whole card, starting small and see-through.
        assert line.split(",")[1:3] == ["0:00:10.00", "0:00:13.00"]
        assert "\\fscx70\\fscy70\\alpha&HFF&" in line
        grow, settle = re.findall(r"\\t\((\d+),(\d+),", line)
        assert int(grow[1]) - int(grow[0]) == round(420 * 0.6)
        assert int(settle[1]) - int(grow[0]) == 420
        assert "\\fscx107\\fscy107\\alpha&H00&" in line
        starts[line.rsplit("}", 1)[-1]] = int(grow[0])
    assert starts["Stoneware Mug"] == 120
    # The price, old price and badge (and the badge's pill) pop in together, as one part.
    assert starts["$24.00"] == starts["$30.00"] == starts["20% off"] == 230
    assert starts["Sale ends Dec 1"] == 340
    assert starts["Hand-thrown, holds 350 ml, dishwasher safe."] == 450
    assert starts["kilnandco.com"] == 560
    pill = [line for line in drawn if "\\p1" in line]
    assert len(pill) == 1 and "\\t(230," in pill[0]
    # The old price is crossed out, the price not; each row in its own font.
    (old,) = [line for line in drawn if line.endswith("$30.00")]
    assert "\\s1" in old and "\\fnInter Medium" in old
    (price,) = [line for line in drawn if line.endswith("$24.00")]
    assert "\\s0" in price and "\\fnInter ExtraBold" in price


def test_the_badge_shade_is_lighter_than_the_accent_and_white_reads_on_it() -> None:
    assert badge_colour("#1f4a39") == "#307358"  # the mock-up drew #2f7359
    for accent in ("#1f4a39", NEAR_BLACK, "#1458c8", "#7a1f1f"):
        assert contrast(badge_colour(accent), "#ffffff") >= 4.5
        assert sum(int(badge_colour(accent)[i : i + 2], 16) for i in (1, 3, 5)) >= sum(
            int(accent[i : i + 2], 16) for i in (1, 3, 5)
        )


def test_the_card_is_white_with_a_faint_tint_of_the_accent() -> None:
    assert card_colour("#1f4a39") == "#f2f4f3"
    assert card_colour("#ffffff") == "#ffffff"


def test_a_sale_end_is_written_as_a_month_and_day() -> None:
    assert sale_end_words(date(2026, 12, 1)) == "Sale ends Dec 1"


def test_a_card_holds_each_kind_of_part_once_and_an_accent_colour() -> None:
    with pytest.raises(ValueError, match="at most once"):
        CardParts(parts=(Part(kind=K.HEADLINE), Part(kind=K.HEADLINE)))
    with pytest.raises(ValueError):
        CardParts(parts=(), accent="green")
    assert CardParts.model_validate(a_card(sale=True).record()) == a_card(sale=True)


def test_the_fonts_and_their_licence_are_in_the_repo() -> None:
    assert "SIL Open Font License" in (assembly.FONTS.parent / "LICENSE-Inter.txt").read_text()
    # ffmpeg tries to load every file in the fonts folder: it holds fonts only.
    assert {path.suffix for path in assembly.FONTS.iterdir()} == {".ttf"}
    for name, file in assembly.FONT_FILES.items():
        family, weight = PIL.ImageFont.truetype(assembly.FONTS / file, 20).getname()
        assert f"{family} {weight}" == name
