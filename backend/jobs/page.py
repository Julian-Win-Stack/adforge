"""Fetching a product page with a plain HTTP request and pulling out its text and photos.
A headless browser is only added if plain fetches miss what real product pages show."""

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from adforge.retry import OutsideServiceDown, with_retries

MAX_PAGE_BYTES = 5_000_000
MAX_PHOTO_BYTES = 15_000_000
MAX_PHOTOS = 10
# Some shops turn away requests that don't look like they come from a browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "en",
}


class PageUnreadable(Exception):
    """The page can't be read, and trying again won't help."""


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


def download(url: str, *, max_bytes: int) -> Download:
    """GET `url`, retrying while the site is down. Raises PageUnreadable for answers that
    won't change, like 404, and OutsideServiceDown if the site stays down."""

    def attempt() -> Download:
        try:
            with httpx.stream(
                "GET", url, headers=HEADERS, follow_redirects=True, timeout=20
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise OutsideServiceDown(f"{url} answered {response.status_code}")
                if response.status_code >= 400:
                    raise PageUnreadable(f"{url} answered {response.status_code}")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise PageUnreadable(f"{url} is larger than {max_bytes:,} bytes")
                return Download(
                    final_url=str(response.url),
                    content=bytes(content),
                    content_type=response.headers.get("content-type", "").split(";")[0].strip(),
                    charset=response.charset_encoding,
                )
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"{url} could not be reached: {error}") from error

    return with_retries(attempt)


def parse(page: Download) -> ProductPage:
    soup = BeautifulSoup(page.content, "html.parser", from_encoding=page.charset)
    photo_urls = _photo_urls(soup, page.final_url)
    for hidden in soup(["script", "style", "noscript", "template", "svg"]):
        hidden.decompose()
    return ProductPage(text=soup.get_text("\n", strip=True), photo_urls=photo_urls)


def _photo_urls(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Product photos the shop itself declares: schema.org Product images, then og:image."""
    found: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        for product in _products(data):
            found.extend(_image_urls(product.get("image")))
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
