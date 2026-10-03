"""Reading a product page through Firecrawl, which opens it in a real browser, so text that
only appears once the page's JavaScript runs, or sits in closed tabs, is read too.

Every Firecrawl call is one POST to /v2/scrape, made by `scrape()`. The "page read" asks
for the page's markdown and HTML."""

from http import HTTPStatus
from typing import Any

import httpx
from django.conf import settings

from adforge.retry import OutsideServiceDown, with_retries

from .page import Download, PageUnreadable

# How long Firecrawl may take over a page, in milliseconds: 5 minutes.
TIMEOUT_MS = 300_000
# How long to wait for Firecrawl's answer: a little longer than Firecrawl itself waits.
HTTP_TIMEOUT_SECONDS = 330
# The page read: what the copy test's markdown was fetched with (docs/scraping-test/scripts/
# scrape.py). onlyMainContent is left at Firecrawl's default.
PAGE_READ_FORMATS = ["markdown", "rawHtml"]


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
