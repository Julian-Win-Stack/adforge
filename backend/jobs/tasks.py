import logging
import mimetypes
from typing import Literal

from celery import shared_task
from django.db import transaction

from adforge import file_store
from adforge.retry import OutsideServiceDown
from gateway.gateway import call_model
from gateway.types import Handoff, Judgement, UnusableReply

from . import page
from .activity import record
from .models import Job, ProductPhoto, Question, Scene
from .planning import (
    PAGE_TEXT_FOR_PLAN,
    PLAN_INSTRUCTIONS,
    Answer,
    PlanHandoff,
    ProducerDecision,
)

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


WORKING_LINK_QUESTION = (
    "We couldn't read one product's page from that link. What's the link to the product's own page?"
)
PRODUCT_PHOTOS_QUESTION = (
    "The page had no product photo we could use. Can you upload at least one photo of the product?"
)


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
        ready = _read_page(job)
    except page.PageUnreadable as error:
        _ask_for_working_link(job, reason=str(error))
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
    else:
        if ready:
            plan_ad.delay(job_id)


def _read_page(job: Job) -> bool:
    """Read the page and keep what the ad needs. False when the job has to wait instead."""
    if _waiting_for_answer(job):
        return False
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
        _ask_for_working_link(job, reason=check.reason)
        return False
    record(job, "The page has what the ad needs", reason=check.reason)

    saved = _save_photos(job, product_page.photo_urls)
    if saved == 0:
        ask(
            job,
            Question.Kind.PRODUCT_PHOTOS,
            PRODUCT_PHOTOS_QUESTION,
            message="Waiting for product photos",
            reason="The ad has to show the real product, and the page gave no product photo "
            "we could use.",
            status=Job.Status.NEEDS_PRODUCT_PHOTOS,
        )
        return False
    record(
        job,
        f"Saved {saved} product photo{'s' if saved != 1 else ''}",
        reason="The ad has to show the real product, so its photos are kept with the job.",
        status=Job.Status.PAGE_READ,
    )
    return True


def _ask_for_working_link(job: Job, *, reason: str) -> None:
    ask(
        job,
        Question.Kind.WORKING_LINK,
        WORKING_LINK_QUESTION,
        message="Waiting for a working link to the product page",
        reason=reason,
        status=Job.Status.NEEDS_WORKING_LINK,
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
        keep_photo(job, saved, photo.content, photo.content_type, source_url=url)
    return saved


def keep_photo(
    job: Job, position: int, content: bytes, content_type: str, *, source_url: str = ""
) -> None:
    """Store a product photo with the job. An uploaded photo has no source link."""
    extension = mimetypes.guess_extension(content_type) or ""
    key = file_store.save(f"jobs/{job.pk}/photos/{position}{extension}", content)
    ProductPhoto.objects.create(job=job, position=position, source_url=source_url, file=key)


@shared_task
def plan_ad(job_id: str) -> None:
    job = Job.objects.get(pk=job_id)
    try:
        _plan_ad(job)
    except UnusableReply as error:
        record(
            job,
            "Could not plan the ad",
            reason=f"The model's answer couldn't be used: {error}.",
            status=Job.Status.FAILED,
        )
    except OutsideServiceDown as error:
        record(
            job,
            "Could not plan the ad",
            reason=f"An outside service stayed down after several tries: {error}.",
            status=Job.Status.FAILED,
        )
    except Exception:
        logger.exception("Planning failed for job %s", job_id)
        record(
            job,
            "Something went wrong while planning the ad",
            reason="An unexpected error stopped the job; the details are in the server log.",
            status=Job.Status.FAILED,
        )


def _plan_ad(job: Job) -> None:
    if _waiting_for_answer(job):
        return
    record(
        job,
        "Planning the ad",
        reason="The plan sets the scenes, what the person says in each, and how long each lasts.",
        status=Job.Status.PLANNING,
    )
    decision = call_model(
        job=job,
        purpose="plan_ad",
        instructions=PLAN_INSTRUCTIONS,
        handoff=PlanHandoff(
            product_url=job.product_url,
            page_text=job.page_text[:PAGE_TEXT_FOR_PLAN],
            target_seconds=job.target_seconds,
            photo_count=job.photos.count(),
            answers=[
                Answer(question=asked.question, answer=asked.answer)
                for asked in job.questions.filter(
                    kind=Question.Kind.PRODUCER, answered_at__isnull=False
                )
            ],
        ),
        output=ProducerDecision,
    )
    if decision.question is not None:
        ask(
            job,
            Question.Kind.PRODUCER,
            decision.question,
            message=f"Asked: {decision.question}",
            reason=decision.reason,
            status=Job.Status.NEEDS_ANSWER,
        )
        return
    plan = decision.plan
    assert plan is not None
    with transaction.atomic():
        Scene.objects.bulk_create(
            Scene(job=job, number=number, line=scene.line, slot_seconds=scene.slot_seconds)
            for number, scene in enumerate(plan.scenes, start=1)
        )
        job.brand_colours = plan.brand_colours
        job.save(update_fields=["brand_colours"])
        count = len(plan.scenes)
        record(
            job,
            f"Planned {count} scene{'s' if count != 1 else ''}",
            reason=decision.reason,
            status=Job.Status.PLANNED,
        )


def ask(
    job: Job,
    kind: Question.Kind,
    question: str,
    *,
    message: str,
    reason: str,
    status: Job.Status,
) -> None:
    """Put a question to the user and leave the job waiting for the answer."""
    with transaction.atomic():
        Question.objects.create(job=job, kind=kind, question=question, reason=reason)
        record(job, message, reason=reason, status=status)


def _waiting_for_answer(job: Job) -> bool:
    """A task run again after a restart finds the question already asked, and keeps waiting."""
    return job.questions.filter(answered_at__isnull=True).exists()
