"""Reading a product page through Firecrawl, which opens it in a real browser, so text that
only appears once the page's JavaScript runs, or sits in closed tabs, is read too.

Every Firecrawl call is one POST to /v2/scrape, made by `scrape()`. Three are made for a
page, at the same time: the "page read" asks for its markdown and HTML; the "marked
screenshot" scrolls the page, opens its closed tabs, numbers every picture and piece of text
a visitor sees and screenshots the whole page, for picking the product's photos; and the
"product record" asks for the shop's own record of the product, as the picker's reference."""

import json
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown, with_retries

from . import page
from .page import Download, PageUnreadable

# How long Firecrawl may take over a page, in milliseconds: 5 minutes.
TIMEOUT_MS = 300_000
# How long to wait for Firecrawl's answer: a little longer than Firecrawl itself waits.
HTTP_TIMEOUT_SECONDS = 330
# The page read: what the copy test's markdown was fetched with (docs/scraping-test/scripts/
# scrape.py). onlyMainContent is left at Firecrawl's default.
PAGE_READ_FORMATS = ["markdown", "rawHtml"]
# The screenshot is a very tall picture.
MAX_SCREENSHOT_BYTES = 50_000_000

_SCRIPTS = Path(__file__).parent / "firecrawl_scripts"
# What Firecrawl's browser does on the page before the marked screenshot, exactly as tested
# (docs/scraping-test/text-test/scripts/fetch_marked.py): scroll down so lazy photos load,
# back to the top, open closed tabs (open.js), note the visible text (visible.js), number
# every picture and piece of text a visitor sees (mark.js), then screenshot the whole page.
ACTIONS: list[dict[str, Any]] = [
    {"type": "wait", "milliseconds": 3000},
    *[
        action
        for _ in range(10)
        for action in (
            {"type": "scroll", "direction": "down"},
            {"type": "wait", "milliseconds": 500},
        )
    ],
    {"type": "executeJavascript", "script": "window.scrollTo(0, 0)"},
    {"type": "wait", "milliseconds": 1000},
    {"type": "executeJavascript", "script": (_SCRIPTS / "open.js").read_text()},
    {"type": "wait", "milliseconds": 2000},
    {"type": "executeJavascript", "script": (_SCRIPTS / "visible.js").read_text()},
    {"type": "executeJavascript", "script": (_SCRIPTS / "mark.js").read_text()},
    {"type": "wait", "milliseconds": 1000},
    {"type": "screenshot", "fullPage": True},
]


class FirecrawlFailed(Exception):
    """Firecrawl couldn't read the page, and asking it again won't help: the page is read
    some other way. The message says why, in a few words."""


def scrape(body: dict[str, Any]) -> dict[str, Any]:
    """Ask Firecrawl to scrape a page, as `body` says, retrying while it is busy or down.
    Gives back its answer's `data`. Raises OutsideServiceDown if it stays down, and
    FirecrawlFailed if it turns the request down or runs out of time."""

    def attempt() -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{settings.FIRECRAWL_URL.rstrip('/')}/v2/scrape",
                headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
                json={**body, "timeout": TIMEOUT_MS},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as error:
            # Firecrawl already gave the page its whole time: asking again would wait as long.
            raise FirecrawlFailed(f"timed out after {TIMEOUT_MS // 60_000} min") from error
        except httpx.TransportError as error:
            raise OutsideServiceDown(f"Firecrawl could not be reached: {error}") from error
        # 408 is Firecrawl giving up on a slow page, which may load the next time.
        if response.status_code in (408, 429) or response.status_code >= 500:
            raise OutsideServiceDown(
                f"Firecrawl answered {response.status_code}: {_error(response)}"
            )
        if response.status_code >= 400:
            raise FirecrawlFailed(f"Firecrawl answered {response.status_code}: {_error(response)}")
        answer = response.json()
        if not answer.get("success") or not isinstance(answer.get("data"), dict):
            raise FirecrawlFailed(f"Firecrawl gave no page: {_error(response)}")
        data: dict[str, Any] = answer["data"]
        return data

    return with_retries(attempt)


