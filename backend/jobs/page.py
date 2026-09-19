"""Fetching a product page with a plain HTTP request and pulling out its text and photos.
A headless browser is only added if plain fetches miss what real product pages show."""

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from django.conf import settings

from adforge.retry import OutsideServiceDown, with_retries

MAX_PAGE_BYTES = 5_000_000
MAX_PHOTO_BYTES = 15_000_000
MAX_PHOTOS = 10
MAX_REDIRECTS = 10
# How much page text a model is sent.
PAGE_TEXT_FOR_MODEL = 200_000
DECLARED_DATA_HEADING = "\n\nProduct data the page declares for search engines:\n"

# Some shops turn away requests that don't look like they come from a browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "en",
}


class PageUnreadable(Exception):
    """The page can't be read, and trying again won't help. The message is one sentence
    saying why, written for the user."""


@dataclass(frozen=True)
class Download:
    final_url: str  # Where redirects ended up; the same as the request's URL if none.
    content: bytes
    content_type: str
    charset: str | None


@dataclass(frozen=True)
class ProductPage:
    text: str
    photo_urls: list[str]


def download(url: str, *, max_bytes: int, what: str) -> Download:
    """GET `url`, retrying while the site is down. `what` names the thing fetched, such as
    "product page", for the reasons given. Raises PageUnreadable for answers that won't
    change, like 404, and OutsideServiceDown if the site stays down."""

    def attempt() -> Download:
        current = url
        # Redirects are followed by hand so every hop gets checked.
        for _ in range(MAX_REDIRECTS + 1):
            _check_where_it_points(current)
            try:
                with httpx.stream("GET", current, headers=HEADERS, timeout=20) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers["location"])
                        continue
                    status = f"{response.status_code} {response.reason_phrase}".strip()
                    if response.status_code == 429 or response.status_code >= 500:
                        raise OutsideServiceDown(f"{current} answered {status}")
                    if response.status_code >= 400:
                        raise PageUnreadable(
                            f"{current} answered {status}, so trying again won't help."
                        )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise PageUnreadable(
                                f"{current} is too big to be a {what}, at over {max_bytes:,} bytes."
                            )
                    return Download(
                        final_url=current,
                        content=bytes(content),
                        content_type=response.headers.get("content-type", "").split(";")[0].strip(),
                        charset=response.charset_encoding,
                    )
            except httpx.TransportError as error:
                raise OutsideServiceDown(f"{current} could not be reached: {error}") from error
        raise PageUnreadable(f"{url} redirected more than {MAX_REDIRECTS} times.")

    return with_retries(attempt)


def _check_where_it_points(url: str) -> None:
    """Look the link's host up, and refuse links to this machine or a private network (like
    localhost or a cloud's settings address), so a pasted link can't make the server reach
    places only it can see. A host that changes its address between this check and the
    fetch (DNS rebinding) could still get through."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PageUnreadable(f"{url} is not a web link.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except ValueError as error:  # A port that isn't a number, or out of range.
        raise PageUnreadable(f"{url} is not a web link.") from error
    except socket.gaierror as error:
        if error.errno in _NO_SUCH_HOST:
            raise PageUnreadable(
                f"{parsed.hostname} doesn't exist, so the link can't be opened. Check it for typos."
            ) from error
        raise OutsideServiceDown(f"{url} could not be looked up: {error}") from error
    if settings.FETCH_PRIVATE_ADDRESSES:
        return
    for *_, sockaddr in addresses:
        address = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global:
            raise PageUnreadable(
                f"{url} leads to a private network address, which is never fetched."
            )


# Lookup answers meaning the name has no address, as opposed to the lookup itself failing.
_NO_SUCH_HOST = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}


def parse(page: Download) -> ProductPage:
    """The page text is the words a visitor sees, then the product data the page declares
    for search engines, where the price, brand and stock are sometimes the only copy."""
    soup = BeautifulSoup(page.content, "html.parser", from_encoding=page.charset)
    products = _declared_products(soup)
    photo_urls = _photo_urls(soup, products, page.final_url)
    for hidden in soup(["script", "style", "noscript", "template", "svg"]):
        hidden.decompose()
    text = soup.get_text("\n", strip=True)
    if products:
        declared = "\n".join(json.dumps(product, ensure_ascii=False) for product in products)
        text += f"{DECLARED_DATA_HEADING}{declared}"
    return ProductPage(text=text, photo_urls=photo_urls)


def for_model(page_text: str) -> str:
    """Enough of the page text to judge from, without paying to send a whole bloated page.
    A long page loses the end of its words, never its declared product data."""
    if len(page_text) <= PAGE_TEXT_FOR_MODEL:
        return page_text
    words, heading, declared = page_text.partition(DECLARED_DATA_HEADING)
    declared_part = (heading + declared)[:PAGE_TEXT_FOR_MODEL]
    return words[: PAGE_TEXT_FOR_MODEL - len(declared_part)] + declared_part


def _declared_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """schema.org Product objects from the page's JSON-LD blocks."""
    products: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        products.extend(_products(data))
    return products


def _photo_urls(soup: BeautifulSoup, products: list[dict[str, Any]], page_url: str) -> list[str]:
    """Product photos the shop itself declares: schema.org Product images, then og:image."""
    found = [url for product in products for url in _image_urls(product.get("image"))]
    for meta in soup.find_all("meta", property="og:image"):
        content = meta.get("content")
        if isinstance(content, str):
            found.append(content)

    urls: list[str] = []
    for raw in found:
        url = urljoin(page_url, raw.strip())
        if urlparse(url).scheme in ("http", "https") and url not in urls:
            urls.append(url)
    return urls[:MAX_PHOTOS]


def _products(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [product for item in data for product in _products(item)]
    if not isinstance(data, dict):
        return []
    if "@graph" in data:
        return _products(data["@graph"])
    kind = data.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return [data] if "Product" in kinds else []


def _image_urls(image: Any) -> list[str]:
    if isinstance(image, str):
        return [image]
    if isinstance(image, dict):
        return _image_urls(image.get("url") or image.get("contentUrl"))
    if isinstance(image, list):
        return [url for item in image for url in _image_urls(item)]
    return []
