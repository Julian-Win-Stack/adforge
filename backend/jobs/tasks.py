import logging
import mimetypes
from typing import Literal

from celery import shared_task

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.gateway import call_model
from gateway.types import Handoff, Judgement, UnusableReply

from . import page
from .activity import record
from .models import Job, ProductPhoto

logger = logging.getLogger(__name__)

# Enough of the page for the check, without paying to send a whole bloated page.
PAGE_TEXT_FOR_CHECK = 20_000

CHECK_INSTRUCTIONS = """\
You check whether a product page was read properly. It was fetched with a plain HTTP \
request, so nothing that needs JavaScript ran. You get the page's visible text, followed \
by any product data the page declares for search engines.
Decide "readable" if the text names one product and says what it is, enough to script a \
short video ad from. Decide "unreadable" if the text is mostly empty, a loading screen, a \
cookie wall, a bot check or an error page, or if it lists many products (a category, \
collection or search page) instead of showing one. The link may have redirected: page_url \
is the page actually read.
Give one sentence saying why, written for the shop owner."""


class PageCheckHandoff(Handoff):
    product_url: str
    page_url: str
    page_text: str
    photo_count: int  # Product photos the page declares; they're downloaded after the check.


class PageCheck(Judgement):
    decision: Literal["readable", "unreadable"]


@shared_task
def read_page(job_id: str) -> None:
    job = Job.objects.get(pk=job_id)
    try:
        _read_page(job)
    except page.PageUnreadable as error:
        record(
            job,
            "Waiting for a working link to the product page",
            reason=str(error),
            status=Job.Status.NEEDS_WORKING_LINK,
        )
    except UnusableReply as error:
        record(
            job,
            "Could not check the product page",
            reason=f"The model's answer couldn't be used: {error}.",
            status=Job.Status.FAILED,
        )
    except OutsideServiceDown as error:
        record(
            job,
            "Could not read the product page",
            reason=f"An outside service stayed down after several tries: {error}.",
            status=Job.Status.FAILED,
        )
    except Exception:
        logger.exception("Reading the page failed for job %s", job_id)
        record(
            job,
            "Something went wrong while reading the page",
            reason="An unexpected error stopped the job; the details are in the server log.",
            status=Job.Status.FAILED,
        )


def _read_page(job: Job) -> None:
    record(
        job,
        "Reading the product page",
        reason="Every fact and picture in the ad has to come from the page, never made up.",
        status=Job.Status.READING_PAGE,
    )
    download = page.download(job.product_url, max_bytes=page.MAX_PAGE_BYTES, what="product page")
    if download.final_url != job.product_url:
        record(
            job,
            f"The link led to {download.final_url}",
            reason="The shop sent us to a different page, so that page is the one being read.",
        )
    product_page = page.parse(download)
    job.page_text = product_page.text
    job.page_html_key = file_store.save(f"jobs/{job.pk}/page.html", download.content)
    job.save(update_fields=["page_text", "page_html_key"])
    record(
        job,
        f"Stored {len(product_page.text):,} characters of page text and the page's HTML",
        reason="The script's claims will be checked against this text, and the HTML shows "
        "exactly what the page said on the day it was read.",
    )

    check = call_model(
        job=job,
        purpose="check_page",
        instructions=CHECK_INSTRUCTIONS,
        handoff=PageCheckHandoff(
            product_url=job.product_url,
            page_url=download.final_url,
            page_text=product_page.text[:PAGE_TEXT_FOR_CHECK],
            photo_count=len(product_page.photo_urls),
        ),
        output=PageCheck,
    )
    if check.decision == "unreadable":
        record(
            job,
            "Waiting for a working link to the product page",
            reason=check.reason,
            status=Job.Status.NEEDS_WORKING_LINK,
        )
        return
    record(job, "The page has what the ad needs", reason=check.reason)

    saved = _save_photos(job, product_page.photo_urls)
    if saved == 0:
        record(
            job,
            "Waiting for product photos",
            reason="The ad has to show the real product, and the page gave no product photo "
            "we could use.",
            status=Job.Status.NEEDS_PRODUCT_PHOTOS,
        )
        return
    record(
        job,
        f"Saved {saved} product photo{'s' if saved != 1 else ''}",
        reason="The ad has to show the real product, so its photos are kept with the job.",
        status=Job.Status.PAGE_READ,
    )


def _save_photos(job: Job, urls: list[str]) -> int:
    """Download and keep each photo, saying in the activity view why any was skipped."""
    saved = 0
    for url in urls:
        try:
            photo = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
        except (page.PageUnreadable, OutsideServiceDown) as error:
            record(job, f"Skipped the photo at {url}", reason=str(error).rstrip(".") + ".")
            continue
        if not photo.content_type.startswith("image/"):
            record(
                job,
                f"Skipped the photo at {url}",
                reason=f"It came back as {photo.content_type or 'an unknown type'}, not an image.",
            )
            continue
        saved += 1
        extension = mimetypes.guess_extension(photo.content_type) or ""
        key = file_store.save(f"jobs/{job.pk}/photos/{saved}{extension}", photo.content)
        ProductPhoto.objects.create(job=job, position=saved, source_url=url, file=key)
    return saved
