"""Keeping only this product's own text from its page. A cheap model copies out, word for
word, every passage about the product the page sells; each copied sentence is then matched
back to the page, so nothing the model made up or reworded is kept.

Tested on 70 saved shop pages: docs/scraping-test/copy-test/results.md. The matching rules
are those the test scored with (copy-test/scripts/copy_text.py, text-test/scripts/textnorm.py,
scripts/missing.py)."""

import difflib
import json
import math
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from gateway.types import Handoff

from .page import DECLARED_DATA_HEADING

# How alike a copied sentence and a page sentence must be, from 0 to 1, to count as the
# same sentence with small changes, such as a fixed typo. The page's sentence is kept.
NEAR = 0.85
# How many page sentences, those sharing the most words with a copied one, are compared
# with it closely.
CANDIDATES = 25
# A run of text with no full stop, such as a list joined by spaces, is cut near this many
# characters, so a sentence copied from inside it can be matched.
LONGEST_SENTENCE = 300
# Bits shorter than this, normalised, aren't sentences: "Size:", "$34", "Add to cart".
SHORTEST_SENTENCE = 12

# Heading of the prices from Firecrawl's record of the product, which follow the copied text.
# They are the only prices the planner is given: the copy model leaves prices out, as a page
# shows other products' prices too, and the declared data's prices are taken out. Without a
# record, the planner asks the shop owner the price.
PRICES_HEADING = "\n\nPrices in the shop's own record of the product:\n"
# How many options are named on a price's line; the rest are counted.
OPTIONS_NAMED = 10

# A markdown image, ![alt](url), and a markdown link, [text](url), its text kept in group 1.
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

COPY_INSTRUCTIONS = """\
You clean the text of one shop's product page before it is given to an advert writer. The \
advert is about ONE product: the one this page sells (its name, link and the shop's own \
description are given). Product pages also carry text about OTHER products, and none of it \
may reach the advert.

Copy out, word for word as written on the page, every passage that describes this product: \
its name, what it is, materials or ingredients, sizes, fit, dimensions, how to use, \
benefits, claims, research and awards, specs, what's in the box, warranty, shipping and \
returns for this item, and its FAQ. If the page lets the shopper choose a colour, flavour, \
shade or size, those choices are part of this product. Text in tabs, accordions and "read \
more" blocks counts: it is all in the text you get. Copy exactly: do not fix, shorten, join \
or rephrase. One passage per paragraph or list item. Skip the price.

Do not copy text about other products, even single sentences inside a passage that is \
otherwise about this product: "you may also like", "shop the look", "pairs well with", \
"complete your purchase", "frequently bought together", bundles and kits, the rest of a \
collection, other flavours, scents, models or versions sold on their own pages, accessories \
sold separately, recipes or tips built around another product, and comparisons with the \
brand's other products. Also leave out reviews, shoppers' own questions and answers (but do \
copy the shop's or brand's answers about this product), navigation, cart, promotions, \
newsletter, cookie and footer text.

Both mistakes matter: leaving out real text about this product loses facts the advert \
needs, and copying text about another product puts the wrong product in the advert. Copy \
everything about this product and nothing else. Links are shown as [text](path); a passage \
whose link goes to another product's page is about that product.

Headings can mislead: a product FAQ or description can sit under a heading like "Shop the \
Collection" or "Got questions?". Judge each passage by what it says, not by its heading. \
The shop's or brand's own text can also sit after or between customer reviews: a brand \
story, a "why it's better" block, the shop's answers to questions. Judge each passage by \
who wrote it: copy what the shop or brand wrote about this product, leave out what shoppers \
wrote."""


class CopyHandoff(Handoff):
    product: str  # Product sold on this page.
    page_url: str
    shop_description: str  # The shop's own description, or "(none)".
    page_text: str


class CopiedText(BaseModel):
    product: str
    passages: list[str]


def shorten_links(markdown: str) -> str:
    """The page's markdown as the copy model is given it: images carry no text, so they go,
    and each link is cut to its path, which still tells one product's page from another's."""
    markdown = IMAGE.sub(" ", markdown)
    return re.sub(
        r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)",
        lambda link: f"[{link[1]}]({urlparse(link[2]).path or link[2][:60]})",
        markdown,
    )


def markdown_to_text(markdown: str) -> str:
    """Markdown as plain text: images gone, links as their words, no headings, bullets,
    table rules, emphasis or escapes."""
    markdown = IMAGE.sub(" ", markdown)
    markdown = LINK.sub(r"\1", markdown)
    markdown = re.sub(r"<[^>]+>", " ", markdown)
    markdown = re.sub(r"^\s{0,3}#{1,6}\s*", "", markdown, flags=re.M)
    markdown = re.sub(r"^\s*[-*+]\s+|^\s*\d+\.\s+", "", markdown, flags=re.M)
    markdown = re.sub(r"^\s*\|?[\s:|-]+\|?\s*$", "", markdown, flags=re.M)
    markdown = markdown.replace("|", "\n")
    markdown = re.sub(r"[*_`~]{1,3}", "", markdown)
    markdown = re.sub(r"\\([^\w\s])", r"\1", markdown)
    return re.sub(r"\\\s*$", "", markdown, flags=re.M)