def read_page(link: str) -> dict[str, Any]:
    """Firecrawl's page read of `link`: its markdown, its HTML and its metadata. Raises
    PageUnreadable when the shop answered Firecrawl with an error that won't change, as a
    plain download does; FirecrawlFailed when the shop was busy or down, or Firecrawl turned
    the request down; and OutsideServiceDown when Firecrawl itself stays down."""
    answer = scrape({"url": link, "formats": PAGE_READ_FORMATS})
    # Firecrawl answers 200 even when the shop didn't: the shop's answer is in the metadata.
    status = answer.get("metadata", {}).get("statusCode")
    if isinstance(status, int) and (status == 429 or status >= 500):
        raise FirecrawlFailed(f"the shop answered Firecrawl {status}")
    if isinstance(status, int) and status >= 400:
        url = answer["metadata"].get("url") or link
        raise PageUnreadable(f"{url} answered {_status(status)}, so trying again won't help.")
    return answer


@dataclass(frozen=True)
class Marked:
    """The marked screenshot: the marking script's lists of the page's numbered pictures
    (`imgs`) and text pieces (`secs`), the page's title, and the screenshot as a PNG."""

    marks: dict[str, Any]
    title: str
    screenshot: bytes


def marked_screenshot(link: str) -> tuple[dict[str, Any], bytes]:
    """Firecrawl's marked screenshot of `link`: its answer, and the screenshot it took.
    Raises FirecrawlFailed if it couldn't take one, or Firecrawl stays down."""
    answer = _asked(
        {
            "url": link,
            "formats": ["rawHtml", "markdown"],
            "onlyMainContent": False,
            "actions": ACTIONS,
        }
    )
    marks_in(answer)
    shots = answer.get("actions", {}).get("screenshots") or []
    if not shots or not isinstance(shots[0], str):
        raise FirecrawlFailed("Firecrawl took no screenshot")
    try:
        shot = page.download(shots[0], max_bytes=MAX_SCREENSHOT_BYTES, what="page screenshot")
    except (PageUnreadable, OutsideServiceDown) as error:
        raise FirecrawlFailed(f"its screenshot couldn't be downloaded: {error}") from error
    return answer, shot.content


def marks_in(answer: dict[str, Any]) -> dict[str, Any]:
    """The marking script's lists in a marked screenshot's answer. Each script hands back
    a JSON string; the marking script's is the one with the pictures (`imgs`)."""
    for returned in answer.get("actions", {}).get("javascriptReturns") or []:
        value = returned.get("value") if isinstance(returned, dict) else returned
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("imgs"), list):
            return parsed
    raise FirecrawlFailed("the page's pictures couldn't be numbered")


def product_record(link: str) -> dict[str, Any]:
    """Firecrawl's answer for the shop's own record of the product at `link`; its `product`
    is missing on pages where Firecrawl can't tell which product is sold. Raises
    FirecrawlFailed if Firecrawl turned it down or stays down."""
    return _asked({"url": link, "formats": ["product"]})


def record_in(answer: dict[str, Any]) -> dict[str, Any] | None:
    """The product record in a product record's answer, if Firecrawl found one."""
    record = answer.get("product")
    return record if isinstance(record, dict) and record.get("title") else None


def _asked(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return scrape(body)
    except OutsideServiceDown as error:
        raise FirecrawlFailed(str(error).rstrip(".")) from error


def as_download(answer: dict[str, Any], link: str) -> Download:
    """A page read's answer as the page it read: its HTML, and where the link led."""
    return Download(
        final_url=answer.get("metadata", {}).get("url") or link,
        content=(answer.get("rawHtml") or "").encode(),
        content_type="text/html",
        charset="utf-8",
        # No markdown: the page's visible text is used, as on the plain path.
        markdown=answer.get("markdown") or None,
    )


def _status(code: int) -> str:
    try:
        return f"{code} {HTTPStatus(code).phrase}"
    except ValueError:
        return str(code)


def _error(response: httpx.Response) -> str:
    """What Firecrawl said went wrong, from its answer's "error", else its start."""
    try:
        said = response.json().get("error")
    except ValueError:
        said = None
    return str(said or response.text[:200] or "no reason given")