def norm(text: str) -> str:
    """Text as it is compared: lower case, curly quotes made straight, no punctuation, one
    space between words."""
    text = IMAGE.sub(" ", text)
    text = LINK.sub(r"\1", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold())).strip()


def sentences(text: str) -> list[str]:
    """`text` split into sentences, at . ! ? and line breaks. A long run with no full stop
    is cut at a space near LONGEST_SENTENCE characters; bits too short to be a sentence go."""
    found = []
    for piece in re.split(r"(?<=[.!?])\s+|\n+", text):
        piece = piece.strip()
        while len(piece) > LONGEST_SENTENCE:
            cut = piece.rfind(" ", 0, LONGEST_SENTENCE)
            cut = cut if cut > LONGEST_SENTENCE // 2 else LONGEST_SENTENCE
            found.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        found.append(piece)
    return [sentence for sentence in found if len(norm(sentence)) >= SHORTEST_SENTENCE]


def match_back(passages: list[str], page: str) -> list[str]:
    """The sentences of the copied `passages` that are on `page`, the page's plain text, in
    the order copied, each once. A sentence found on the page as it is is kept; one at least
    NEAR like a page sentence is kept as the page wrote it; anything else is dropped."""
    page_norm = norm(page)
    pool = sentences(page)
    pool_norm = [norm(sentence) for sentence in pool]
    pool_words = [set(sentence.split()) for sentence in pool_norm]
    kept: list[str] = []
    for passage in passages:
        for sentence in sentences(markdown_to_text(passage)):
            copied = norm(sentence)
            if copied in page_norm:
                found = sentence
            else:
                words = set(copied.split())
                likeliest = sorted(range(len(pool)), key=lambda i: -len(words & pool_words[i]))
                score, best = max(
                    (
                        (difflib.SequenceMatcher(None, copied, pool_norm[i]).ratio(), i)
                        for i in likeliest[:CANDIDATES]
                    ),
                    key=lambda compared: compared[0],
                    default=(0.0, -1),
                )
                if score < NEAR:
                    continue
                found = pool[best]
            if found not in kept:
                kept.append(found)
    return kept


def record_prices(record: dict[str, Any]) -> str:
    """PRICES_HEADING and each price in Firecrawl's `record` of the product, one line each,
    with the price it was before a sale and the options sold at it, or "" if it gives none.
    An option whose amount isn't a number is left out."""
    variants = record.get("variants")
    options: dict[str, list[str]] = {}
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict):
            continue
        price = _money(variant.get("price"))
        if price is None:
            continue
        sale = variant.get("sale")
        before = _money(sale.get("originalPrice")) if isinstance(sale, dict) else None
        if before is not None:
            price += f", was {before}"
        names = options.setdefault(price, [])
        name = str(variant.get("title") or "")
        if name and name not in names:
            names.append(name)
    if not options:
        return ""
    lines = []
    for price, names in options.items():
        named = "; ".join(names[:OPTIONS_NAMED])
        if len(names) > OPTIONS_NAMED:
            named += f"; and {len(names) - OPTIONS_NAMED} more"
        lines.append(f"{price}: {named}" if named else price)
    return PRICES_HEADING + "\n".join(lines)


def _money(price: Any) -> str | None:
    """A record's price, {"amount": 34, "currency": "USD"}, as "34.00 USD"; None if its
    amount isn't a number."""
    if not isinstance(price, dict):
        return None
    amount = price.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
        return None
    try:
        number = float(amount)
    except TypeError, ValueError:
        return None
    if not math.isfinite(number):
        return None
    written = f"{number:,.2f}"
    currency = price.get("currency")
    return f"{written} {currency}" if isinstance(currency, str) and currency else written


def without_prices(declared: str) -> str:
    """The product data a page declares, DECLARED_DATA_HEADING and one JSON object a line,
    with every field whose name has "price" in it taken out, such as price, lowPrice and
    priceCurrency, so only the record's prices count; "" stays ""."""
    if not declared:
        return ""
    listed = declared.removeprefix(DECLARED_DATA_HEADING)
    products = [
        json.dumps(_priceless(json.loads(line)), ensure_ascii=False) for line in listed.split("\n")
    ]
    return DECLARED_DATA_HEADING + "\n".join(products)


def _priceless(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _priceless(v) for k, v in data.items() if "price" not in str(k).casefold()}
    if isinstance(data, list):
        return [_priceless(item) for item in data]
    return data
