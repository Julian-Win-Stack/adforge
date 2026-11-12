"""The work the producer's tools do on a job: reading its page, planning it, making its
person, running the planning checks, making the music, making each scene and assembling the
finished ad."""

import contextvars
import hashlib
import json
import logging
import math
import mimetypes
import re
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from adforge import file_store
from adforge.retry import OutsideServiceDown, with_retries
from chat import messages
from chat.models import Attachment, Message
from gateway import catalog
from gateway.gateway import (
    IMAGE_TYPE_NAMES,
    UnreadableImage,
    call_model,
    collect_clip,
    draw_picture,
    edit_picture,
    make_music,
    past_the_safety_filter,
    refused_by_filter,
    refused_by_halving,
    submit_broll_clip,
    transcribe,
    transcription_from,
    transcription_output,
)
from gateway.models import ModelCall
from gateway.types import (
    BlockedBySafetyFilter,
    BrollClipHandoff,
    ClipFailed,
    Handoff,
    Image,
    Judgement,
    MusicHandoff,
    ReferenceVideo,
    UnusableReply,
)
from jobs.presenter_words import presenter_words

from . import (
    assembly,
    auto_fix,
    clip_judge,
    colours,
    cut_out,
    firecrawl,
    fix_check,
    library,
    library_items,
    library_sheets,
    notices,
    owner_parts,
    page,
    page_source,
    page_text,
    photos,
    picker_grids,
    plan_quality,
    reference_photos,
)
from .checks import (
    DEFAULT_AD_SECONDS,
    FACT_CHECK_INSTRUCTIONS,
    LENGTH_ALLOWANCE_SECONDS,
    LENGTHEN_LINE_INSTRUCTIONS,
    MOST_BROLL_SHORTENINGS,
    MOST_LENGTHENINGS,
    MOST_REWRITES,
    SHORTEN_INSTRUCTIONS,
    SHORTEN_LINE_INSTRUCTIONS,
    FactCheck,
    FactCheckHandoff,
    LengthenLineHandoff,
    LineToCheck,
    Problem,
    RewriteHandoff,
    RewrittenScene,
    ShortenHandoff,
    ShortenLineHandoff,
    Wrong,
    count_words,
    default_length_fit,
    fact_check_for,
    line_seconds,
    most_words_in_clips,
    rewritten_line_for,
    rewritten_scene_for,
    shortened_script_for,
    target_length_fit,
)
from .job_locks import one_job_at_a_time as _one_job_at_a_time
from .library import Drawn, PictureLabel, SentList, watch_of
from .models import (
    BROLL_FIELDS,
    CLIP_ONLY_INPUTS,
    PLANNED_FOR_THE_ACTION,
    QUALITY_FIELDS,
    STEP_BROLL_FIELDS,
    AgentFix,
    AgentFixTry,
    Job,
    OwnerQuestion,
    ProducedItem,
    ProductPhoto,
    ProductVideo,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from .notices import post_left_out_by_the_filter, post_notice, post_refused_by_the_filter
from .owner_direction import (
    keeps_exact_line,
    owner_changed_scene,
    remember_line,
    tell_line_change,
    words_to_keep,
)
from .pace import (
    FIXED_WORDS_PER_SECOND,
    LONGEST_BROLL_LINE_SECONDS,
    SECONDS_AFTER_THE_LINE,
    SHORTEST_BROLL_LINE_SECONDS,
    SHORTEST_CLIP_SECONDS,
    fewest_words_in_a_broll_line,
    h3_clip_seconds,
    most_words_in_a_broll_line,
)
from .picture_check import (
    ASKS,
    FAILS_TO_FAIL,
    STARTING_PICTURE_CHECK,
    StartingPictureVerdict,
)
from .picture_rules import NO_FACE_RULE
from .plan_rules import DOING_IT, broll_can_talk
from .planning import (
    BROLL_ONE_ACTION_INSTRUCTIONS,
    END_CARD_SECONDS,
    HOW_TO_METHOD,
    NOT_A_CLAIM,
    PLAN_INSTRUCTIONS,
    SPOKEN_LINE_RULE,
    ChatMessage,
    Need,
    PlanHandoff,
    ProducerDecision,
    brand_missing,
    producer_decision_for,
    rules_not_kept,
)
from .reference_photos import ReferencePhotoUnusable
from .scene_rules import scene_rules_of
from .scenes import (
    AD_ROOM_WORDS,
    BEFORE_PICTURE_INSTRUCTIONS,
    CHANGED_PLACE_WORDS,
    PERSON_IN_ROOM_PROMPT,
    ROOM_ALONE_PROMPT,
    ROOM_FROM_PERSON_PROMPT,
    SCENE_NEW_PLACE_PROMPT,
    SCENE_PLACE_EDIT_PROMPT,
    BeforePictureHandoff,
    BeforePicturePlan,
    BrollSceneToDraw,
)
from .threads import closing_its_connection as _closing_its_connection

if TYPE_CHECKING:
    from agents.models import ToolCall

CHECK_INSTRUCTIONS = """\
You check whether a product page was read properly. It may have been read in a real \
browser, or with a plain HTTP request, in which case nothing that needs JavaScript ran. You \
get the page's visible text, followed by any product data the page declares for search \
engines.
Decide "readable" if the text names one product and says what it is, enough to script a \
short video ad from. Decide "unreadable" if the text is mostly empty, a loading screen, a \
cookie wall, a bot check or an error page, or if it lists many products (a category, \
collection or search page) instead of showing one. The link may have redirected: page_url \
is the page actually read. The text may instead have been pasted by the shop owner from the \
page, when it couldn't be read from the link: judge it the same way.
Give one sentence saying why, written for the shop owner."""

logger = logging.getLogger(__name__)


class PageCheckHandoff(Handoff):
    product_url: str
    page_url: str
    page_text: str
    photo_count: int  # Product photos the page declares; they're downloaded after the check.


class PageCheck(Judgement):
    decision: Literal["readable", "unreadable"]


NO_FIRECRAWL = (
    "Firecrawl isn't set up (FIRECRAWL_API_KEY is empty), so the page was read with a plain "
    "download instead: text that needs JavaScript or sits in closed tabs may be missing from "
    "the ad. This is probably a setup mistake."
)


@dataclass(frozen=True)
class PageRead:
    """What reading a page gave: the page itself, Firecrawl's marked screenshot of it and
    its record of the product. Without Firecrawl's key the other two are None; a call that
    failed gives why."""

    download: page.Download
    marked: firecrawl.Marked | firecrawl.FirecrawlFailed | None = None
    # The shop's record of the product; None if Firecrawl found none, or wasn't asked.
    record: dict[str, Any] | firecrawl.FirecrawlFailed | None = None


def fetch_page(job: Job, link: str) -> PageRead:
    """Read the page at `link` through Firecrawl, or with a plain download when Firecrawl
    isn't set up or can't read it; each fallback posts a notice. Firecrawl's three calls are
    made at the same time. A link to a private network address is refused before anything
    is asked of anyone. Raises PageUnreadable for a page that won't read, and
    OutsideServiceDown if the shop stays down."""
    with_retries(lambda: page.check_where_it_points(link))
    if not settings.FIRECRAWL_API_KEY and page_source.current_source() is None:
        post_notice(job, notices.NO_FIRECRAWL, NO_FIRECRAWL, Message.Level.PROBLEM)
        return PageRead(_plain_download(link))
    answers = _firecrawl_answers(job, link)
    marked, record, read = answers["marked"], answers["product"], answers["page"]
    if isinstance(read, page.PageUnreadable):
        raise read
    if isinstance(read, Exception):
        post_notice(
            job,
            notices.FIRECRAWL_FAILED,
            f"Firecrawl couldn't open the page ({read}). Read it with the plain download "
            "instead, so text that needs JavaScript or sits in closed tabs may be missing from "
            "the ad.",
            Message.Level.PROBLEM,
        )
        download = _plain_download(link)
    else:
        download = firecrawl.as_download(read, link)
    return PageRead(
        download,
        marked=_failed(marked) if isinstance(marked, Exception) else _as_marked(job, marked),
        record=_failed(record) if isinstance(record, Exception) else firecrawl.record_in(record),
    )


@dataclass(frozen=True)
class CheckedRead:
    """A page read, as parsed, and the check of whether it can be used."""

    read: PageRead
    product_page: page.ProductPage
    check: PageCheck


def read_and_check_page(job: Job, link: str) -> CheckedRead:
    """Read the page at `link` (`fetch_page`) and check it can be used (`check_page`). With
    Firecrawl set up, a page that can't be used, such as a shop's bot check, is never kept
    for a read run again, and is read once more through Firecrawl, fresh (firecrawl.FRESH),
    and checked again, even when the first read was the plain download Firecrawl failed
    over to: a read that works is kept, and a notice says so. Raises as `fetch_page` does."""
    read = fetch_page(job, link)
    product_page = page.parse(read.download)
    check = check_page(job, read.download, product_page)
    if check.decision == "readable" or (
        not settings.FIRECRAWL_API_KEY and page_source.current_source() is None
    ):
        return CheckedRead(read, product_page, check)
    # A read run again, or the same link sent again, asks Firecrawl again.
    if job.firecrawl.get("files", {}).pop("page", None) is not None:
        job.firecrawl.pop("picker_images", None)
        job.save(update_fields=["firecrawl"])
    try:
        source = page_source.current_source()
        answer = (source.read_page if source else firecrawl.read_page)(link, fresh=True)
    except (firecrawl.FirecrawlFailed, OutsideServiceDown, page.PageUnreadable) as error:
        logger.warning("Job %s: reading %s again, fresh, failed: %s", job.pk, link, error)
        return CheckedRead(read, product_page, check)
    download = firecrawl.as_download(answer, link)
    fresh_page = page.parse(download)
    fresh_check = check_page(job, download, fresh_page)
    if fresh_check.decision == "unreadable":
        return CheckedRead(read, product_page, check)
    job.firecrawl["files"]["page"] = file_store.save(
        f"jobs/{job.pk}/firecrawl/page.json", json.dumps(answer, ensure_ascii=False).encode()
    )
    job.save(update_fields=["firecrawl"])
    post_notice(
        job,
        notices.PAGE_READ_AGAIN,
        f"The page's first read couldn't be used ({check.reason}), so it was read again, "
        "fresh, and the second read worked.",
        Message.Level.INFO,
    )
    return CheckedRead(PageRead(download, read.marked, read.record), fresh_page, fresh_check)


def _failed(error: Exception) -> firecrawl.FirecrawlFailed:
    return (
        error
        if isinstance(error, firecrawl.FirecrawlFailed)
        else firecrawl.FirecrawlFailed(str(error))
    )


def page_addresses(link: str) -> list[str]:
    """Where the page at `link` is: the address it answers from, after any redirects, and
    the one it names as its own, if any. Read with a plain download, the page read's own,
    which nothing is paid for and nothing is kept from. Raises PageUnreadable for a page that
    won't open, and OutsideServiceDown if the shop stays down."""
    with_retries(lambda: page.check_where_it_points(link))
    download = _plain_download(link)
    return [address for address in (download.final_url, page.parse(download).canonical) if address]


def _plain_download(link: str) -> page.Download:
    return page.download(link, max_bytes=page.MAX_PAGE_BYTES, what="product page")


type _Answer = dict[str, Any]
type _FirecrawlCall = Literal["page", "marked", "product"]


def _firecrawl_answers(job: Job, link: str) -> dict[_FirecrawlCall, _Answer | Exception]:
    """Firecrawl's three answers for `link`: the ones this job saved for it, if it read the
    link before, so a read run again is given the same page and pays for nothing twice; else
    new ones, asked for at the same time and each saved as soon as it arrives. A call that
    failed gives its error, which isn't saved: a read run again asks again."""
    saved: dict[str, str] = (
        job.firecrawl.get("files", {}) if job.firecrawl.get("url") == link else {}
    )
    if job.firecrawl.get("url") != link:
        job.firecrawl = {"url": link, "files": saved}
    answers: dict[_FirecrawlCall, _Answer | Exception] = {
        call: json.loads(file_store.read(saved[call]))
        for call in ("page", "marked", "product")
        if call in saved
    }
    source = page_source.current_source() or firecrawl
    asks: dict[_FirecrawlCall, Callable[[], Any]] = {
        "page": lambda: source.read_page(link),
        "marked": lambda: source.marked_screenshot(link),
        "product": lambda: source.product_record(link),
    }
    # The calls only wait on Firecrawl; the answers are saved here, by this thread.
    with ThreadPoolExecutor(max_workers=3) as pool:
        asked = {
            pool.submit(contextvars.copy_context().run, ask): call
            for call, ask in asks.items()
            if call not in answers
        }
        for done in as_completed(asked):
            call = asked[done]
            try:
                answer = done.result()
            except (firecrawl.FirecrawlFailed, OutsideServiceDown, page.PageUnreadable) as error:
                answers[call] = error
                continue
            if call == "marked":
                answer, shot = answer
                saved["screenshot"] = file_store.save(
                    f"jobs/{job.pk}/firecrawl/screenshot.png", shot
                )
            answers[call] = answer
            # The picker's pictures were made from the answers before this one.
            job.firecrawl.pop("picker_images", None)
            saved[call] = file_store.save(
                f"jobs/{job.pk}/firecrawl/{call}.json",
                json.dumps(answer, ensure_ascii=False).encode(),
            )
            job.save(update_fields=["firecrawl"])
    return answers


def _as_marked(job: Job, answer: _Answer) -> firecrawl.Marked | firecrawl.FirecrawlFailed:
    try:
        marks = firecrawl.marks_in(answer)
    except firecrawl.FirecrawlFailed as error:
        return error
    return firecrawl.Marked(
        # Every picture's size known before the picker's list is made.
        marks=picker_grids.measured(marks),
        title=str(answer.get("metadata", {}).get("title") or ""),
        screenshot=file_store.read(job.firecrawl["files"]["screenshot"]),
    )


def copy_page_text(
    job: Job,
    download: page.Download,
    product_page: page.ProductPage,
    record: dict[str, Any] | firecrawl.FirecrawlFailed | None,
) -> str:
    """This product's own text: what a model copies out of the page about it, each sentence
    matched back to the page, and each sentence of the page's own description that neither
    the page's words nor its declared data hold, then its prices, from Firecrawl's `record`
    of it or else the one price the page declares, then the product data the page declares
    without its prices.
    However little is kept is used. A failed copy call is raised: the page isn't read."""
    if download.markdown is not None:
        given = page_text.shorten_links(download.markdown)
        on_the_page = page_text.markdown_to_text(download.markdown)
    else:
        given = on_the_page = product_page.words
    copied = call_model(
        job=job,
        purpose="copy_page_text",
        instructions=page_text.COPY_INSTRUCTIONS,
        handoff=page_text.CopyHandoff(
            product=product_page.name,
            page_url=download.final_url,
            shop_description=product_page.description or "(none)",
            page_text=given,
        ),
        output=page_text.CopiedText,
        # A page read again, as after a worker stopped, isn't copied and paid for twice.
        pay_once=True,
    )
    prices = page_text.prices(record if isinstance(record, dict) else None, product_page.declared)
    kept = page_text.match_back(copied.passages, on_the_page)
    # Each sentence of the page's own description is kept when it is in no other text the
    # planner gets: not on the page, where the copy kept only what it chose, nor in the
    # declared data.
    elsewhere = [page_text.norm(text) for text in (on_the_page, product_page.declared)]
    kept += [
        sentence
        for sentence in page_text.sentences(product_page.own_description)
        if not any(page_text.norm(sentence) in text for text in elsewhere)
    ]
    return "\n".join(kept) + prices + page_text.without_prices(product_page.declared)


@dataclass(frozen=True)
class Picked:
    """The picker's photo links, gallery first, or None when the page's declared photos are
    used instead; and the notices to post about how they were picked."""

    urls: list[str] | None
    notices: list[tuple[str, Message.Level]]
    # The picked links the picker marked with a stranger's face.
    faces: frozenset[str] = frozenset()
    # Whether the picker saw a block or error page instead of the shop's product page.
    blocked: bool = False
    # The picked links the picker didn't note for a face: the record's official photos.
    unnoted: frozenset[str] = frozenset()
    # The video players the picker picked from the product's gallery, as the page script
    # found them.
    videos: tuple[dict[str, Any], ...] = ()
    # Each picked link that was sized up, with the link as the page gave it: fetched instead
    # when the biggest size can't be, as when a size-like word was the shop's own name.
    as_given: dict[str, str] = field(default_factory=dict)
    # The picker's fixed fields for each picked link it labelled.
    labels: dict[str, PictureLabel] = field(default_factory=dict)
    # Each picked link's copy group: every picked link the picker gave the same copy group,
    # in pick order. A link it gave no label is a group of its own.
    copies: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The product's colour list, as the picker wrote it (#273 point 2).
    colours: tuple[str, ...] = ()
    # The picked links the picker saw only small or not at all (photos.seen_small): each is
    # labelled again from its own file, and its copies found by comparing files.
    seen_small: frozenset[str] = frozenset()


DECLARED_INSTEAD = (
    "Used the photos the page declares for search engines instead, which may include other "
    "products' photos and miss some of this one's."
)


PAGE_BLOCKED = (
    "The shop's page blocked our page reader: it showed a block or error page instead of the "
    f"product, so no photos could be picked off it. {DECLARED_INSTEAD}"
)


def copy_and_pick(job: Job, read: PageRead, product_page: page.ProductPage) -> tuple[str, Picked]:
    """This product's own text and photos, copied and picked off the page at the same time.
    A failed copy call posts a notice and is raised: the page isn't read."""
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        picking = pool.submit(context.run, _closing_its_connection, pick_photos, job, read)
        try:
            text = copy_page_text(job, read.download, product_page, read.record)
        except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
            post_notice(
                job,
                notices.PAGE_TEXT_NOT_COPIED,
                f"Picking this product's own text out of the page failed ({error}), so the "
                "page wasn't read and nothing was kept from it. Ask to read it again.",
                Message.Level.PROBLEM,
            )
            raise
    return text, picking.result()


def pick_photos(job: Job, read: PageRead) -> Picked:
    """Have a model pick this product's photos off Firecrawl's marked screenshot of the page,
    with the shop's record of the product and its official photos as a reference. Without a
    marked screenshot, or when the picker fails or picks nothing, the page's declared photos
    are used. Says what fell back in the notices; posts none itself."""
    if read.marked is None:  # Firecrawl isn't set up, which was already said.
        return Picked(None, [])
    if isinstance(read.marked, firecrawl.FirecrawlFailed):
        return Picked(
            None,
            [
                (
                    f"Firecrawl couldn't take the page's marked screenshot ({read.marked}), so "
                    f"this product's photos couldn't be picked off it. {DECLARED_INSTEAD}",
                    Message.Level.PROBLEM,
                )
            ],
        )
    told: list[tuple[str, Message.Level]] = []
    record = read.record
    if isinstance(record, firecrawl.FirecrawlFailed):
        told.append(
            (
                f"Firecrawl couldn't get the shop's record of the product ({record}), so its "
                "photos were picked without the official photos to compare them with: a "
                "look-alike product's photo is a little more likely to get through.",
                Message.Level.PROBLEM,
            )
        )
        record = None
    elif record is None:
        told.append(
            (
                "Firecrawl found no record of the product on the page, as on many pages, so its "
                "photos were picked without the shop's official photos to compare them with.",
                Message.Level.INFO,
            )
        )
    marked = read.marked
    images, shown = _picker_images(job, record, marked.marks, marked.screenshot)

    def pick(images: list[Image], shown: photos.ShownLines) -> photos.PickedPhotos:
        return call_model(
            job=job,
            purpose="pick_photos",
            instructions=photos.PICK_INSTRUCTIONS,
            handoff=photos.handoff(record, marked.marks, marked.title, shown),
            output=photos.PickedPhotos,
            images=images,
            # A page read again, as after a worker stopped, isn't picked and paid for twice.
            pay_once=True,
        )

    try:
        picked, left_out = _picked_past_the_safety_filter(job, pick, images, shown)
    except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
        told.append(
            (
                f"Picking this product's photos off the page failed ({error}). {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, told)
    if left_out:
        told.append((_left_out_by_the_filter(left_out), Message.Level.PROBLEM))
    if picked.page_blocked:
        told.append((PAGE_BLOCKED, Message.Level.PROBLEM))
        return Picked(None, told, blocked=True)
    links = photos.picked_links(
        picked, read.marked.marks, photos.record_photo_urls(record) if record else []
    )
    videos = tuple(_picked_videos(picked, read.marked.marks))
    if not links:
        told.append(
            (
                f"The photo picker found no photo of this product on the page. {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, told, videos=videos)
    labels, copies, unlabelled = _labels_and_copies(picked, links)
    if unlabelled:
        told.append((_kept_unlabelled(unlabelled), Message.Level.PROBLEM))
    shown_tiles = {tile for image in images for tile in picker_grids.tiles_of(image.label)} - set(
        left_out
    )
    seen_big = {
        link.url
        for link in links
        if not photos.seen_small(link.mark, read.marked.marks, shown_tiles)
    }
    return Picked(
        [link.url for link in links],
        told,
        frozenset(link.url for link in links if link.has_face),
        unnoted=frozenset(link.url for link in links if link.has_face is None),
        videos=videos,
        as_given={link.url: link.as_given for link in links if link.as_given != link.url},
        labels=labels,
        copies=copies,
        colours=tuple(picked.colours),
        seen_small=frozenset(link.url for link in links if link.url not in seen_big),
    )


def _labels_and_copies(
    picked: photos.PickedPhotos, links: list[photos.PickedLink]
) -> tuple[dict[str, PictureLabel], dict[str, tuple[str, ...]], list[str]]:
    """Each picked link's label from the picker, from the last of its marks with fields
    that keep the library's rules; each link's copy group, by that mark's; and the first
    mark of each link left with no such fields."""
    by_mark = {picture.picture: picture for picture in picked.pictures}
    labels: dict[str, PictureLabel] = {}
    group_of: dict[str, int] = {}
    for link in links:
        found = by_mark.get(link.mark)
        if found is None:
            continue
        try:
            labels[link.url] = found.label_of(picked.colours)
        except ValueError:
            continue
        group_of[link.url] = found.copy_group
    groups: dict[int, list[str]] = {}
    for url, group in group_of.items():
        groups.setdefault(group, []).append(url)
    copies = {
        link.url: tuple(groups[group_of[link.url]]) if link.url in group_of else (link.url,)
        for link in links
    }
    unlabelled: dict[str, str] = {}
    for link in links:
        if link.url not in labels:
            unlabelled.setdefault(link.url, link.mark)
    return labels, copies, list(unlabelled.values())


def _kept_unlabelled(marks: list[str]) -> str:
    """The notice naming the picked pictures kept without the picker's fields (the labelling
    call labels them for the library, which its own notice says)."""
    listed = f"{', '.join(marks[:-1])} and {marks[-1]}" if len(marks) > 1 else marks[0]
    return (
        f"The photo picker kept picture{'s' if len(marks) > 1 else ''} {listed} off the page "
        "without usable fields saying what each shows (its kind, side, colour and label), so "
        f"{'they' if len(marks) > 1 else 'it'} may each be kept twice if the page has another "
        "copy."
    )


def _picked_past_the_safety_filter(
    job: Job,
    pick: Callable[[list[Image], photos.ShownLines], photos.PickedPhotos],
    images: list[Image],
    shown: photos.ShownLines,
) -> tuple[photos.PickedPhotos, list[str]]:
    """The picker's answer, and the tiles of the grids left out to get it: the grids beside
    the screenshot may be left out past the safety filter, never the screenshot itself, and
    the picker isn't told of a grid's pictures it isn't shown. The tiles of the grids left out
    are then halved to find the ones the filter refuses (requirement 22), and the picker asked
    once more, shown the rest of them drawn again in grids, else the answer a probe got
    shown the most of them; when none can be had, the answer without the grids stands.
    Raises BlockedBySafetyFilter when it refuses even the screenshot."""

    def tiles(grids: list[Image]) -> list[str]:
        return [tile for grid in grids for tile in picker_grids.tiles_of(grid.label)]

    def asked(kept: list[Image]) -> photos.PickedPhotos:
        return pick(
            kept,
            picker_grids.without_tiles(shown, [t for t in tiles(images) if t not in tiles(kept)]),
        )

    picked, left = past_the_safety_filter(
        asked, images, may_leave_out=lambda image: not SCREENSHOT_PART.match(image.label)
    )
    if not left:
        return picked, []
    try:
        return _past_refused_tiles(job, asked, images, left)
    except (BlockedBySafetyFilter, UnusableReply, OutsideServiceDown, OSError) as error:
        # No probe got through, or a probe failed otherwise: the pick made without the
        # grids stands.
        logger.warning(
            "The picker's refused pictures weren't found, so their grids are out: %s", error
        )
        return picked, tiles(left)


def _past_refused_tiles(
    job: Job,
    asked: Callable[[list[Image]], photos.PickedPhotos],
    images: list[Image],
    left: list[Image],
) -> tuple[photos.PickedPhotos, list[str]]:
    """The picker's answer shown `images` but the tiles of the grids `left` the safety filter
    refuses, and those tiles, for a picker refused when shown `images`: asked once more as
    it was, as the filter may refuse a call once and pass it the next time; then the tiles
    are found by halving (requirement 22) and the picker asked once on the rest, drawn again
    in grids. When the filter refuses that too, the answer a probe got while shown the most
    tiles stands. Raises when no answer can be had."""
    try:
        return asked(images), []
    except BlockedBySafetyFilter:
        pass
    kept = [image for image in images if image not in left]
    cut = [
        tile
        for grid in left
        for tile in picker_grids.tiles_cut_from(grid.label, file_store.read(grid.key))
    ]

    # Every answer that got through while halving, with the tiles it was shown.
    passed: list[tuple[list[picker_grids.CutTile], photos.PickedPhotos]] = []

    def asked_with(some: list[picker_grids.CutTile]) -> photos.PickedPhotos:
        answer = asked(_with_grids_drawn_again(job, kept, some))
        passed.append((some, answer))
        return answer

    refused = refused_by_halving(lambda some: refused_by_filter(asked_with, some), cut)
    try:
        return asked_with([tile for tile in cut if tile not in refused]), [
            tile.label for tile in refused
        ]
    except BlockedBySafetyFilter:
        # Pictures the filter refuses only together (Wet Brush's back photo, Nov 10): the
        # answer that got through shown the most of them stands, not the one shown none.
        if not passed:
            raise
        shown, answer = max(passed, key=lambda each: len(each[0]))
        logger.warning(
            "The picker's last ask was refused, so the answer shown %d of %d tiles stands",
            len(shown),
            len(cut),
        )
        return answer, [tile.label for tile in cut if tile not in shown]


def _with_grids_drawn_again(
    job: Job, kept: list[Image], tiles: list[picker_grids.CutTile]
) -> list[Image]:
    """`kept` and `tiles` of the grids left out drawn again into grids of their kinds, each
    kind's grids in the place the picker is shown that kind. Each grid is kept in the file
    store under a name made from its picture, so the same grid drawn again is kept once."""
    drawn: list[Image] = []
    for kind, (across, side) in picker_grids.GRID_SHAPES.items():
        of_kind = [(tile.label, tile.picture) for tile in tiles if tile.kind == kind]
        for label, content in picker_grids.grids(kind, of_kind, across, side):
            named = hashlib.sha256(content).hexdigest()[:24]
            key = file_store.save_once(
                f"jobs/{job.pk}/firecrawl/grid-{named}.jpg",
                partial(bytes, content),
                whole=file_store.whole_picture,
            )
            drawn.append(Image(label, key))
    order = [*picker_grids.GRID_SHAPES]

    def place(image: Image) -> int:
        kind = picker_grids.kind_of(image.label)
        return order.index(kind) + 1 if kind in order else 0

    return sorted([*kept, *drawn], key=place)


def _left_out_by_the_filter(tiles: list[str]) -> str:
    """The notice saying which pictures beside the screenshot the picker went without."""
    kinds = {"R": "official photos", "I": "hidden pictures", "V": "video players"}
    named = []
    for letter, kind in kinds.items():
        numbers = [tile.split()[0] for tile in tiles if tile.startswith(letter)]
        if numbers:
            listed = (
                f"{', '.join(numbers[:-1])} and {numbers[-1]}" if len(numbers) > 1 else numbers[0]
            )
            named.append(f"{kind} {listed}")
    return (
        "The AI model's safety filter refused to look at some of the pictures beside the "
        f"page's screenshot ({'; '.join(named)}), so this product's photos were picked "
        "without them."
    )


def _picked_videos(picked: photos.PickedPhotos, marks: dict[str, Any]) -> list[dict[str, Any]]:
    """The video players the picker picked, as the page script found them. Numbers of
    players it wasn't shown are dropped."""
    players = {player["n"]: player for player in picker_grids.videos(marks)}
    return [players[number] for number in picked.gallery_videos if number in players]


# How each part of the screenshot is labelled for the picker, and how one is known.
SCREENSHOT_PART_LABEL = "Part {} of {} (from {} px down the page):"
SCREENSHOT_PART = re.compile(r"Part \d+ of \d+ \(from \d+ px down the page\):$")
# The most pictures the picker's model takes in one call.
MOST_PICKER_IMAGES = 50


def _picker_images(
    job: Job, record: dict[str, Any] | None, marks: dict[str, Any], screenshot: bytes
) -> tuple[list[Image], photos.ShownLines]:
    """The pictures the picker is shown, and the lines naming those beside the screenshot:
    the screenshot's parts, top to bottom, then the grids of the record's official photos,
    the hidden pictures and the video players. Kept with the job's Firecrawl answers, so a
    read run again shows the same files and pays for nothing twice."""
    kept, lines = job.firecrawl.get("picker_images"), job.firecrawl.get("picker_lines")
    if kept is not None and lines is not None:
        return [Image(label, key) for label, key in kept], photos.ShownLines(**lines)
    images: list[Image] = []
    parts = photos.parts(screenshot)
    for number, (top, part) in enumerate(parts, 1):
        key = file_store.save(f"jobs/{job.pk}/firecrawl/part-{number}.jpg", part)
        images.append(Image(SCREENSHOT_PART_LABEL.format(number, len(parts), top), key))
    shown = picker_grids.beside_the_screenshot(record, marks)
    for number, (label, grid) in enumerate(shown.grids, 1):
        key = file_store.save(f"jobs/{job.pk}/firecrawl/grid-{number}.jpg", grid)
        images.append(Image(label, key))
    if len(images) > MOST_PICKER_IMAGES:
        raise ValueError(
            f"The picker can be shown {MOST_PICKER_IMAGES} pictures, not {len(images)}"
        )
    job.firecrawl["picker_images"] = [[image.label, image.key] for image in images]
    job.firecrawl["picker_lines"] = shown.lines.model_dump()
    job.save(update_fields=["firecrawl"])
    return images, shown.lines


def keep_page(job: Job, download: page.Download, product_page: page.ProductPage, text: str) -> None:
    """Store the product's own text, the page's whole text and its original HTML with the
    job. Only for a page found to show its product, whose photos are kept: the job counts as
    having its page from then."""
    job.page_text = text
    job.page_text_full = product_page.text
    job.page_html_key = file_store.save(f"jobs/{job.pk}/page.html", download.content)
    # Where the page is, so another link to the same product is known as this ad's.
    job.page_final_url = download.final_url[:2000]
    job.page_canonical_url = product_page.canonical[:2000]
    job.status = Job.Status.PAGE_READ
    job.save(
        update_fields=[
            "page_text",
            "page_text_full",
            "page_html_key",
            "page_final_url",
            "page_canonical_url",
            "status",
        ]
    )


def check_page(job: Job, download: page.Download, product_page: page.ProductPage) -> PageCheck:
    """Have a model judge whether the page shows one product, enough to make an ad from."""
    return call_model(
        job=job,
        purpose="check_page",
        instructions=CHECK_INSTRUCTIONS,
        handoff=PageCheckHandoff(
            product_url=job.product_url,
            page_url=download.final_url,
            page_text=page.for_model(product_page.text),
            photo_count=len(product_page.photo_urls),
        ),
        output=PageCheck,
        # A page read again, as after a worker stopped, isn't checked and paid for twice.
        pay_once=True,
    )


@dataclass(frozen=True)
class SkippedPhoto:
    """A photo on the page that wasn't kept, and one sentence saying why."""

    url: str
    reason: str


def save_photos(
    job: Job,
    urls: list[str],
    *,
    copies: Mapping[str, Sequence[str]] | None = None,
    labels: Mapping[str, PictureLabel] | None = None,
    faces: frozenset[str] | None = None,
    unnoted: frozenset[str] = frozenset(),
    as_given: dict[str, str] | None = None,
    seen_small: frozenset[str] = frozenset(),
) -> list[SkippedPhoto]:
    """Download and keep each photo, after any the job already has, up to MAX_PHOTOS, with
    the picker's fields for it in `labels`. With the picker's `copies`, each link's copy
    group, one photo of each group is kept, the first that can be, and the one kept has a
    face if any copy does; a link of a group is also the same photo at any other size.
    Without them, as for the page's declared photos, every link is kept. `faces` are the
    links the picker marked with a face; without them, or for the `unnoted` links it didn't
    note, each photo kept is given its Face note by a call of its own. A link that can't be
    fetched is fetched `as_given` instead, when that names another link for it. Gives back
    each one that was skipped, with why.

    A link the picker saw only small (`seen_small`) is kept as seen small, to be labelled
    again from its own file (jobs.library_items.add_kept_photos), and the picker's copy
    groups don't count for it: it is a copy of a kept photo, or a kept photo seen small is a
    copy of it, only when their files look alike (photos.alike).

    A page read again, as after a crash or by a second read of the same page, only adds the
    photos the job doesn't have yet: one at a link already kept, or with `copies` one in a
    copy group a kept photo was kept for, is left out. Photos already kept are never deleted
    or changed, so their numbers and what the plan marked on them stay."""
    page_photos = job.photos.exclude(source_url="").filter(from_video=None).order_by("position")
    saved = job.photos.aggregate(last=Max("position"))["last"] or 0
    links = set(page_photos.values_list("source_url", flat=True))
    # The photo kept for each link of its copy group, by what its links share at every size.
    kept_for: dict[str, ProductPhoto] = {}
    for had in page_photos:
        _keep_for_its_copies(kept_for, had, [had.source_url, *had.copy_links])
    # Each kept photo's fingerprint: only needed when a link was seen small, so a read with
    # none reads no kept file again.
    prints: list[tuple[int, ProductPhoto]] = []
    if seen_small:
        for had in page_photos:
            if (kept_print := photos.fingerprint_of(file_store.read(had.file))) is not None:
                prints.append((kept_print, had))
    this_read: list[ProductPhoto] = []
    count = page_photos.count()
    skipped = []
    for url in urls:
        if count >= page.MAX_PHOTOS:
            break
        if url in links:
            continue
        group = list(copies.get(url, (url,))) if copies is not None else []
        if url in seen_small:
            group = [url]
        else:
            group = [link for link in group if link not in seen_small]
        if copies is not None and (copy := _kept_copy(kept_for, [url, *group])) is not None:
            if copy in this_read:
                _merge_face(copy, url, faces)
            continue
        try:
            photo = _photo_at(url, (as_given or {}).get(url))
        except (page.PageUnreadable, OutsideServiceDown) as error:
            skipped.append(SkippedPhoto(url, str(error).rstrip(".") + "."))
            continue
        # Only formats the models can read, so every kept photo can be shown to them: judged
        # by its bytes, any other picture Pillow opens is kept as a PNG.
        made_readable = photos.readable(photo.content)
        if made_readable is None:
            skipped.append(
                SkippedPhoto(
                    url,
                    f"It came back as {photo.content_type or 'an unknown type'}, "
                    f"not a {IMAGE_TYPE_NAMES} image.",
                )
            )
            continue
        content, content_type = made_readable
        size = photos.size_of(content)
        if size is not None and min(size) < photos.SMALLEST_PHOTO:
            skipped.append(
                SkippedPhoto(
                    url, f"It's only {size[0]} by {size[1]} px, too small to be a product photo."
                )
            )
            continue
        fingerprint = photos.fingerprint_of(content) if seen_small else None
        if (
            fingerprint is not None
            and (copy := _alike_kept(prints, fingerprint, any_kept=url in seen_small)) is not None
        ):
            if copy in this_read:
                _merge_face(copy, url, faces)
            continue
        saved += 1
        count += 1
        links.add(url)
        has_face = url in faces if faces is not None and url not in unnoted else None
        kept = keep_photo(
            job,
            saved,
            content,
            content_type,
            source_url=url,
            has_face=has_face,
            picked_as=(labels or {}).get(url),
            copy_links=group,
            seen_small=url in seen_small,
        )
        this_read.append(kept)
        _keep_for_its_copies(kept_for, kept, [url, *group])
        if fingerprint is not None:
            prints.append((fingerprint, kept))
    return skipped


def _alike_kept(
    prints: Sequence[tuple[int, ProductPhoto]], fingerprint: int, *, any_kept: bool
) -> ProductPhoto | None:
    """The kept photo whose file looks like the one with `fingerprint`, if one does: among
    every kept photo when `any_kept` (the new one was seen small), else among the kept photos
    seen small only, as the picker's copy groups decide the rest."""
    return next(
        (
            kept
            for kept_print, kept in prints
            if (any_kept or kept.seen_small) and photos.alike(fingerprint, kept_print)
        ),
        None,
    )


def _keep_for_its_copies(
    kept_for: dict[str, ProductPhoto], kept: ProductPhoto, group: Sequence[str]
) -> None:
    """Note `kept` as the photo kept for every link of its copy `group`."""
    for link in group:
        kept_for.setdefault(photos.same_key(link), kept)


def _kept_copy(kept_for: Mapping[str, ProductPhoto], group: Sequence[str]) -> ProductPhoto | None:
    """The photo already kept for any link of a copy `group`, if one is."""
    return next((kept_for[key] for key in map(photos.same_key, group) if key in kept_for), None)


# How far, in seconds, a look frame's second may be from the pulled frame it names.
MOST_SECONDS_OFF = 0.5


def save_video_pictures(
    job: Job,
    video: ProductVideo,
    kept: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
) -> list[ProductPhoto]:
    """Keep each look frame the watcher `kept` from the video ({second, shows}) as a video
    picture: the pulled frame at that second, of the video's pulled `frames` ({key, second}),
    as a product photo after every photo the job has, noted with what it `shows`. A frame
    with no pulled frame near its second, or that is a copy of a photo from the page or of a
    picture already kept, is left out. Each is given its Face note by a call of its
    own, as a page photo the picker never saw is."""
    seen = [
        photos.fingerprint_of(file_store.read(photo.file))
        for photo in job.photos.exclude(source_url="")
    ]
    position = max(job.photos.values_list("position", flat=True), default=0)
    saved: list[ProductPhoto] = []
    for moment in kept:
        second = float(moment["second"])
        frame = min(frames, key=lambda frame: abs(float(frame["second"]) - second), default=None)
        if frame is None or abs(float(frame["second"]) - second) > MOST_SECONDS_OFF:
            continue
        content = file_store.read(str(frame["key"]))
        fingerprint = photos.fingerprint_of(content)
        if fingerprint is not None and any(
            other is not None and photos.alike(fingerprint, other) for other in seen
        ):
            continue
        seen.append(fingerprint)
        position += 1
        saved.append(
            keep_photo(
                job,
                position,
                content,
                "image/jpeg",
                source_url=video.source_url,
                has_face=None,
                from_video=video,
                at_second=float(frame["second"]),
                note=str(moment["shows"]),
            )
        )
    return saved


def _photo_at(url: str, as_given: str | None) -> page.Download:
    """The photo at `url`, else at `as_given`; the error from `url` if neither can be had."""
    try:
        return page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
    except page.PageUnreadable, OutsideServiceDown:
        if not as_given:
            raise
        try:
            return page.download(as_given, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
        except page.PageUnreadable, OutsideServiceDown:
            pass
        raise


def _merge_face(kept: ProductPhoto, url: str, faces: frozenset[str] | None) -> None:
    """A copy of a kept photo was marked with a face, so the kept one has one."""
    if faces is not None and url in faces and not kept.has_face:
        kept.has_face = True
        kept.save(update_fields=["has_face"])


def keep_photo(
    job: Job,
    position: int,
    content: bytes,
    content_type: str,
    *,
    source_url: str = "",
    has_face: bool | None = False,
    from_video: ProductVideo | None = None,
    at_second: float | None = None,
    note: str = "",
    picked_as: PictureLabel | None = None,
    copy_links: Sequence[str] = (),
    seen_small: bool = False,
) -> ProductPhoto:
    """Store a product photo with the job, with its Face note, or with None, noted by a call
    of its own. An uploaded photo has no source link. A video picture is kept with the video
    it was cut `from_video`, its second there and its one-line `note`. A page photo the
    picker kept has the fields it was `picked_as` and the links of its copy group, and
    whether the picker saw it only small (`seen_small`), so it is labelled again from its
    file."""
    extension = mimetypes.guess_extension(content_type) or ""
    key = file_store.save(f"jobs/{job.pk}/photos/{position}{extension}", content)
    size = photos.size_of(content)
    if source_url and from_video is None and size is not None and max(size) < photos.TINY_SIDE:
        notices.post_notice(
            job,
            notices.TINY_PAGE_PHOTO,
            f"Page photo {position} is only {size[0]}x{size[1]} pixels after asking for its "
            f"full size: {source_url}",
            Message.Level.INFO,
        )
    if has_face is None:
        # A video's pictures share its link, so each is named by its second too.
        source = source_url if from_video is None else f"{source_url} at {at_second:g} s"
        has_face = note_face(job, source, key)
    return ProductPhoto.objects.create(
        job=job,
        position=position,
        source_url=source_url,
        file=key,
        has_face=has_face,
        from_video=from_video,
        at_second=at_second,
        note=note,
        kind=picked_as.kind if picked_as else "",
        side=picked_as.side if picked_as else "",
        alone=picked_as.alone if picked_as else "",
        colour=picked_as.colour if picked_as else "",
        target=picked_as.target if picked_as else "",
        label=picked_as.label if picked_as else "",
        copy_links=list(copy_links),
        seen_small=seen_small,
    )


def note_face(job: Job, source: str, key: str) -> bool:
    """Whether the photo in the file store at `key` shows a stranger's face, by a call of its
    own: for a photo the picker never saw. `source` names it by where it came from, so a page
    read again pays for nothing twice though its photos are stored again under new keys. A
    failed call counts as a face, the safe side: such a photo is only used when no other shows
    what's needed. That fallback is recorded in red for the developer, with its warning line."""
    try:
        noted = call_model(
            job=job,
            purpose="note_face",
            instructions=photos.NOTE_FACE_INSTRUCTIONS,
            handoff=photos.FaceNoteHandoff(photo=source),
            output=photos.FaceNote,
            images=[Image("Photo", key)],
            pay_once=True,
            images_may_move=True,
        )
    except (UnusableReply, OutsideServiceDown, UnreadableImage, BlockedBySafetyFilter) as error:
        logger.warning("Job %s: photo %s's face note failed (%s)", job.pk, source, error)
        post_notice(job, notices.FACE_NOT_NOTED, FACE_NOT_NOTED, Message.Level.PROBLEM)
        return True
    return noted.has_face


# What is recorded for the developer when a photo's face note failed, so it counts as showing a
# face: the shop owner can't act on it (spec #272).
FACE_NOT_NOTED = (
    "Checking whether one of the product's photos shows a stranger's face didn't work this "
    "time, so it counts as showing one: it is used only when no other photo shows what the ad "
    "needs."
)


def plan(job: Job) -> ProducerDecision:
    """Have the planner plan the ad from the video moments, the library's sheets, the page and
    the conversation, and keep the plan. A decision to ask the user keeps nothing."""
    from agents.planning_questions import check_plan_defaults, question_resolved

    sheets = library_sheets.sheets_of(job, how_to=True)
    library_sheets.tell_not_shown(job, sheets)
    library, moments = _library_numbers(job), moments_by_video(job)

    def ask_planner(sent_back: str) -> tuple[ProducerDecision, list[Image]]:
        # `sent_back`: what the plan quality check said of the last plan, after the
        # instructions, as a broken rule's words are.
        return library_sheets.past_the_filter(
            job,
            sheets,
            lambda images: call_model(
                job=job,
                purpose="plan_ad",
                instructions=presenter_words(job.person_gender).render(PLAN_INSTRUCTIONS)
                + (f"\n\n{sent_back}" if sent_back else ""),
                handoff=plan_handoff(job, job.page_text, sheets),
                output=presenter_words(job.person_gender).form(
                    producer_decision_for(
                        library=library,
                        moments=moments,
                        colour=colours.answer(job, none=False),
                        target_seconds=job.target_seconds,
                        photo_worry=job.photo_worry,
                        questions_resolved=question_resolved(job),
                        plan_check=lambda decision: check_plan_defaults(job, decision),
                    )
                ),
                images=images,
            ),
        )

    decision, left_out = ask_planner("")
    if decision.plan is not None:
        decision, left_out = plan_quality.quality_checked(
            job,
            decision,
            left_out,
            plan_again=ask_planner,
            video_transcripts=_video_transcripts(job),
            conversation=_conversation(job),
        )
    if left_out:
        post_left_out_by_the_filter(job, library_sheets.named(left_out), "the ad was planned")
    planned = decision.plan
    if planned is None:
        return decision
    photos_of = _photos_of_pictures(job)
    with transaction.atomic():
        Scene.objects.bulk_create(
            Scene(
                job=job,
                number=number,
                line=scene.line,
                kind=scene.kind or Scene.Kind.TALKING,
                kind_why=scene.kind_why,
                shows=scene.shows or "",
                overlay=" ".join((scene.overlay or "").split()),
                part=scene.part or "",
                second_state=scene.second_state,
                **{**scene.broll_details(), "needs": photos_of.needs(scene.needs)},
                **scene.quality_fields(),
                owner_part=scene.owner_part.model_dump() if scene.owner_part else None,
                scene_pictures=[
                    picture.model_dump(by_alias=True) for picture in scene.scene_pictures
                ],
                piece=scene.piece.model_dump() if scene.piece else None,
            )
            for number, scene in enumerate(planned.scenes, start=1)
        )
        job.product_name = planned.product_name
        job.product_colour = colours.entry(planned.product_colour)
        job.product_size = planned.product_size
        # A picture left out past the safety filter was never seen, so never marked.
        unseen = sheets.numbers_on(left_out)
        job.product_photos = [
            number for number in dict.fromkeys(planned.product_photos) if number not in unseen
        ]
        job.real_size = " ".join(planned.real_size.split())
        job.real_size_estimated = planned.real_size_estimated
        job.product_behaviour = planned.product_behaviour
        job.person_gender = planned.person_gender
        job.person_looks = planned.person_looks
        job.person_voice = planned.person_voice
        job.script_format = planned.script_format or ""
        job.brand = planned.brand.strip()
        job.end_card_line = " ".join(planned.end_card_line.split())
        job.end_card_price = planned.end_card_price.strip()
        job.status = Job.Status.PLANNED
        job.save(
            update_fields=[
                "product_name",
                "product_colour",
                "product_size",
                "product_photos",
                "real_size",
                "real_size_estimated",
                "product_behaviour",
                "person_gender",
                "person_looks",
                "person_voice",
                "script_format",
                "brand",
                "end_card_line",
                "end_card_price",
                "status",
            ]
        )
        job.photos.filter(
            position__in=photos_of.photos(
                [number for number in planned.colour_photos if number not in unseen], "colour"
            )
        ).update(shows_product_colour=True)
    photos_of.tell(job)
    _tell_rules_not_kept(job, decision)
    return decision


def _tell_rules_not_kept(job: Job, decision: ProducerDecision) -> None:
    """Record every told rule the kept plan breaks, which no check sends back: the plan goes
    ahead as it is (Julian, Nov 9 2026, rules review)."""
    from agents.planning_questions import price_while_unanswered

    assert decision.plan is not None
    broken = [
        rules_not_kept(decision.plan, job.page_text),
        price_while_unanswered(job, decision),
    ]
    said = " ".join(each for each in broken if each is not None)
    if said:
        post_notice(
            job,
            notices.PLAN_RULE_NOT_KEPT,
            f"The plan was kept though it breaks a rule the planner is told: {said}",
            Message.Level.PROBLEM,
        )


def _library_numbers(job: Job) -> set[int]:
    """The numbers of the pictures in `job`'s library, which a plan's scenes name."""
    return set(job.library.values_list("number", flat=True))


def moments_by_video(job: Job) -> dict[int, int]:
    """How many moments each of `job`'s watched page videos has, by the video's number."""
    return {
        video.position: len(watched.moments)
        for video in job.videos.filter(watched__isnull=False)
        if (watched := watch_of(video)) is not None
    }


def plan_handoff(job: Job, page_text: str, sheets: library_sheets.Sheets) -> PlanHandoff:
    """What the planner is handed about the ad beside the library's `sheets`, with the
    product's own text from its page: the video moments first."""
    from agents.planning_questions import planning_defaults

    return PlanHandoff(
        moments=library_sheets.moments_text(job),
        library_pictures=sheets.library_pictures,
        pictures_not_shown=sheets.not_shown,
        video_transcripts=_video_transcripts(job),
        page_text=page.for_model(page_text),
        product_url=job.product_url,
        target_seconds=job.target_seconds,
        conversation=_conversation(job),
        photo_worry=f"The photos are in doubt: {job.photo_worry}" if job.photo_worry else "",
        planning_defaults=planning_defaults(job),
    )


@dataclass
class _PhotosOfPictures:
    """The product photo, by position, of each library picture a plan or a rewrite names
    for its needs and colour photos, and the pictures named that have none. A recoloured
    picture is never in the ad's colour as its photo: that photo is another colour."""

    positions: dict[int, int]
    recoloured: set[int]
    without_a_photo: dict[str, list[int]] = field(
        default_factory=lambda: {"need": [], "colour": []}
    )

    def photos(self, numbers: Sequence[int], use: Literal["need", "colour"]) -> list[int]:
        """The positions of the photos library pictures `numbers` are, in order, each once:
        a picture with no product photo for `use` is left out and noted."""
        kept: list[int] = []
        for number in numbers:
            position = self.positions.get(number)
            if use == "colour" and number in self.recoloured:
                position = None
            if position is None:
                if number not in self.without_a_photo[use]:
                    self.without_a_photo[use].append(number)
            elif position not in kept:
                kept.append(position)
        return kept

    def colour_numbers(self, marked: Collection[int]) -> list[int]:
        """The library numbers of the pictures whose photo is marked in the ad's colour."""
        return [
            number
            for number, at in self.positions.items()
            if at in marked and number not in self.recoloured
        ]

    def needs(self, needs: Sequence[Need]) -> list[dict[str, Any]]:
        """`needs` as a Scene stores them, by photo position: a need whose pictures have no
        product photo is left out."""
        stored = [
            {**need.model_dump(), "photos": self.photos(need.photos, "need")} for need in needs
        ]
        return [need for need in stored if need["photos"]]

    def tell(self, job: Job) -> None:
        """Tell the chat in red, with its warning line, of the pictures named that have no
        product photo for what they were named for, once for the same pictures."""
        for use, numbers in self.without_a_photo.items():
            if not numbers:
                continue
            one = len(numbers) == 1
            named = (
                f"Library picture{'' if one else 's'} "
                f"{', '.join(str(number) for number in sorted(numbers))} "
                f"{'has' if one else 'have'}"
            )
            text = (
                f"{named} no product photo a starting picture can be made from, so "
                f"{'it is' if one else 'they are'} left out of the photos the scenes need."
                if use == "need"
                else f"{named} no product photo of {'its' if one else 'their'} own, so "
                f"{'it isn' if one else 'they aren'}'t marked as showing the ad's colour."
            )
            if all(warning["text"] != text for warning in job.warnings):
                post_notice(job, notices.NO_PHOTO_FOR_A_PICTURE, text, Message.Level.PROBLEM)


def _photos_of_pictures(job: Job) -> _PhotosOfPictures:
    return _PhotosOfPictures(
        library_sheets.photo_positions(job), library_sheets.recoloured_numbers(job)
    )


def _labelled(photo: ProductPhoto, picked_by: list[int]) -> Image:
    """A photo as the fact check is shown it: by its number, and a frame cut from a page
    video also by that video, the second it was cut at, the scenes in `picked_by` that picked
    it and what it shows."""
    label = f"Photo {photo.position}"
    if photo.from_video is not None:
        picked = (
            f", picked by scene{'s' if len(picked_by) > 1 else ''} "
            + ", ".join(str(scene) for scene in picked_by)
            if picked_by
            else ""
        )
        label += (
            f" (a frame of the product's video {photo.from_video.position} at "
            f"{photo.at_second:.1f} s{picked}: {photo.note})"
        )
    return Image(label=label, key=photo.file)


def _video_transcripts(job: Job) -> list[str]:
    """What each of the job's product videos says, in their order: one per video whose
    words were had, "music only" when it says nothing."""
    return list(job.videos.exclude(transcript="").values_list("transcript", flat=True))


def _photos_to_pick_from(job: Job, room: int) -> list[ProductPhoto]:
    """The product photos a starting picture's writer picks its photo from, at most `room`,
    in order: every one when they fit, else those in the ad's colour first, then the page's,
    those cut from its videos and the shop owner's. A job keeps up to page.MAX_PHOTOS page
    photos, more with its video pictures, and a model takes at most clip_judge.MOST_PICTURES
    pictures in one ask."""
    photos = list(job.photos.order_by("position"))
    kept = sorted(
        photos,
        key=lambda photo: (
            not photo.shows_product_colour,
            1 if photo.from_video_id is not None else 0 if photo.source_url else 2,
            photo.position,
        ),
    )[:room]
    return [photo for photo in photos if photo in kept]


PORTRAIT_PROMPT = """\
A photorealistic vertical portrait of the person who presents a video ad, looking \
straight at the camera with a friendly expression, head and shoulders in frame, lit \
naturally. Not a real, famous person. No text, logos or products in the picture. The \
person: {who}"""


def _who_the_person_is(job: Job) -> str:
    """The person as the portrait's prompt describes them: the plan's gender first, then
    its looks, so the picture model never picks a gender at random. A job planned before
    the plan said the gender has only the looks."""
    if not job.person_gender:
        return job.person_looks
    return f"a {job.person_gender}. {job.person_looks}"


# How a voice description already says the speaker's gender, by the plan's gender: it
# opens by naming the speaker ("A warm, relaxed woman ...", "A calm male voice ..."), or
# names a gendered voice anywhere ("... a woman's voice"). A gender word used for
# something else ("like a men's grooming coach", "not too masculine") doesn't count.
_SAYS_THE_GENDER = {
    gender: re.compile(
        rf"^\W*an?\s+(?:[\w,'-]+\s+){{0,5}}?(?:{words})\b"
        rf"|\b(?:{words})(?:'s)?\s+(?:voice|speaker|narrator|presenter)\b",
        re.IGNORECASE,
    )
    for gender, words in (
        ("woman", "woman|female|feminine"),
        ("man", "man|male|masculine"),
    )
}


def voice_words(job: Job) -> str:
    """The voice as H3 is told it, described once: the plan's description, with the plan's
    gender put in front when the description doesn't already say it, so an off-camera
    clip never leaves H3 to pick a gender at random. A job planned before the plan said
    the gender has only the description."""
    voice = job.person_voice.strip()
    says_it = _SAYS_THE_GENDER.get(job.person_gender)
    if says_it is None or says_it.search(voice):
        return voice
    return f"a {job.person_gender}'s voice. {voice}"


def _older_voice_words(job: Job) -> str:
    """The voice words older clips kept in their sent list, so a clip filmed before the
    voice words said the gender once doesn't turn stale with no voice change."""
    if not job.person_gender:
        return job.person_voice
    return f"A {job.person_gender}'s voice. {job.person_voice}"


def create_person(job: Job) -> ProducedItem:
    """Make the presenter's portrait; H3 uses the plan's voice words, with no sample.

    Only what the job doesn't have yet is made, so run again it pays for nothing new, and
    what was paid for before a worker stopped is kept rather than paid for again."""
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if portrait is None:
        paid_for = _paid_for_before(job, "draw_person")
        portrait = ProducedItem.objects.create(
            job=job,
            kind=ProducedItem.Kind.PORTRAIT,
            file=(
                paid_for["file"]
                if paid_for
                else draw_picture(
                    job=job,
                    purpose="draw_person",
                    prompt=PORTRAIT_PROMPT.format(who=_who_the_person_is(job)),
                )
            ),
        )
    return portrait


# How the producer's own instruction is put on top of a standard prompt for the person
# (spec #247 point 6: a tool with an AI inside takes the producer's detailed instruction).
# Not approved yet: the wording.
ON_TOP = "{standard}\nOn top of that, which wins where the two differ: {instruction}"


def _on_top(standard: str, instruction: str) -> str:
    """`standard` with the producer's `instruction` on top, or alone when there is none."""
    instruction = instruction.strip()
    return ON_TOP.format(standard=standard, instruction=instruction) if instruction else standard


def remake_person(job: Job, *, look: bool, instruction: str, call: ToolCall) -> ProducedItem | None:
    """Draw a new portrait when requested, from the updated plan and producer instruction.
    H3's voice words are saved by the producer after cost consent; no voice file is made.
    Return the portrait, or None when only voice words changed; no voice file is made.

    Run again, as after a worker stopped, it pays for nothing `call` already paid for."""
    portrait = None
    if look:
        paid_for = _paid_for_before(job, "draw_person", charged_to=call)
        file = (
            paid_for["file"]
            if paid_for
            else draw_picture(
                job=job,
                purpose="draw_person",
                prompt=_on_top(PORTRAIT_PROMPT.format(who=_who_the_person_is(job)), instruction),
            )
        )
        portrait = _job_version(job, ProducedItem.Kind.PORTRAIT, file=file)
    return portrait


def _job_version(job: Job, kind: ProducedItem.Kind, **made: Any) -> ProducedItem:
    """The job's next version of `kind`, as `made`; the newest when it is already that, as
    when it was kept before the worker stopped."""
    newest = latest(job, kind)
    if newest is not None and all(getattr(newest, key) == value for key, value in made.items()):
        return newest
    return ProducedItem.objects.create(
        job=job, kind=kind, version=(newest.version + 1) if newest else 1, **made
    )


def _paid_for_before(
    job: Job, purpose: str, *, charged_to: ToolCall | None = None, **asked: Any
) -> dict[str, Any] | None:
    """What a call for `purpose` made before the worker stopped, if it was paid for but not
    kept: for the job, or for the tool call it was `charged_to`, and only one `asked` as
    given, as a voice's measuring by its voice. Every call is recorded as soon as it
    succeeds, so a restart reuses what it made."""
    call = _paid_calls(job, purpose, charged_to=charged_to).filter(**asked).last()
    return call.output if call else None


def _paid_calls(
    job: Job, purpose: str, *, charged_to: ToolCall | None = None
) -> QuerySet[ModelCall]:
    """Every call for `purpose` that succeeded, oldest first: for the job, or for the tool
    call it was `charged_to`."""
    calls = job.model_calls.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED)
    if charged_to is not None:
        calls = calls.filter(tool_call=charged_to)
    return calls


@dataclass(frozen=True)
class PlanningProblem:
    """A planning check the producer must fix with its scene tools before rendering."""

    about: Literal["line", "line_length"]
    detail: str
    reason: str
    # The scene the producer must fix.
    scene: Scene | None = None


def run_checks(job: Job) -> PlanningProblem | None:
    """Check the script before anything is rendered: every line, and what each scene shows,
    against the page; then each line against how long its clip can last, and the whole
    script against the target length. Fix what the checks can and hand any remaining
    scene problem to the producer. None once every check has passed.

    Run again after a restart or a scene edit: lines already checked stay checked."""
    while True:
        if job.scenes.filter(fact_checked=False).exists():
            problem = _fact_check(job)
        else:
            fit = _fit_length(job)
            if fit is True:
                job.status = Job.Status.READY_TO_RENDER
                job.save(update_fields=["status"])
                return None
            problem = fit if isinstance(fit, PlanningProblem) else None
        if problem is not None:
            return problem


def _fact_check(job: Job) -> PlanningProblem | None:
    unchecked = list(job.scenes.filter(fact_checked=False))
    conversation = _conversation(job)
    # A line whose failure was stored before a restart is fixed from that failure, rather
    # than checked, and paid for, again.
    scenes = [scene for scene in unchecked if not _needs_fixing(scene)]
    if scenes:
        _checked(job, scenes, conversation)
    for scene in unchecked:
        if not _needs_fixing(scene):
            continue
        if len(scene.fact_problems) > MOST_REWRITES:
            return _about_line(scene)
        _rewrite_scene(job, scene, conversation)
    return None


def _needs_fixing(scene: Scene) -> bool:
    """Whether the scene's line failed its last fact check and hasn't been rewritten since."""
    return bool(scene.fact_problems) and not scene.fact_problems[-1]["rewritten"]


def _checked(job: Job, scenes: list[Scene], conversation: list[ChatMessage]) -> None:
    """Fact-check the scenes' lines and what they show, storing why each failed."""
    check = _ask_fact_check(job, [_to_check(scene) for scene in scenes], conversation)
    verdicts = {verdict.scene: verdict for verdict in check.lines}
    for scene in scenes:
        verdict = verdicts[scene.number]
        if verdict.verdict == "ok":
            scene.fact_checked = True
            scene.save(update_fields=["fact_checked"])
            continue
        assert verdict.wrong is not None
        assert verdict.problem is not None and verdict.page_says is not None
        scene.fact_problems.append(
            {
                "wrong": verdict.wrong,
                "problem": verdict.problem,
                "page_says": verdict.page_says,
                "rewritten": False,
            }
        )
        scene.save(update_fields=["fact_problems"])
    return None


def _ask_fact_check(
    job: Job, lines: list[LineToCheck], conversation: list[ChatMessage], *, pay_once: bool = False
) -> FactCheck:
    """Have `lines`, and what their scenes show, checked against the page and what the user
    said. With `pay_once`, a check already made of these same lines is answered from its
    record."""
    showing = [line.scene for line in lines if line.shows]
    # What a scene shows may be supported by how the product looks in its photos: those in
    # the ad's colour, and those the scenes picked from the library (spec #210 point 11),
    # among them the frames cut from the page videos, which show how the shop uses it. A
    # script where the person talks throughout is checked on text alone.
    positions = library_sheets.photo_positions(job) if showing else {}
    picked_by: dict[int, list[int]] = {}
    for scene in job.scenes.filter(number__in=showing).order_by("number"):
        for picture in scene.scene_pictures:
            position = positions.get(picture["number"])
            if position is not None and scene.number not in picked_by.get(position, []):
                picked_by.setdefault(position, []).append(scene.number)
    # A plan may mark no photo in the ad's colour: then every photo that fits is shown, with
    # the scenes' own picks always among them.
    marked = job.photos.filter(shows_product_colour=True).exists()
    photos: list[ProductPhoto] = []
    if showing:
        if marked:
            shown = Q(shows_product_colour=True)
        else:
            fits = _photos_to_pick_from(job, clip_judge.MOST_PICTURES - len(picked_by))
            shown = Q(position__in=[photo.position for photo in fits])
        photos = list(
            job.photos.filter(shown | Q(position__in=picked_by))
            .select_related("from_video")
            .order_by("position")
        )
    return call_model(
        job=job,
        purpose="fact_check",
        instructions=presenter_words(job.person_gender).render(FACT_CHECK_INSTRUCTIONS),
        handoff=FactCheckHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            lines=lines,
            video_transcripts=_video_transcripts(job),
            video_moments=library_sheets.moments_text(job),
        ),
        output=fact_check_for([line.scene for line in lines], showing=showing),
        images=[_labelled(photo, picked_by.get(photo.position, [])) for photo in photos],
        pay_once=pay_once,
    )


def _to_check(scene: Scene) -> LineToCheck:
    return LineToCheck(
        scene=scene.number,
        line=scene.line,
        shows=scene.shows or None,
        usage=scene.usage or None,
        result=scene.result or None,
    )


def brand_to_keep(job: Job, scene: Scene) -> str:
    """The brand a rewrite of `scene`'s line must keep saying: the ad's brand when `scene` is
    its last and its line says it now, else blank (planning.brand_dropped)."""
    if owner_changed_scene(scene) and job.produced.filter(kind="finished_ad").exists():
        return ""
    if job.scenes.filter(number__gt=scene.number).exists():
        return ""
    return _kept_brand(job, scene.line)


def _kept_brand(job: Job, last_line: str) -> str:
    return job.brand if brand_missing(last_line, job.brand) is None else ""


# What a fix-up may change, by what the fact check last found wrong (Julian, Nov 8 2026,
# thread "Stop rule drift after planning", part 1): code keeps the rest as it was.
_KEEPS_ITS_JOB = (
    "A changed line keeps its job in the ad: its ask, price and brand, and about its length. "
)
_ONLY_WHAT_FAILED: dict[Wrong, str] = {
    "line": (
        "Only its line failed: change only the words of the line the problem names, and give "
        "back its action and details exactly as they are. " + _KEEPS_ITS_JOB
    ),
    "shows": (
        "Only its action failed: give back its line word for word, and change only what the "
        "problem names in the action and its details. "
    ),
    "both": "Its line and its action both failed: change only what the problems name. "
    + _KEEPS_ITS_JOB,
}


def _what_was_wrong(problem: dict[str, Any]) -> Wrong:
    """What a stored fact-check failure found wrong. Failures stored before scenes could show
    something were of the line."""
    wrong: Wrong = problem.get("wrong", "line")
    return wrong


def _rewrite_scene(job: Job, scene: Scene, conversation: list[ChatMessage]) -> None:
    """Have the scene rewritten, changing only what the fact check last found wrong: its
    line, what it shows and its B-roll details, or both. A scene that shows something is
    rewritten seeing the library's sheets, as the planner does, to say what it needs."""
    old_line = scene.line
    remember_line(scene)
    owner_words = words_to_keep(scene)
    wrong = _what_was_wrong(scene.fact_problems[-1])
    # A scene where the person talks is rewritten on text alone: no sheet is drawn for it.
    sheets = library_sheets.sheets_of(job, drawn=bool(scene.shows), how_to=True)
    photos_of = _photos_of_pictures(job)
    marked = set(job.photos.filter(shows_product_colour=True).values_list("position", flat=True))
    colour_photos = photos_of.colour_numbers(marked)
    if scene.shows:
        library_sheets.tell_not_shown(job, sheets)

    def ask(images: list[Image]) -> RewrittenScene:
        return call_model(
            job=job,
            purpose="rewrite_line",
            instructions=presenter_words(job.person_gender).render(
                "Rewrite the failed scene so every claim in its line and action is supported by "
                "the page, the shop owner's words, or for looks only the product photos. Keep "
                "its place in the whole script and its kind. When its line says what kind of "
                "thing the product is, or tells the page's story as the product's story, the "
                "new line still does. Both kinds have an action: never "
                "give null shows. A talking scene speaks while showing, using or showing a "
                "result with the product. "
                + NO_FACE_RULE
                + " Keep its kind's why and all its B-roll details. Give back its line and action "
                "and their details. Never invent a price, number, name or claim. "
                + _ONLY_WHAT_FAILED[wrong]
                + SPOKEN_LINE_RULE
                + NOT_A_CLAIM
                + " "
                + BROLL_ONE_ACTION_INSTRUCTIONS
                + "\n"
                + HOW_TO_METHOD
                + "\n"
                + scene_rules_of(
                    job, [scene], leave_out=(BROLL_ONE_ACTION_INSTRUCTIONS.strip(), HOW_TO_METHOD)
                )
            ),
            handoff=RewriteHandoff(
                owner_words=owner_words,
                page_text=page.for_model(job.page_text),
                conversation=conversation,
                product_colour=job.product_colour,
                library_pictures=sheets.library_pictures,
                pictures_not_shown=sheets.not_shown,
                colour_photos=colour_photos,
                script=[_to_check(each) for each in job.scenes.all()],
                scene=scene.number,
                problems=[
                    Problem(
                        wrong=_what_was_wrong(problem),
                        problem=problem["problem"],
                        page_says=problem["page_says"],
                    )
                    for problem in scene.fact_problems
                ],
            ),
            output=presenter_words(job.person_gender).form(
                rewritten_scene_for(
                    scene.number,
                    shows_something=bool(scene.shows),
                    library=_library_numbers(job),
                    kind="talking" if scene.kind == Scene.Kind.TALKING else "broll",
                    kind_why=scene.kind_why,
                    brand=brand_to_keep(job, scene),
                )
            ),
            images=images,
        )

    if scene.shows:
        # As the planner does: only the pictures the safety filter refuses are left out.
        rewrite, left_out = library_sheets.past_the_filter(job, sheets, ask)
        if left_out:
            post_left_out_by_the_filter(
                job, library_sheets.named(left_out), "the line was rewritten"
            )
    else:
        rewrite = ask([])
    scene.fact_problems[-1]["rewritten"] = True
    if wrong == "line":
        # Only the line failed: the action and its B-roll and quality fields stay as planned.
        scene.change_line(rewrite.line)
    else:
        # Only the action failed: the line stays word for word, whatever the rewrite says.
        scene.change_line(
            scene.line if wrong == "shows" else rewrite.line,
            rewrite.shows or "",
            broll={**rewrite.broll_details(), "needs": photos_of.needs(rewrite.needs)},
        )
        # A field the rewrite leaves empty keeps the scene's; whether it only shows the
        # product to the lens follows the rewritten action.
        for name, value in rewrite.quality_fields().items():
            if value not in ("", None) or name == "shows_to_lens":
                setattr(scene, name, value)
    tell_line_change(
        scene,
        old_line,
        scene.line,
        "the fact check found a problem: " + scene.fact_problems[-1]["problem"],
        owner_words,
    )
    photos_of.tell(job)
    with transaction.atomic():
        if rewrite.shows is None:
            # A talking scene shows no second state, and the scene after it no longer follows
            # the first state it was made from.
            scene.second_state = False
            job.scenes.filter(number=scene.number + 1).update(second_state=False)
        scene.save(
            update_fields=[
                "line",
                "shortened_from",
                "owner_words",
                "shows",
                *BROLL_FIELDS,
                *PLANNED_FOR_THE_ACTION,
                *QUALITY_FIELDS,
                "status",
                "fact_problems",
                "second_state",
            ]
        )


def _about_line(scene: Scene) -> PlanningProblem:
    last = scene.fact_problems[-1]
    return PlanningProblem(
        about="line",
        detail=(
            f"Scene {scene.number}'s line still fails the fact check after "
            f'{MOST_REWRITES} rewrites: "{scene.line}"{_while_its_said(scene)} '
            f"{last['problem']} The page says: {last['page_says']}"
        ),
        reason=(
            f"The line was rewritten {MOST_REWRITES} times and still failed the fact check, "
            "so the producer must replace or remove the unsupported claim."
        ),
        scene=scene,
    )


def _while_its_said(scene: Scene) -> str:
    """What a scene shows while its line is said, as the user is told it, or nothing when
    the person says it to camera. The user is never told the kinds of scene apart."""
    return f" While it's said, the ad shows: {scene.shows.rstrip('.')}." if scene.shows else ""


def _length_script(job: Job) -> dict[str, Any]:
    """The inputs to target fitting, kept with a longer ad's acceptance."""
    return {
        "target_seconds": job.target_seconds,
        "lines": list(job.scenes.values_list("line", flat=True)),
    }


def _kept_longer(job: Job) -> bool:
    """An unchanged automatic acceptance, or a historical explicit owner choice."""
    return job.length_choice == Job.LengthChoice.KEEP_LONGER and (
        not job.length_kept_script or job.length_kept_script == _length_script(job)
    )


def _fit_length(job: Job) -> bool | PlanningProblem:
    """Whether the script can go on to be rendered at its length: every line fitting its
    clip, and the whole script within the default clip cap or the owner's target.
    Rewrite where possible and hand remaining line problems to the producer. An ad still
    over its target after shortening continues longer, with an owner notice."""
    words_per_second = FIXED_WORDS_PER_SECOND
    to_fit = _a_line_to_fit(job, words_per_second)
    if to_fit is not None:
        scene, fit = to_fit
        if fit == "too_short":
            raise LineTooShort(
                f"Scene {scene.number}'s line is still shorter than "
                f"{SHORTEST_BROLL_LINE_SECONDS} seconds after {MOST_LENGTHENINGS} lengthenings. "
                "Revise the line to fill its clip, then run the planning checks again."
            )
        if fit == "hand_back":
            return _line_too_long(scene, words_per_second)
        if fit == "lengthen":
            _lengthen_line(job, scene, words_per_second)
        elif fit == "shorten_broll":
            _shorten_line(job, scene, most_words_in_a_broll_line(words_per_second))
        return False
    target = job.target_seconds
    if target is None:
        default_fit = default_length_fit(list(job.scenes.values_list("line", flat=True)))
        if default_fit.fits:
            return True
        post_notice(
            job,
            notices.DEFAULT_AD_SHORTENED,
            f"The whole clips and end card add up to {default_fit.seconds} seconds, "
            "over the default 35 seconds; "
            "shortening the lines toward 30 seconds before any clip is made.",
            Message.Level.INFO,
        )
        _shorten(job)
        return False
    if _kept_longer(job):
        return True
    target_fit = target_length_fit(list(job.scenes.values_list("line", flat=True)), target)
    if target_fit.fits:
        return True
    if _shortened_since_the_user_spoke(job) < MOST_REWRITES:
        _shorten(job)
        return False
    text = (
        f"Your ad is about {target_fit.seconds} seconds, longer than your {target}-second "
        "target after shortening; I've kept it at this length."
    )
    with transaction.atomic():
        # Keep this decision and its notice together across notes and restarts. This field
        # also preserves ads whose owner chose the outcome before checks stopped asking.
        job.length_choice = Job.LengthChoice.KEEP_LONGER
        job.length_kept_script = _length_script(job)
        job.save(update_fields=["length_choice", "length_kept_script"])
        post_notice(job, notices.TARGET_LENGTH_KEPT, text, Message.Level.INFO)
    return True


# Every kind of scene must fit the same clip limits; exhausted repairs return to the producer.
class LineTooShort(Exception):
    """The line still cannot fill a clip after its allowed lengthenings."""


Fit = Literal["lengthen", "too_short", "shorten_broll", "hand_back"]


def _a_line_to_fit(job: Job, words_per_second: float) -> tuple[Scene, Fit] | None:
    """The first scene whose line doesn't fit its clip, and what it needs."""
    for scene in job.scenes.all():
        fit = _fit_for(job, scene, words_per_second)
        if fit is not None:
            return scene, fit
    return None


def _fit_for(job: Job, scene: Scene, words_per_second: float) -> Fit | None:
    """Lengthen short lines, shorten long lines, then hand remaining problems to the producer."""
    seconds = line_seconds(scene.line, words_per_second)
    if seconds > LONGEST_BROLL_LINE_SECONDS:
        shortened = job.model_calls.filter(
            purpose="shorten_line",
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__scene=scene.number,
        ).count()
        return "shorten_broll" if shortened < MOST_BROLL_SHORTENINGS else "hand_back"
    if seconds < SHORTEST_BROLL_LINE_SECONDS and not keeps_exact_line(scene):
        lengthened = job.model_calls.filter(
            purpose="lengthen_line",
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__scene=scene.number,
        ).count()
        return "lengthen" if lengthened < MOST_LENGTHENINGS else "too_short"
    return None


def _line_too_long(scene: Scene, words_per_second: float) -> PlanningProblem:
    """Hand a line back to the producer after the allowed shortenings cannot fit its clip."""
    most_tries = MOST_BROLL_SHORTENINGS
    limit = LONGEST_BROLL_LINE_SECONDS
    seconds = line_seconds(scene.line, words_per_second)
    return PlanningProblem(
        about="line_length",
        detail=(
            f"Scene {scene.number}'s line still takes about {seconds:.1f} seconds to say "
            f"after {most_tries} shortenings, and a scene can last at most "
            f'{limit} seconds: "{scene.line}"'
        ),
        reason=(
            f"The line was shortened {most_tries} times and is still too long for one "
            "scene, so the producer must split the scene or rewrite its line."
        ),
        scene=scene,
    )


def _shorten_line(job: Job, scene: Scene, most_words: int) -> None:
    _new_line(scene, _shorter_line(job, scene, most_words, _conversation(job)))


def _shorter_line(
    job: Job,
    scene: Scene,
    most_words: int,
    conversation: list[ChatMessage],
    *,
    pay_once: bool = False,
) -> str:
    """Have the scene's line rewritten in at most `most_words`. With `pay_once`, a line
    already shortened from this same script is answered from its record."""
    shortened = call_model(
        job=job,
        purpose="shorten_line",
        instructions=presenter_words(job.person_gender).render(SHORTEN_LINE_INSTRUCTIONS)
        + "\n"
        + scene_rules_of(job, [scene]),
        handoff=ShortenLineHandoff(
            owner_words=words_to_keep(scene),
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            script=[_to_check(each) for each in job.scenes.all()],
            scene=scene.number,
            most_words=most_words,
        ),
        output=rewritten_line_for(brand_to_keep(job, scene)),
        pay_once=pay_once,
    )
    line = " ".join(shortened.line.split())
    tell_line_change(
        scene, scene.line, line, "the line was too long for one clip.", words_to_keep(scene)
    )
    return line


def _lengthen_line(job: Job, scene: Scene, words_per_second: float) -> None:
    lengthened = call_model(
        job=job,
        purpose="lengthen_line",
        instructions=presenter_words(job.person_gender).render(LENGTHEN_LINE_INSTRUCTIONS)
        + "\n"
        + scene_rules_of(job, [scene]),
        handoff=LengthenLineHandoff(
            owner_words=words_to_keep(scene),
            page_text=page.for_model(job.page_text),
            conversation=_conversation(job),
            product_colour=job.product_colour,
            script=[_to_check(each) for each in job.scenes.all()],
            scene=scene.number,
            fewest_words=fewest_words_in_a_broll_line(words_per_second),
        ),
        output=rewritten_line_for(brand_to_keep(job, scene)),
    )
    tell_line_change(
        scene,
        scene.line,
        lengthened.line,
        "the line was too short for one clip.",
        words_to_keep(scene),
    )
    _new_line(scene, lengthened.line)


def _new_line(scene: Scene, line: str) -> None:
    """Give the scene a rewritten line, which is fact checked again."""
    remember_line(scene)
    scene.change_line(" ".join(line.split()))
    scene.fact_checked = False
    scene.fact_problems = []
    scene.save(
        update_fields=[
            "line",
            "shortened_from",
            "owner_words",
            "status",
            "fact_checked",
            "fact_problems",
        ]
    )


def _shortened_since_the_user_spoke(job: Job) -> int:
    """Times the script was shortened since the owner last said anything."""
    return _since_the_user_spoke(
        job, job.model_calls.filter(purpose="shorten_script", outcome=ModelCall.Outcome.SUCCEEDED)
    )


def _since_the_user_spoke(job: Job, calls: QuerySet[ModelCall]) -> int:
    """How many of `calls` were made since the user last said anything."""
    spoke = _last_heard_from_the_user(job)
    return (calls.filter(created_at__gt=spoke) if spoke else calls).count()


def _last_heard_from_the_user(job: Job) -> datetime | None:
    if job.session is None:
        # A job started before sessions existed has no conversation.
        return None
    spoke: datetime | None = job.session.messages.filter(role=Message.Role.USER).aggregate(
        last=Max("created_at")
    )["last"]
    return spoke


def _broll_fields(scene: Scene) -> dict[str, Any]:
    return {field: getattr(scene, field) for field in BROLL_FIELDS}


# How a script's whole clips are counted, as the shortening model is told it.
CLIP_LENGTH_RULE = (
    f"a clip lasts its words divided by {FIXED_WORDS_PER_SECOND}, plus "
    f"{SECONDS_AFTER_THE_LINE} seconds, rounded up, at least {SHORTEST_CLIP_SECONDS} seconds."
)


def _shorten(job: Job) -> None:
    """Have the script rewritten to fit its target. Each line comes back with the scene it
    comes from, and takes that scene's "shows", overlay, part and B-roll fields with it, so a
    dropped scene takes them away and a line never moves under another scene's picture. A
    second state stays one only while the first is still kept just before it."""
    script = [_to_check(scene) for scene in job.scenes.all()]
    exact_owner_lines = {
        scene.number: scene.line for scene in job.scenes.all() if keeps_exact_line(scene)
    }
    default_length = job.target_seconds is None
    target = job.target_seconds if job.target_seconds is not None else DEFAULT_AD_SECONDS
    instructions = presenter_words(job.person_gender).render(SHORTEN_INSTRUCTIONS)
    if default_length:
        instructions += (
            " Keep every scene and shorten only its line. Aim for 30 seconds in whole clips "
            f"and the {END_CARD_SECONDS}-second end card after them, never over 35 seconds; "
            f"{CLIP_LENGTH_RULE} Keep exact owner lines unchanged, even when short. "
            f"Every other line needs at least "
            f"{fewest_words_in_a_broll_line(FIXED_WORDS_PER_SECOND)} words."
        )
    else:
        instructions += (
            f" The target counts whole clips and the {END_CARD_SECONDS}-second end card after "
            f"them, and they may add up to {LENGTH_ALLOWANCE_SECONDS} seconds over it: "
            f"{CLIP_LENGTH_RULE}"
        )
    last = job.scenes.order_by("number").last()
    brand = brand_to_keep(job, last) if last is not None else ""
    if brand:
        instructions += f" The last line keeps saying the brand, {brand}."
    instructions += "\n" + scene_rules_of(job, job.scenes.all())
    shortened = call_model(
        job=job,
        purpose="shorten_script",
        instructions=instructions,
        handoff=ShortenHandoff(
            owner_words={scene.number: words_to_keep(scene) for scene in job.scenes.all()},
            page_text=page.for_model(job.page_text),
            conversation=_conversation(job),
            product_colour=job.product_colour,
            target_seconds=target,
            most_words=most_words_in_clips(
                target if default_length else target + LENGTH_ALLOWANCE_SECONDS, len(script)
            ),
            script=script,
        ),
        output=shortened_script_for(
            script,
            default_length=default_length,
            brand=brand,
            owner_lines=exact_owner_lines,
        ),
    )
    with transaction.atomic():
        scenes = list(job.scenes.all())
        was = {
            scene.number: (
                scene.shows,
                scene.overlay,
                scene.part,
                _broll_fields(scene),
                scene.kind,
                scene.kind_why,
            )
            for scene in scenes
        }
        for scene in scenes:
            remember_line(scene)
        owner_words = {scene.number: words_to_keep(scene) for scene in scenes}
        owner_lines = {scene.number: scene.line for scene in scenes}
        exact_words = {scene.number: scene.owner_words for scene in scenes}
        kept_numbers = {line.scene for line in shortened.lines}
        for scene in scenes:
            if scene.number not in kept_numbers:
                tell_line_change(
                    scene,
                    scene.line,
                    None,
                    f"the script needed to fit the {target}-second target length.",
                    owner_words[scene.number],
                )
        # A line that passed the fact check word for word, showing the same, still has;
        # anything else is new.
        checked = {(scene.line, scene.shows) for scene in scenes if scene.fact_checked}
        # Keep stored failures with unchanged lines as scenes move.
        problems = {scene.number: scene.fact_problems for scene in scenes}
        # A second state stays one only while the first still plays just before it.
        second_states = {scene.number for scene in scenes if scene.second_state}
        after_first_ad = job.produced.filter(kind="finished_ad").exists()
        source_scenes = {scene.number: scene for scene in scenes}
        previous_kept = None
        for index, kept in enumerate(shortened.lines):
            scene = source_scenes[kept.scene] if after_first_ad else scenes[index]
            shows, overlay, part, broll, kind, kind_why = was[kept.scene]
            second_state = kept.scene in second_states and previous_kept == kept.scene - 1
            previous_kept = kept.scene
            if (
                scene.line,
                scene.shows,
                scene.overlay,
                scene.part,
                scene.second_state,
                _broll_fields(scene),
                scene.kind,
                scene.kind_why,
            ) != (kept.line, shows, overlay, part, second_state, broll, kind, kind_why):
                tell_line_change(
                    scene,
                    owner_lines[kept.scene],
                    kept.line,
                    "the script was too long for the ad’s clip lengths.",
                    owner_words[kept.scene],
                )
                scene.change_line(kept.line, shows=shows)
                scene.owner_words = exact_words[kept.scene]
                scene.overlay = overlay
                scene.part = part
                scene.kind = kind
                scene.kind_why = kind_why
                scene.second_state = second_state
                for field, value in broll.items():
                    setattr(scene, field, value)
                scene.fact_checked = (kept.line, shows) in checked
                scene.fact_problems = problems[kept.scene] if scene.fact_checked else []
                scene.save(
                    update_fields=[
                        "line",
                        "shortened_from",
                        "owner_words",
                        "shows",
                        "overlay",
                        "part",
                        "kind",
                        "kind_why",
                        "second_state",
                        *BROLL_FIELDS,
                        *PLANNED_FOR_THE_ACTION,
                        "status",
                        "fact_checked",
                        "fact_problems",
                    ]
                )
        if after_first_ad:
            from . import plan_edits

            for number, scene in source_scenes.items():
                if number not in kept_numbers:
                    plan_edits.remove_scene(scene, owner_asked=True)
            kept_scenes = [source_scenes[line.scene] for line in shortened.lines]
            for scene in kept_scenes:
                scene.refresh_from_db()
            plan_edits.renumber(job, kept_scenes, owner_asked=True)
        else:
            for scene in scenes[len(shortened.lines) :]:
                scene.delete()


def why_the_checks_passed(job: Job) -> str:
    """One sentence on why the script can go on to be rendered."""
    facts = (
        "Every line matches the product page or what you told me."
        if job.produced.filter(kind="finished_ad").exists()
        else "Every line matches the product page."
    )
    if job.target_seconds is None:
        return facts
    fit = target_length_fit(list(job.scenes.values_list("line", flat=True)), job.target_seconds)
    if not fit.fits:
        return (
            facts + " The script was kept longer than the "
            f"{job.target_seconds}-second target after shortening."
        )
    return facts.rstrip(".") + f", and the whole clips fit your {job.target_seconds}-second target."


def why_the_checks_havent_passed(job: Job) -> str | None:
    """Why the script can't go on to be rendered yet, or None once the planning checks have
    passed: every line passed the fact check and fills its B-roll line limits at the fixed speed,
    and the script fits the default clip cap, its target, or was kept longer after its
    allowed shortenings."""
    unchecked = job.scenes.filter(fact_checked=False).first()
    if unchecked is not None:
        return (
            f"scene {unchecked.number}'s line hasn't passed the fact check, and nothing is made "
            "until every line has. Run the planning checks first."
        )
    words_per_second = FIXED_WORDS_PER_SECOND
    to_fit = _a_line_to_fit(job, words_per_second)
    if to_fit is not None:
        scene, fit = to_fit
        how_long = (
            "is too short for its clip"
            if fit in {"lengthen", "too_short"}
            else "takes longer to say than a scene can last"
        )
        return f"scene {scene.number}'s line {how_long}. Run the planning checks first."
    target = job.target_seconds
    if target is None:
        default_fit = default_length_fit(list(job.scenes.values_list("line", flat=True)))
        if not default_fit.fits:
            return (
                f"the whole clips and end card add up to {default_fit.seconds} seconds, "
                "over the default 35 seconds. "
                "Run the planning checks to shorten the lines first."
            )
        return None
    if _kept_longer(job):
        return None
    target_fit = target_length_fit(list(job.scenes.values_list("line", flat=True)), target)
    if not target_fit.fits:
        return (
            f"the whole clips and end card add up to {target_fit.seconds} seconds, over the "
            f"{target}-second target. Run the planning checks first."
        )
    return None


# The music is made before the clips, so how long the ad will be is only known roughly: this
# much more is made than the voice takes to say the script, so the music doesn't run out.

# The producer gives only the mood: the music always leaves room for the voice.
MUSIC_PROMPT = (
    "{mood}. Background music for a short video ad, played under a person speaking. "
    "Instrumental only, no vocals, no singing."
)


def music_mood(mood: str) -> str:
    """The mood the producer gave, tidied, so moods that differ only in spacing, a capital
    first letter or a closing full stop ask for the same music."""
    mood = " ".join(mood.split()).rstrip(".")
    return mood[:1].upper() + mood[1:]


def music_prompt(mood: str) -> str:
    """What the music model is asked for, in a mood tidied by `music_mood`."""
    return MUSIC_PROMPT.format(mood=mood)


def music_seconds(job: Job) -> int:
    """Music for an H3 ad's whole clips and the end card after them, predicting unmade clips
    from the fixed pace."""
    seconds = float(END_CARD_SECONDS)
    for scene in job.scenes.all():
        clip = (
            scene.produced.filter(
                kind=ProducedItem.Kind.CLIP,
                step__status=SceneStep.Status.FINISHED,
                **SceneStep.made_for(scene, through="step__"),
            )
            .order_by("version")
            .last()
        )
        seconds += (
            clip.seconds
            if clip is not None and clip.seconds is not None
            else h3_clip_seconds(count_words(scene.line))
        )
    return math.ceil(seconds)


def create_music(job: Job, prompt: str, seconds: int) -> ProducedItem:
    """Make `seconds` of music as `prompt` asks, kept as the job's next version of its music.

    Music paid for before a worker stopped, but not kept, is kept rather than paid for
    again."""
    kept = set(job.produced.values_list("file", flat=True))
    paid_for = [
        output["file"]
        for output in job.model_calls.filter(
            purpose="make_music",
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff=MusicHandoff(prompt=prompt, seconds=seconds).model_dump(),
        ).values_list("output", flat=True)
        if output is not None and output["file"] not in kept
    ]
    file = (
        paid_for[-1]
        if paid_for
        else make_music(job=job, purpose="make_music", prompt=prompt, seconds=seconds)
    )
    return _keep_music(job, file, prompt, seconds)


def use_music_again(job: Job, music: ProducedItem) -> ProducedItem:
    """Make music made before the ad's music again, as its next version, without paying for
    it again: the ad's music is always its latest version."""
    assert music.seconds is not None, "music is made as long as it was asked to be"
    return _keep_music(job, music.file, music.text, round(music.seconds))


def _keep_music(job: Job, file: str, prompt: str, seconds: int) -> ProducedItem:
    last = job.produced.filter(kind=ProducedItem.Kind.MUSIC).aggregate(last=Max("version"))
    return ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.MUSIC,
        version=(last["last"] or 0) + 1,
        file=file,
        seconds=seconds,
        text=prompt,
    )


# How a product photo is labelled for a model that picks one of them.
PHOTO_LABEL = re.compile(r"Photo (\d+)")


# Which Postgres advisory lock stands for "one clean picture of the shop owner's things drawn
# at a time per ad", with the job's.
CLEAN_PICTURE_LOCK = 3


def clean_picture(scene: Scene, row: ReferencePhoto) -> str:
    """The clean picture of only `row`'s thing, drawn from the shop owner's photo once per ad
    (see _clean_picture), which joins the job's library as that photo, labelled, the first
    time it is drawn: unless it is a photo of the scene's owner part, whose picture enters
    the library as the part's own (jobs.owner_parts.draw_part_picture). Gives its key in the
    file store."""
    drawn = _clean_picture(scene, row)
    part = owner_parts.owner_part(scene)
    if part is None or not reference_photos.same_thing(row.thing, part.what):
        library_items.add_owner_photo(scene.job, row.file, drawn, f"the shop owner's {row.thing}")
    return drawn


def _clean_picture(scene: Scene, row: ReferencePhoto) -> str:
    """The clean picture of only `row`'s thing, drawn from the shop owner's photo (Julian
    2026-10-15 17:43) once per ad, by whichever step needs it first, and kept on every scene's
    row of the same photo and thing. It is a cut-out (jobs.cut_out), checked beside the photo
    by `owner_photo_check`, and drawn again when the check fails, at most
    checked_pictures.MOST_REDRAWS times
    (D8). Gives its key in the file store. Raises ReferencePhotoUnusable when it still fails,
    or the picture model refuses the photo past its retry: nothing more is made for the
    scene, and the shop owner is asked for another photo.

    Run again, as after a worker stopped, it pays for nothing already paid for: each picture
    drawn is reused in turn, and each check answered from its record."""
    if row.drawn:
        return row.drawn
    job = scene.job
    with _one_job_at_a_time(CLEAN_PICTURE_LOCK, job):
        same = ReferencePhoto.objects.filter(scene__job=job, file=row.file, thing__iexact=row.thing)
        drawn = same.exclude(drawn="").values_list("drawn", flat=True).first()
        if drawn:
            row.drawn = drawn
            return drawn
        try:
            file = cut_out.redraw_with_check(
                job,
                purpose="make_reference_photo_picture",
                prompt=reference_photos.clean_picture_prompt(row.thing),
                photo=row.file,
                check=owner_photo_check(
                    job,
                    row.file,
                    keep=f"the shop owner's {row.thing}",
                    named=f"the clean picture of the shop owner's {row.thing}",
                    photo_named=f"the shop owner's photo of their {row.thing}",
                ),
                unchecked_passes=True,
            )
        except cut_out.CutOutFailed as failed:
            if failed.refused is not None:
                raise ReferencePhotoUnusable(
                    f"the picture model refused to draw the {row.thing} from the shop owner's "
                    f"photo ({failed.refused}), so nothing more was made for scene "
                    f"{scene.number}. Ask them for another photo of the {row.thing}.",
                    thing=row.thing,
                ) from failed
            raise ReferencePhotoUnusable(
                f"the clean copy of the {row.thing} drawn from the shop owner's photo failed "
                f"all {len(failed.problems)} tries ({' '.join(failed.problems)}), so nothing "
                f"more was made for scene {scene.number}. Ask them for another photo of the "
                f"{row.thing}, showing it clearly.",
                thing=row.thing,
            ) from failed
        same.update(drawn=file)
        row.drawn = file
        return file


def owner_photo_check(
    job: Job, photo: str, *, keep: str, named: str, photo_named: str
) -> cut_out.Check:
    """How a clean picture of only `keep`, named `named`, drawn from the shop owner's photo
    at `photo` (named `photo_named`) is checked: by the cut-out check, asked once, as the
    product-only versions of library photos are (spec #210 point 6). NOT APPROVED YET
    (audit item 24: never asked; before, it was the starting picture's 3-ask vote, which
    `judge_starting_picture` still holds). Unchecked when QUALITY_CHECKS is off. A cut-out the
    check's filter refuses to look at, or that gives no answer, is kept unchecked, recorded in
    red."""

    def check(file: str) -> cut_out.Said:
        if not settings.QUALITY_CHECKS:
            return None
        said = cut_out.check_cut_out(
            job,
            file,
            photo,
            keep=keep,
            named=named,
            photo_named=photo_named,
            needs_photo=False,
        )
        if isinstance(said, cut_out.Unchecked) and said.refused:
            post_refused_by_the_filter(job, named, "it is kept unchecked")
        elif isinstance(said, cut_out.Unchecked):
            # Kept unchecked all the same: never silently (the wording is Claude's, not
            # approved yet).
            post_notice(
                job,
                notices.OWNER_PHOTO_UNCHECKED,
                f"Checking {named} gave no answer ({said.why}), so it is kept unchecked.",
                Message.Level.PROBLEM,
            )
        return said

    return check


# Which Postgres advisory locks stand for "one before-picture per ad": this number with the
# job's. Any number will do, as long as nothing else takes the same one.
BEFORE_PICTURE_LOCK = 2


def _before_picture(step: SceneStep) -> ProducedItem:
    """The ad's before-picture: the place every B-roll scene happens in, with each thing the
    scenes act on and every problem any of them shows, as it is before any starts, and no
    product or person. Every B-roll starting picture is drawn from it, so the scenes match:
    drawn alone, each got its own toilet and its own stains (round 3 #8 s3 FAIL,
    research/scenes-match/README.md).

    Drawn once per ad, by the first B-roll starting picture that needs it, from the plan's
    B-roll scenes as they stand then. Starting pictures made at the same time wait for it,
    and every one made later uses it too, so they all match. Run again, as after a worker
    stopped, it pays for nothing already paid for."""
    job = step.scene.job
    with _one_job_at_a_time(BEFORE_PICTURE_LOCK, job):
        made = latest(job, ProducedItem.Kind.BEFORE_PICTURE)
        if made is not None:
            return made
        plan = call_model(
            job=job,
            purpose="plan_before_picture",
            instructions=BEFORE_PICTURE_INSTRUCTIONS,
            handoff=BeforePictureHandoff(
                script=list(job.scenes.values_list("line", flat=True)),
                broll_scenes=[
                    BrollSceneToDraw(
                        scene=scene.number,
                        line=scene.line,
                        shows=scene.shows,
                        broll_kind=scene.broll_kind,
                        usage=scene.usage,
                        result=scene.result,
                    )
                    for scene in job.scenes.exclude(broll_kind="")
                ],
                conversation=_conversation(job, until=step.started_at),
            ),
            output=BeforePicturePlan,
            pay_once=True,
        )
        paid_for = (
            _paid_calls(job, "make_before_picture").filter(handoff={"prompt": plan.prompt}).last()
        )
        file = (
            paid_for.output["file"]
            if paid_for is not None and paid_for.output is not None
            else draw_picture(job=job, purpose="make_before_picture", prompt=plan.prompt)
        )
        return ProducedItem.objects.create(
            job=job, kind=ProducedItem.Kind.BEFORE_PICTURE, file=file, text=plan.prompt
        )


# Which Postgres advisory lock stands for "one person-in-room picture and one room alone per
# ad", with the job's.
PERSON_AND_ROOM_LOCK = 4


def person_and_room(step: SceneStep, person_shown: str) -> tuple[ProducedItem | None, ProducedItem]:
    """The ad's person-in-room picture and its room alone (spec #210 point 12, spec #401): the
    person-in-room picture is the presenter in the before-picture's room, relaxed, nothing
    else happening, for the scenes they are seen in; the room alone is that room with nothing
    of any scene in it, for the scenes nobody is seen in (spec 2 item 53).

    The presenter in the room is drawn first, in one go, from the portrait as the face
    reference and words for them and the room (PERSON_IN_ROOM_PROMPT), once per portrait (a
    remade person is drawn there again); no step merges a finished portrait with a finished
    room, which made her look pasted on. The room alone is then drawn once per ad as an edit
    of it with her taken out (ROOM_FROM_PERSON_PROMPT), so B-roll happens in her room. An ad
    with no portrait yet has no person-in-room picture (None), which only a clip showing the
    presenter misses, and its room alone is drawn from the before-picture
    (ROOM_ALONE_PROMPT). Each is shown in the chat once.

    A clip of `step` showing the presenter (`person_shown`) in a place of its own also has
    them drawn there, when they aren't yet (`presenter_in_place`). Gives back the ad's
    person-in-room picture, then its room alone; PlaceNotDrawn when one can't be drawn.

    Run again, as after a worker stopped, it pays for nothing already paid for."""
    job = step.scene.job
    before = _before_picture(step)
    with _one_job_at_a_time(PERSON_AND_ROOM_LOCK, job):
        portrait = latest(job, ProducedItem.Kind.PORTRAIT)
        if portrait is None:
            if person_shown == Scene.PersonShown.HAS_FACE:
                # Nobody to draw in it: said in red, and the clip is sent no such picture.
                raise PlaceNotDrawn(ProducedItem.Kind.PERSON_IN_ROOM)
            room = latest(job, ProducedItem.Kind.ROOM_ALONE) or _draw_once(
                job, ProducedItem.Kind.ROOM_ALONE, "make_room_alone", ROOM_ALONE_PROMPT, before
            )
            show_once(job, [room])
            return None, room
        person = ad_place(job, "person_in_room") or _draw_once(
            job,
            ProducedItem.Kind.PERSON_IN_ROOM,
            "make_person_in_room",
            _person_in_room_prompt(job, AD_ROOM_WORDS.format(before=before.text)),
            before,
            portrait=portrait,
        )
        room = latest(job, ProducedItem.Kind.ROOM_ALONE) or _draw_once(
            job, ProducedItem.Kind.ROOM_ALONE, "make_room_alone", ROOM_FROM_PERSON_PROMPT, person
        )
        show_once(job, [person, room])
        scene = Scene.objects.get(pk=step.scene_id)
        if (
            scene.place is not None
            and person_shown == Scene.PersonShown.HAS_FACE
            and own_place(scene, "person_in_room") is None
        ):
            try:
                presenter_in_place(scene)
            except Exception as error:
                raise PlaceNotDrawn(ProducedItem.Kind.SCENE_PERSON_IN_ROOM) from error
    return person, room


def _person_in_room_prompt(job: Job, room: str) -> str:
    """PERSON_IN_ROOM_PROMPT for the job's presenter, in the room the words `room` describe."""
    return PERSON_IN_ROOM_PROMPT.format(who=_who_the_person_is(job).strip(), room=room.strip())


def draw_the_place(step: SceneStep, person_shown: str) -> None:
    """Draw the ad's pictures of its place, and the presenter in the scene's own place, when
    they aren't drawn yet (`person_and_room`), for a clip of `step` showing `person_shown`:
    so the picture of its place every send of a B-roll clip takes (cut-and-send's tier 3)
    is there before any of its sends is built. One that can't be drawn is said
    (`say_the_place_wasnt_drawn`), and the clip goes without it; the next clip draws it again.

    Run again, as after a worker stopped, it pays for nothing already paid for."""
    try:
        person_and_room(step, person_shown)
    except PlaceNotDrawn as not_drawn:
        say_the_place_wasnt_drawn(step, not_drawn)


# The ad's picture of its place, and one scene's own, by which drawn picture it is.
_AD_PLACE: dict[Drawn, ProducedItem.Kind] = {
    "person_in_room": ProducedItem.Kind.PERSON_IN_ROOM,
    "room": ProducedItem.Kind.ROOM_ALONE,
}
_SCENE_PLACE: dict[Drawn, ProducedItem.Kind] = {
    "person_in_room": ProducedItem.Kind.SCENE_PERSON_IN_ROOM,
    "room": ProducedItem.Kind.SCENE_ROOM,
}
_ROOMS = (ProducedItem.Kind.ROOM_ALONE, ProducedItem.Kind.SCENE_ROOM)


def person_shown_for(kind: str, asked: str = "") -> Scene.PersonShown:
    """H3 talking scenes show her face; B-roll keeps it out of frame unless `asked` is "has
    face" (a one-sided brush on her hair: picture_rules.NO_FACE_RULE's one exception)."""
    if kind == Scene.Kind.TALKING or asked == Scene.PersonShown.HAS_FACE:
        return Scene.PersonShown.HAS_FACE
    return Scene.PersonShown.NO_FACE


def face_kept(was_kind: str, was_shown: str, kind: str, asked: str) -> Scene.PersonShown:
    """Who a scene of kind `was_kind` showing `was_shown` shows once it is of kind `kind`,
    asked `asked`: a B-roll scene keeps her face only when it already showed it (the planner
    gives it to a one-sided brush on her hair), so a talking scene turned B-roll never
    carries its face over."""
    had_face = was_kind == Scene.Kind.BROLL and was_shown == Scene.PersonShown.HAS_FACE
    return person_shown_for(kind, asked.strip() if had_face else "")


def drawn_for(person_shown: str) -> Drawn:
    """Which drawn picture a scene is sent (spec #210 point 12): the presenter in the room
    when they are seen, the room alone when nobody is."""
    return "person_in_room" if person_shown == Scene.PersonShown.HAS_FACE else "room"


def scene_place(scene: Scene, person_shown: str) -> tuple[Drawn, ProducedItem | None]:
    """The drawn picture (tier 3) a clip of `scene` showing `person_shown` is sent: of the
    scene's own place when it has one (Scene.place: set_place's or the fixing agent's
    place_edit's), else of the ad's; the presenter in it drawn from the portrait as it
    stands. None when that picture isn't drawn yet: never the picture of another place or
    of the person as they were."""
    person_shown = person_shown_for(scene.kind, scene.person_shown)
    drawn = drawn_for(person_shown)
    return drawn, place_of(scene, drawn)


def talking_portrait_and_room(scene: Scene) -> tuple[ProducedItem, ProducedItem] | None:
    """The presenter's portrait and the room alone a talking scene's starting picture is drawn
    from, in place of its picture of her in the room, whose camera the picture model copies
    into every talking scene; the camera setup comes from the words instead (Julian, Nov 9
    2026, thread "Test talking scene camera angles"). None for a B-roll scene, while either
    isn't drawn, or when the scene's own place is an edited picture of her that no room alone
    shows: then that picture is used, so the edit isn't lost."""
    if scene.kind != Scene.Kind.TALKING:
        return None
    portrait = latest(scene.job, ProducedItem.Kind.PORTRAIT)
    room = place_of(scene, "room")
    if portrait is None or room is None:
        return None
    if scene.place_id is not None and not _room_shows_place(room, newest_own_place(scene)):
        return None
    return portrait, room


def _room_shows_place(room: ProducedItem, place: ProducedItem | None) -> bool:
    """Whether the room alone shows the scene's own place as it stands: it is that picture,
    or one of the two was drawn from the other."""
    return (
        place is None or room.pk in (place.pk, place.made_from_id) or room.made_from_id == place.pk
    )


def place_inputs_now(scene: Scene) -> list[list[str]]:
    """Each set of place pictures a current starting picture of the scene may have been drawn
    from, by file: its shared picture (`scene_place`), and for her talking her portrait and
    the room alone (`talking_portrait_and_room`). A picture drawn from either can be filmed."""
    _, shared = scene_place(scene, scene.person_shown)
    talking = talking_portrait_and_room(scene)
    return [
        *([[shared.file]] if shared is not None else []),
        *([[item.file for item in talking]] if talking is not None else []),
    ]


def place_of(scene: Scene, drawn: Drawn) -> ProducedItem | None:
    """The scene's `drawn` picture of its place as it stands (`scene_place`), if drawn."""
    if scene.place_id is not None:
        return own_place(scene, drawn)
    return ad_place(scene.job, drawn)


def ad_place(job: Job, drawn: Drawn) -> ProducedItem | None:
    """The ad's room alone, or its picture of the presenter as they stand in it, if drawn."""
    if drawn == "room":
        return latest(job, ProducedItem.Kind.ROOM_ALONE)
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if portrait is None:
        return None
    return (
        job.produced.filter(kind=ProducedItem.Kind.PERSON_IN_ROOM, person=portrait)
        .order_by("id")
        .last()
    )


def newest_own_place(scene: Scene) -> ProducedItem | None:
    """The scene's own place (Scene.place): the newest picture set_place or place_edit made
    for it, of either kind."""
    return Scene.with_removed.get(pk=scene.pk).place


def own_place(scene: Scene, drawn: Drawn) -> ProducedItem | None:
    """The scene's `drawn` picture of its own place: for the room, the room it is or the room
    its presenter picture was drawn or edited in (the ad's room alone for an edit of the ad's
    presenter picture); for the presenter, the picture it is while drawn from the portrait as
    it stands, else the presenter drawn again in its room for that portrait
    (`presenter_in_place`). None when the scene has no place of its own, or that picture
    isn't drawn."""
    place = newest_own_place(scene)
    if place is None:
        return None
    if drawn == "room":
        # An edit of the ad's presenter picture is in the ad's room.
        return _room_of(place)
    portrait = latest(scene.job, ProducedItem.Kind.PORTRAIT)
    if portrait is None:
        return None
    if place.kind == ProducedItem.Kind.SCENE_PERSON_IN_ROOM and place.person_id == portrait.pk:
        return place
    return (
        scene.produced.filter(
            kind=ProducedItem.Kind.SCENE_PERSON_IN_ROOM,
            person=portrait,
            made_from=_room_of(place),
            pk__gt=place.pk,
        )
        .order_by("id")
        .last()
    )


def _room_of(place: ProducedItem) -> ProducedItem | None:
    """The room a picture of a place shows: itself for a room, else the room the presenter
    picture it was made from, or edited from, was drawn in, or, for a presenter picture drawn
    from words (spec #401), the room taken out of it; for the ad's presenter picture drawn
    again after a remake, the ad's room alone."""
    found: ProducedItem | None = place
    made: list[ProducedItem] = []
    while found is not None and found.kind not in _ROOMS:
        made.append(found)
        found = found.made_from
    if found is not None:
        return found
    taken_out = (
        ProducedItem.objects.filter(kind__in=_ROOMS, made_from__in=made).order_by("id").last()
    )
    if taken_out is not None:
        return taken_out
    # The ad's presenter picture drawn again for a remade person, or an edit of it: the ad's
    # room alone is drawn once per ad, from the first (docs/limitations.md).
    if any(each.kind == ProducedItem.Kind.PERSON_IN_ROOM for each in made):
        return latest(place.job, ProducedItem.Kind.ROOM_ALONE)
    return None


def _place_words(place: ProducedItem) -> str:
    """The words a picture of a place shows the room by, for drawing the presenter in it
    again (`presenter_in_place`): the words its room was first drawn from (the before-
    picture's for the ad's, a new place's for a scene's own), then each change made to it
    since, in order. Pictures taken from another (a room taken out of a presenter picture,
    or one drawn before spec #401 by merging two pictures) add nothing."""
    changes: list[str] = []
    found: ProducedItem | None = place
    while found is not None:
        if found.kind == ProducedItem.Kind.BEFORE_PICTURE:
            words = AD_ROOM_WORDS.format(before=found.text)
            break
        filled = (
            _filled_in(PERSON_IN_ROOM_PROMPT, found.text)
            or _filled_in(SCENE_NEW_PLACE_PROMPT, found.text)
            or _filled_in(SCENE_PLACE_EDIT_PROMPT, found.text)
            or {}
        )
        if "edit" in filled:
            changes.append(filled["edit"])
        elif "room" in filled or "place" in filled:
            words = filled["room"] if "room" in filled else filled["place"]
            break
        found = found.made_from
    else:
        raise ValueError(f"no words say which room picture {place.pk} shows")
    if not changes:
        return words
    return CHANGED_PLACE_WORDS.format(place=words, changes=" ".join(reversed(changes)))


def _filled_in(template: str, text: str) -> dict[str, str] | None:
    """What was put into each {field} of `template` to make `text`, or None when `text`
    wasn't made from it."""
    parts = re.split(r"\{(\w+)\}", template)
    pattern = "".join(
        re.escape(part) if index % 2 == 0 else f"(?P<{part}>.*)" for index, part in enumerate(parts)
    )
    found = re.fullmatch(pattern, text, re.DOTALL)
    return found.groupdict() if found else None


class NoPlaceYet(Exception):
    """A scene's place can't be edited: the ad has no such picture yet."""


class NoPresenterYet(Exception):
    """The presenter can't be drawn in a scene's new place: the ad has no portrait yet."""


def edit_scene_place(scene: Scene, drawn: Drawn, edit: str) -> ProducedItem:
    """`scene`'s own `drawn` picture, made by the picture model from the one the scene has
    now (`place_of`: its own, else the ad's; the presenter drawn in its own place first when
    they aren't yet) with the small change `edit`, as SCENE_PLACE_EDIT_PROMPT orders: for
    that scene only, and its place from now on (Scene.place). NoPlaceYet when there is
    nothing to edit. Run again, as after a worker stopped, it pays for nothing already paid
    for."""
    base = place_of(scene, drawn)
    if base is None and drawn == "person_in_room" and scene.place_id is not None:
        base = presenter_in_place(scene)
    if base is None:
        raise NoPlaceYet(drawn)
    prompt = SCENE_PLACE_EDIT_PROMPT.format(edit=edit.strip())
    file = _edited_once(scene.job, "edit_scene_place", prompt, [base.file])
    made = _kept_place(
        scene, _SCENE_PLACE[drawn], file, made_from=base, prompt=prompt, person=base.person
    )
    _set_place(scene, made)
    return made


def new_scene_place(scene: Scene, drawn: Drawn, place: str) -> list[ProducedItem]:
    """`scene`'s own place, drawn fresh from the description `place`: for that scene only,
    and its place from now on (Scene.place). A room alone is drawn from the words
    (SCENE_NEW_PLACE_PROMPT); a person-in-room picture is drawn as the ad's is (spec #401):
    the presenter in that place first, in one go from the portrait and words
    (PERSON_IN_ROOM_PROMPT), then the scene's room alone as an edit of it with her taken out
    (ROOM_FROM_PERSON_PROMPT). Gives back what was made, the picture asked for last. Run
    again, it pays for nothing already paid for."""
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if drawn == "person_in_room" and portrait is None:
        raise NoPresenterYet()
    if drawn == "person_in_room":
        assert portrait is not None
        person = _presenter_drawn(scene, portrait, place, made_from=None)
        file = _edited_once(job, "make_scene_room", ROOM_FROM_PERSON_PROMPT, [person.file])
        room = _kept_place(
            scene,
            ProducedItem.Kind.SCENE_ROOM,
            file,
            made_from=person,
            prompt=ROOM_FROM_PERSON_PROMPT,
        )
        _set_place(scene, person)
        return [room, person]
    prompt = SCENE_NEW_PLACE_PROMPT.format(place=place.strip())
    paid_for = _paid_calls(job, "draw_scene_room").filter(handoff={"prompt": prompt}).last()
    file = (
        paid_for.output["file"]
        if paid_for is not None and paid_for.output is not None
        else draw_picture(job=job, purpose="draw_scene_room", prompt=prompt)
    )
    room = _kept_place(scene, ProducedItem.Kind.SCENE_ROOM, file, made_from=None, prompt=prompt)
    _set_place(scene, room)
    return [room]


def presenter_in_place(scene: Scene) -> ProducedItem:
    """The presenter, from the portrait as it stands, drawn again in the scene's own place as
    the ad's is drawn (spec #401): in one go from the portrait and the words of that place
    (`_place_words`), never by merging the portrait with its picture. For a scene whose place
    is a room alone, or whose presenter picture shows the person as they were. Kept as made
    from the place's room, so `own_place` finds it. The scene's place stays as it is: the
    picture is of it. NoPresenterYet before the person is made. Run again, it pays for
    nothing already paid for."""
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if portrait is None:
        raise NoPresenterYet()
    place = newest_own_place(scene)
    assert place is not None, "only a scene with its own place has the presenter drawn in it"
    room = _room_of(place)
    assert room is not None, "every picture of a place shows a room"
    return _presenter_drawn(scene, portrait, _place_words(place), made_from=room)


def _presenter_drawn(
    scene: Scene, portrait: ProducedItem, room: str, *, made_from: ProducedItem | None
) -> ProducedItem:
    """The presenter in `scene`'s own place, the room the words `room` describe, drawn in one
    go from `portrait` (PERSON_IN_ROOM_PROMPT) and kept as the scene's."""
    prompt = _person_in_room_prompt(scene.job, room)
    file = _edited_once(scene.job, "make_scene_person_in_room", prompt, [portrait.file])
    return _kept_place(
        scene,
        ProducedItem.Kind.SCENE_PERSON_IN_ROOM,
        file,
        made_from=made_from,
        prompt=prompt,
        person=portrait,
    )


def _set_place(scene: Scene, place: ProducedItem) -> None:
    """Make `place` the scene's own place, saved at once."""
    Scene.with_removed.filter(pk=scene.pk).update(place=place)
    scene.place = place


def _edited_once(job: Job, purpose: str, prompt: str, pictures: list[str]) -> str:
    """The picture model's picture made from `pictures` as `prompt` says, unless it was made
    before the worker stopped."""
    paid_for = (
        _paid_calls(job, purpose).filter(handoff={"prompt": prompt, "pictures": pictures}).last()
    )
    if paid_for is not None and paid_for.output is not None:
        file: str = paid_for.output["file"]
        return file
    return edit_picture(job=job, purpose=purpose, prompt=prompt, pictures=pictures)


def _kept_place(
    scene: Scene,
    kind: ProducedItem.Kind,
    file: str,
    *,
    made_from: ProducedItem | None,
    prompt: str,
    person: ProducedItem | None = None,
) -> ProducedItem:
    """The scene's next version of `kind`, the picture `file`; the same one when it was kept
    before the worker stopped, so the newest stays the scene's."""
    newest = scene.produced.filter(kind=kind).order_by("version").last()
    if newest is not None and newest.file == file:
        return newest
    return ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        kind=kind,
        version=(newest.version + 1) if newest is not None else 1,
        file=file,
        made_from=made_from,
        person=person,
        text=prompt,
    )


# What the shop owner is told each of the ad's pictures of its place is, when it can't be drawn.
PLACE_PICTURE_NAMES = {
    ProducedItem.Kind.ROOM_ALONE: "picture of the ad's place on its own",
    ProducedItem.Kind.PERSON_IN_ROOM: "picture of the presenter in the ad's place",
    ProducedItem.Kind.SCENE_PERSON_IN_ROOM: "picture of the presenter in a scene's own place",
}


class PlaceNotDrawn(Exception):
    """The picture model couldn't draw the ad's room alone or its person-in-room picture."""

    def __init__(self, kind: ProducedItem.Kind) -> None:
        super().__init__(f"the {PLACE_PICTURE_NAMES[kind]} couldn't be drawn")
        self.kind = kind


def say_the_place_wasnt_drawn(step: SceneStep, failed: PlaceNotDrawn) -> None:
    """Record, in red, that one of the ad's pictures of its place, or the presenter in a
    scene's own place, couldn't be drawn for the clip of `step`, once per scene and picture:
    a clip it was for is sent no picture of its place, and the next B-roll clip draws it
    again."""
    job = step.scene.job
    logger.error("Job %s: %s", job.pk, failed, exc_info=failed.__cause__)
    text = (
        f"The {PLACE_PICTURE_NAMES[failed.kind]} couldn't be drawn for scene "
        f"{step.scene.number}'s clip, so the ad doesn't have it yet. It is drawn again when the "
        "next scene showing the product gets its clip."
    )
    # A scene's clip sent again, as after a worker stopped, tells nothing twice.
    job.refresh_from_db(fields=["warnings"])
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(
            job, notices.PLACE_NOT_DRAWN, text, Message.Level.PROBLEM, scene=step.scene.number
        )


def _draw_once(
    job: Job,
    kind: ProducedItem.Kind,
    purpose: str,
    prompt: str,
    made_from: ProducedItem,
    *,
    portrait: ProducedItem | None = None,
) -> ProducedItem:
    """Have the picture model draw `kind` as `prompt` orders, from the portrait alone when
    given (the presenter in a place, drawn in one go from words), else as an edit of
    `made_from`, unless it was drawn before the worker stopped, and keep it."""
    pictures = [portrait.file] if portrait else [made_from.file]
    paid_for = _paid_calls(job, purpose).filter(handoff={"prompt": prompt, "pictures": pictures})
    drawn = paid_for.last()
    file = (
        drawn.output["file"]
        if drawn is not None and drawn.output is not None
        else _drawn(kind, purpose, prompt, pictures, job)
    )
    newest = latest(job, kind)
    return ProducedItem.objects.create(
        job=job,
        kind=kind,
        version=newest.version + 1 if newest is not None else 1,
        file=file,
        made_from=made_from,
        person=portrait,
        text=prompt,
    )


def _drawn(
    kind: ProducedItem.Kind, purpose: str, prompt: str, pictures: list[str], job: Job
) -> str:
    """Have the picture model draw `kind`: PlaceNotDrawn, from what went wrong, if it can't."""
    try:
        return edit_picture(job=job, purpose=purpose, prompt=prompt, pictures=pictures)
    except Exception as error:
        raise PlaceNotDrawn(kind) from error


def show_once(job: Job, made: list[ProducedItem]) -> None:
    """Show the chat each of `made` it wasn't shown yet, as the portrait is shown."""
    if job.session is None:
        return
    unseen = [
        item
        for item in made
        if not Attachment.objects.filter(message__session=job.session, file=item.file).exists()
    ]
    if unseen:
        messages.add(
            job.session,
            role=Message.Role.AGENT,
            carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, item.file) for item in unseen],
        )


def judge_starting_picture(
    job: Job | None,
    picture: str,
    photo: str,
    *,
    about: str,
    named: str = "",
    photo_named: str = "the shop photo",
    owner_part: owner_parts.OwnerPart | None = None,
) -> list[str]:
    """What is wrong with the starting picture at `picture`, from a judge shown the picture,
    then the shop photo at `photo` it was drawn from, for how the product is built: each
    problem its failing answers found, when FAILS_TO_FAIL of its ASKS answers
    fail it; nothing when it passes. An ask the safety filter blocks, or whose answer can't
    be had, counts as not checked: the picture is paid for, and failing it would pay for it
    again. `about` names the picture in the log. A picture that draws `owner_part` from its
    sentence is checked with the sentence for that part, as no photo shows it.

    A shop photo the safety filter refuses is left out, and the picture judged without it;
    the chat is told in red, naming the picture as `named` does (its starting picture, by
    `about`, unless told) and the photo as `photo_named` does. A job of None, as in an eval,
    has no chat."""
    named = named or f"{about}'s starting picture"
    shop_photo = Image(label="The shop photo:", key=photo)
    passes = 0
    problems: list[str] = []
    left_out: list[Image] = []
    refused = False
    # Asked until the answers settle it, so a picture two answers pass isn't asked a third.
    for ask in range(1, ASKS + 1):
        if passes > ASKS - FAILS_TO_FAIL or len(problems) >= FAILS_TO_FAIL:
            break
        try:
            # The shop photo the safety filter refuses is left out, and the picture judged
            # without it (requirement 22); the picture judged never is.
            handoff = owner_parts.check_handoff(ask, owner_part)
            verdict, left = past_the_safety_filter(
                lambda shown: call_model(
                    job=job,
                    purpose="check_starting_picture",
                    instructions=(
                        STARTING_PICTURE_CHECK
                        if owner_part is None
                        else f"{STARTING_PICTURE_CHECK}\n\n{owner_parts.CHECK_RULE}"
                    ),
                    handoff=handoff,  # noqa: B023 (asked before the next loop)
                    output=StartingPictureVerdict,
                    images=shown,
                    pay_once=True,
                ),
                [Image(label="The picture to judge:", key=picture), shop_photo],
                may_leave_out=lambda image: image == shop_photo,
            )
            left_out += left
        except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
            logger.warning(
                "Ask %s about %s's starting picture went unchecked: %s", ask, about, error
            )
            refused = refused or isinstance(error, BlockedBySafetyFilter)
            continue
        if verdict.verdict == "pass":
            passes += 1
        else:
            assert verdict.problem is not None, "a failing answer says why"
            problems.append(verdict.problem)
    if job is not None and left_out:
        post_left_out_by_the_filter(job, [photo_named], f"{named} was checked")
    if job is not None and refused:
        unchecked = passes + len(problems) == 0
        post_refused_by_the_filter(
            job,
            named,
            "it is kept unchecked" if unchecked else "some of its checks couldn't be made",
        )
        if unchecked:
            text = f"Please look at {named} yourself: I couldn't check it."
            if all(warning["text"] != text for warning in job.warnings):
                post_notice(job, notices.CHECK_IT_YOURSELF, text, Message.Level.INFO)
    return problems if len(problems) >= FAILS_TO_FAIL else []


def _the_clip_judges_answer(new: ProducedItem) -> fix_check.Settled:
    """What the clip judge's answers about `new` settled on the shop owner's change, asked
    beside the clip the step's fix was for (clip_judge.complained_about, the same `old`):
    unchecked when it was judged without it, as when its frames couldn't be cut, or not
    judged at all."""
    ruling = new.ruling or {}
    if "compared_with" not in ruling:
        return fix_check.Settled("unchecked", "", [])
    return fix_check.settle_all(clip_judge.owner_change_answers(ruling))


def _transcribe_clip(step: SceneStep) -> ProducedItem:
    """Write down what was heard in the audio the step was started for, word by word with
    when each was said, exactly as heard: nothing is tidied or matched to the line. Gives
    back the transcript, kept as the scene's next version.

    Run again, as after a worker stopped, it pays for nothing already paid for: a transcript
    made but not kept is kept rather than made again."""
    made = step.produced.first()
    if made is not None:
        return made
    scene = step.scene
    job = scene.job
    audio = step.made_from
    assert audio is not None, "a transcript step is started for its audio"
    paid_for = _paid_for_before(job, "transcribe_line", charged_to=step.tool_call)
    heard = (
        transcription_from(paid_for)
        if paid_for
        else transcribe(
            job=job,
            purpose="transcribe_line",
            audio_key=_clip_sound(audio.file),
        )
    )
    return ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.TRANSCRIPT,
        version=_next_version(scene, ProducedItem.Kind.TRANSCRIPT),
        text=heard.text,
        words=transcription_output(heard)["words"],
        made_from=audio,
    )


def _clip_sound(file: str) -> str:
    """Scribe receives the clip's own sound as WAV, without changing the kept clip."""
    with tempfile.TemporaryDirectory() as folder:
        clip = Path(folder) / "clip.mp4"
        audio = Path(folder) / "sound.wav"
        clip.write_bytes(file_store.read(file))
        assembly._ffmpeg("-i", str(clip), "-vn", "-acodec", "pcm_s16le", str(audio))
        return file_store.save("clip-sound.wav", audio.read_bytes())


def transcribe_clip(step: SceneStep, clip: ProducedItem) -> ProducedItem | None:
    """Keep a clip's transcript once, including across a worker restart. A failed
    transcription is recorded and leaves the clip available for the word check."""
    heard, _ = SceneStep.objects.get_or_create(
        scene=step.scene,
        tool_call=step.tool_call,
        kind=SceneStep.Kind.TRANSCRIPT,
        made_from=clip,
        # This child runs inside the scene agent, never as a separately dispatched step.
        defaults={"status": SceneStep.Status.FINISHED, **SceneStep.snapshot(step.scene)},
    )
    if heard.status == SceneStep.Status.FAILED:
        return None
    try:
        transcript = _transcribe_clip(heard)
    except Exception as error:
        heard.status = SceneStep.Status.FAILED
        heard.reason = str(error)[:500]
        heard.finished_at = timezone.now()
        heard.save(update_fields=["status", "reason", "finished_at"])
        notices.post_notice(
            step.scene.job,
            notices.STEP_NOT_MADE,
            f"Scene {step.scene.number}'s clip could not be transcribed: {error}",
            Message.Level.PROBLEM,
            scene=step.scene.number,
        )
        return None
    heard.status = SceneStep.Status.FINISHED
    heard.finished_at = timezone.now()
    heard.save(update_fields=["status", "finished_at"])
    return transcript


# Why a fix resumed with the shop owner's answer ended, when an error stopped the agent.
STOPPED_AFTER_THE_ANSWER = (
    "The fixing agent stopped on an error after it was carried on, so its last try stays."
)


def _kept_when_stopped(step: SceneStep, first: ProducedItem | None) -> ProducedItem:
    """The clip a B-roll clip step keeps once an error stopped the fixing agent: its first
    clip, unless the fix was carried on after it was parked, with the shop owner's answer
    or after their pause. The scene had its last try in the ad while the fix waited, so it
    keeps it, as a give-up does, and the shop owner is shown every try."""
    if not _carried_on(step):
        assert first is not None, "a step that made its own clip keeps it"
        return _first_kept(step, first)
    AgentFix.objects.filter(
        step=step, finished_at__isnull=False, passed__isnull=True, gave_up=""
    ).update(gave_up=STOPPED_AFTER_THE_ANSWER)
    kept = _ended(step).kept
    assert kept is not None, "a give-up of a step of its own keeps its last try"
    return kept


def _carried_on(step: SceneStep) -> bool:
    """Whether `step`'s fix ended and was opened again since: for a photo or video the shop
    owner answered its question with, or after their pause."""
    return AgentFix.objects.filter(step=step, resumed_at__isnull=False).exists()


def agent_fix(step: SceneStep) -> AgentFix | None:
    """How the fixing agent ended with a B-roll clip step, a try passed or it gave up, or
    None when it never took it, hasn't ended yet, or an error stopped it."""
    return (
        AgentFix.objects.filter(step=step, finished_at__isnull=False)
        .filter(Q(passed__isnull=False) | ~Q(gave_up=""))
        .select_related("passed", "kept")
        .first()
    )


def tried_at(clip: ProducedItem) -> SceneStep | None:
    """The clip step a clip is a try of: the step the fixing agent fixed, for a try it
    filmed (each try is made in a step of its own), else the clip's own step."""
    found = AgentFixTry.objects.filter(clip=clip).select_related("agent_fix__step").first()
    return found.agent_fix.step if found is not None else clip.step


def agent_gave_up(step: SceneStep) -> bool:
    """Whether the fixing agent gave up on a B-roll clip step: the shop owner is then shown
    every try, to pick from."""
    ended = agent_fix(step)
    return ended is not None and not ended.passed_id


def _ended(step: SceneStep) -> AgentFix:
    """Keep how the fixing agent ended with `step`: a try passed, or it gave up, when the
    scene keeps its last try, all at once. A give-up kept already is left as it is, so it can
    be kept again whenever it may not have been."""
    with transaction.atomic():
        ended = AgentFix.objects.select_for_update().get(step=step)
        if ended.passed_id is not None or ended.said_to_camera or ended.kept_id is not None:
            return ended
        scene = Scene.objects.select_for_update().get(pk=step.scene_id)
        ended.kept = _as_the_last_try_filmed_it(scene, step)
        ended.save(update_fields=["said_to_camera", "kept", "camera_before"])
    return ended


def _as_the_last_try_filmed_it(scene: Scene, step: SceneStep) -> ProducedItem:
    """For a give-up that keeps the last try of `step`: the scene, put back by the give-up as
    it was before try 1, is as that try was filmed again, so its clip says the line and shows
    what the scene shows, as when a try passes. Gives the clip in the ad: the last try, or the
    first clip when no try was filmed. A clip made but never a try, as when the worker stopped
    while the judge watched it, is passed over, and the clip kept brought back on top of it."""
    landing = _clip_tries(step)
    if not landing:
        # A door 2 step films nothing itself: with no try filmed, its clip from before, the
        # one the shop owner noted, stays.
        noted = AgentFix.objects.get(step=step).first_clip
        assert noted is not None, "the fix started from the clip the shop owner noted"
        landing = [noted]
    kept = landing[-1]
    filmed = AgentFixTry.objects.filter(clip=kept).first()
    if filmed is not None:
        was = (scene.line, scene.shows, scene.result)
        # A give-up restores the try's creative row, while a concurrently committed
        # place/person change remains live. Explicit owner version swaps restore the
        # step snapshot too; an automatic give-up must not undo that outside change.
        for name, value in {"owner_words": [], **filmed.scene_row}.items():
            setattr(scene, name, value)
        # A try's new line, or what it shows, was fact checked before it was filmed.
        if (scene.line, scene.shows, scene.result) != was:
            scene.fact_checked = True
            scene.fact_problems = []
    scene.save()
    if not finish_with_clip(scene, kept) or _newest_clip(scene) == kept:
        return kept
    number = next(
        version.number for version in versions(scene, landing=landing) if version.clip == kept
    )
    return bring_back_clip(kept, back_to=number)


def _kept_a_try(step: SceneStep, ended: AgentFix) -> bool:
    """Whether a give-up on `step` kept one of its own clips in the ad, its last try or its
    first, as door 1 does, rather than the scene's clip from before a fix."""
    return ended.kept is not None and ended.kept.file in {clip.file for clip in _clip_tries(step)}


def _what_is_kept(step: SceneStep) -> str:
    """The clip of `step` a give-up that kept one of its own has in the ad, as named to the
    shop owner and the producer."""
    # A door 2 step films nothing itself: every clip of its own is a try.
    return "the last try" if step.fixes_a_note or len(_clip_tries(step)) > 1 else "the first clip"


# Why the fixing agent gave up on a scene whose step the worker stopped in the middle of.
STOPPED_BY_A_RESTART = "The worker restarted while the fixing agent worked on the scene."


def end_the_agents_fixing(step: SceneStep) -> None:
    """For a clip step failed as the worker restarted in the middle of it: end the fixing
    agent's work on it, if it took it, so no try the judge failed is in the ad. A fixing
    still going is given up, its scene put back as it was before try 1, as no worker carries
    it on. A give-up is then kept, if it wasn't yet: the scene keeps its last try. A scene with
    no try filmed is planned again, as the step's own clip fails with it, and a question its
    fix was parked on is withdrawn."""
    from agents.scene_agent import lifecycle

    fixing = AgentFix.objects.filter(step=step).first()
    if fixing is None:
        return
    if fixing.finished_at is None:
        lifecycle.give_up(fixing, STOPPED_BY_A_RESTART)
    if agent_fix(step) is None or fixing.passed_id is not None:
        return
    ended = _ended(step)
    # The step fails, so a clip of its own is no clip of the ad's: only a try, filmed in a step
    # of its own, is. With no try filmed, the scene waits for its clip to be made again, and a
    # question the fix was parked on is withdrawn: no answer can resume a fix whose step failed.
    # A step run again to carry its fix on had its own clip in the ad already: it stays there.
    if ended.kept is not None and ended.kept.step_id == step.pk and not carries_its_fix_on(step):
        Scene.objects.get(pk=step.scene_id).plan_again()
        OwnerQuestion.objects.filter(agent_fix=ended, answered_at__isnull=True).delete()


def carries_its_fix_on(step: SceneStep) -> bool:
    """Whether `step` was run again to carry on the fix parked on it, with the shop owner's
    answer or after their pause: it had finished, its clip in the ad, so ending it partway
    must leave that clip there."""
    from jobs import owner_answers  # It imports the watcher, which imports this module.

    return (
        owner_answers.resuming(step)
        or AgentFix.objects.filter(step=step, resumed_at__isnull=False).exists()
    )


def back_to_the_clip_before(step: SceneStep) -> ProducedItem | None:
    """For a door 2 step the fixing agent stopped on before any try was carried on: the
    scene's clip from before the step is its newest again, brought back for nothing when a try
    is newer. Gives the clip in the ad now."""
    scene = Scene.objects.get(pk=step.scene_id)
    before = step.shown_before(ProducedItem.Kind.CLIP)
    made = versions(scene)
    kept = made[-1].clip if made else None
    if before is not None and kept != before:
        back_to = next((version.number for version in made if version.clip == before), None)
        kept = bring_back_clip(before, back_to=back_to)
    return kept


def _first_kept(step: SceneStep, first: ProducedItem) -> ProducedItem:
    """The first clip of a B-roll clip step kept as the scene's clip once the fixing agent
    stopped: brought back on top of any try it made."""
    if _newest_clip(step.scene) == first:
        return first
    landing = _clip_tries(step)
    number = next(
        version.number for version in versions(step.scene, landing=landing) if version.clip == first
    )
    return bring_back_clip(first, back_to=number)


def _newest_clip(scene: Scene) -> ProducedItem | None:
    return scene.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").last()


def _shown_again_for(scene: Scene, clip: ProducedItem) -> None:
    """Undo the say to camera of a scene the fixing agent gave up on, still said to camera,
    as one of its tries, `clip`, is gone back to: the scene shows what it showed before, as
    the try filmed it, and the scene after it follows it again. Only a give-up from before
    2026-10-23 said its scene to camera."""
    step = tried_at(clip)
    ended = agent_fix(step) if step is not None else None
    if step is None or scene.shows or ended is None or not ended.said_to_camera:
        return
    before = ended.camera_before
    filmed = AgentFixTry.objects.filter(clip=clip).first()
    row = filmed.scene_row if filmed is not None else {}
    shown = {
        "shows": row.get("shows") or before["shows"],
        **{field: before[field] for field in BROLL_FIELDS},
        **{field: row[field] for field in ("broll_kind", "person_shown", "result") if field in row},
        "shortened_from": before["shortened_from"],
        "second_state": before["second_state"],
    }
    # A line changed since the give-up is newer than the try's: it stays, and the try's
    # clip, no longer saying it, is made again from its picture.
    if row.get("line") and scene.line == step.line:
        shown["line"] = row["line"]
    Scene.objects.filter(pk=scene.pk).update(**shown)
    if before["next_second_state"] is not None:
        scene.job.scenes.filter(number=scene.number + 1).update(
            second_state=before["next_second_state"]
        )
    scene.refresh_from_db()


def clip_tries(step: SceneStep) -> list[ProducedItem]:
    """A B-roll clip step's clips made, oldest first: its first, then each try."""
    return _clip_tries(step)


def _clip_tries(step: SceneStep) -> list[ProducedItem]:
    """A B-roll clip step's clips made so far, oldest first: its first, then each try the
    fixing agent filmed (each in a step of its own). A clip brought back as the one kept
    isn't another."""
    first = step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").first()
    filmed = AgentFixTry.objects.filter(agent_fix__step=step, clip__isnull=False).order_by("number")
    tries = [each.clip for each in filmed.select_related("clip") if each.clip]
    # A fix row's step (door 2) films nothing itself: its try 0 is the clip from before.
    return [first, *tries] if first is not None else tries


def _tries_shown(step: SceneStep) -> list[ProducedItem]:
    """The clips a tries notice under a B-roll clip step shows, oldest first: its clip tries,
    or, for a step from before the fixing agent, its first clip and the old auto-fix's
    remakes (#180)."""
    tries = _clip_tries(step)
    if AgentFix.objects.filter(step=step).exists():
        return tries
    return [*tries[:1], *_old_remakes(step)]


def try_name(clip: ProducedItem) -> str:
    """Which try of its step a clip is, as the shop owner is shown it: "first clip", or
    "try 1"."""
    return f"try {clip.remake}" if clip.remake else "first clip"


@dataclass(frozen=True)
class TryShown:
    """One try of a clip the judge failed every try of, as the shop owner is shown it."""

    clip: ProducedItem
    # "First clip", "Try 1".
    name: str
    # The label of the scene version it is.
    version: str
    # What the judge still sees wrong in it, in its words: its first answer's.
    problem: str


@dataclass(frozen=True)
class TriesShown:
    """Every try of a B-roll clip step the judge failed every try of, and the one in the ad
    now: the scene's current clip, when it is one of them, else None."""

    scene: int
    tries: list[TryShown]
    in_ad: ProducedItem | None
    # The try the fixing agent would pick, when it gave up and picked one.
    picked: ProducedItem | None = None
    # Why the fixing agent gave up, in its words.
    reason: str = ""
    # Whether a try can be swapped in: one is in the ad, or the scene still stands as the
    # agent's give-up left it.
    on_offer: bool = False


def tries_shown(step: SceneStep) -> TriesShown:
    """What the chat shows of a clip step the judge failed every try of, as it stands now."""
    made = versions(step.scene)
    labels = {version.clip.pk: version.label for version in made if version.clip is not None}
    tries = _tries_shown(step)
    current = made[-1].clip if made else None
    # A clip that no longer says the scene's line, changed since, isn't the ad's any more.
    scene = step.scene
    if current is not None and not says_the_line_as_it_stands(scene, current):
        current = None
    in_ad = next(
        (clip for clip in tries if current is not None and current.file == clip.file),
        None,
    )
    ended = agent_fix(step)
    return TriesShown(
        scene=step.scene.number,
        tries=[
            TryShown(
                clip=clip,
                name=try_name(clip).capitalize(),
                version=labels.get(clip.pk, ""),
                # The first only, SIDES' before MOTION's: one problem per try.
                problem=first_problem(_ruling(clip)),
            )
            for clip in tries
        ],
        in_ad=in_ad,
        picked=ended.picked_clip if ended is not None else None,
        reason=ended.gave_up if ended is not None else "",
        on_offer=in_ad is not None or _as_the_give_up_left_it(step),
    )


def first_problem(ruling: dict[str, Any]) -> str:
    """The one problem a clip the judge failed is shown with: the first it still fails on,
    SIDES' before MOTION's."""
    return next(iter(clip_judge.still_failing(ruling)), "")


def _as_the_give_up_left_it(step: SceneStep) -> bool:
    """Whether the scene of a clip step the fixing agent gave up on still stands as the
    give-up left it, so a try can be swapped in: said to camera with the line it had, or its
    clip from before still in the ad."""
    ended = agent_fix(step)
    if ended is None or ended.passed_id is not None:
        return False
    scene = step.scene
    if ended.said_to_camera:
        return not scene.shows and scene.line == step.line
    made = versions(scene)
    return (
        ended.kept is not None
        and bool(made)
        and made[-1].clip == ended.kept
        and says_the_line_as_it_stands(scene, ended.kept)
    )


def open_question(scene: Scene) -> OwnerQuestion | None:
    """The scene's open question for the shop owner: the one the fixing agent asked, with no
    answer handed in yet (spec #210 point 23). Its `question` is the agent's words, to show
    on the scene's card; its `agent_fix` the fix an answer goes to
    (`owner_answers.answer`)."""
    return (
        OwnerQuestion.objects.filter(scene=scene, answered_at__isnull=True, answer__isnull=True)
        .select_related("agent_fix", "tool_call")
        .order_by("-asked_at", "-id")
        .first()
    )


def say_the_agent_asked(question: OwnerQuestion) -> None:
    """Put the fixing agent's question to the shop owner once, in plain words, never as an
    error (round 7 spec section 4, Julian 2026-10-30): the fix keeps trying, and the ad never
    waits for the answer. The producer is told in a system note, so it never asks it again,
    and knows to hand a photo or video they answer with to the fix."""
    scene = question.scene
    number = scene.number
    post_notice(
        scene.job,
        notices.AGENT_ASKED,
        f"A question about scene {number}'s clip, while I keep working on it: "
        f"{question.question} If you have one, send it here. If not, there's no need to "
        "answer: the ad goes on without it.",
        Message.Level.INFO,
        owner_question=question.pk,
    )
    # The agents' models import this module, so they are imported where they are used.
    from agents.models import SystemNote

    SystemNote.objects.create(
        session=question.agent_fix.step.tool_call.session,
        kind=SystemNote.Kind.AGENT_ASKED,
        text=f"The fixing agent working on scene {number}'s clip asked the shop owner in the "
        f'chat, and keeps filming its tries meanwhile: "{question.question}" Don\'t ask it '
        f"again, and don't wait for the answer. If they answer with a photo or a video, call "
        f"pass_note for scene {number} with its instruction and checked owner quotes.",
    )


def scene_way_out(scene: Scene) -> str:
    """The approved escape from a failed scene, including retained historical ads."""
    if scene.kind == Scene.Kind.TALKING:
        return "You can send a photo, or give us a different line."
    # Said to camera only where the whole-plan rules allow it (jobs.plan_rules): never the
    # ad's only B-roll scene, and a middle scene only while she uses it.
    can_talk = broll_can_talk(
        [each.plan_scene() for each in scene.job.scenes.order_by("number")], scene.number
    )
    if can_talk is None:
        return "You can send a photo."
    if can_talk == DOING_IT:
        return presenter_words(scene.job.person_gender).render(
            "We can make it a scene where she uses it, or shows its result or a dab on her "
            "fingertip, and talks about it, or you can send a photo."
        )
    return presenter_words(scene.job.person_gender).render(
        "We can make it a scene where she holds it and talks about it, or you can send a photo."
    )


def say_the_agent_gave_up(step: SceneStep) -> None:
    """Show the shop owner every try of a B-roll clip the fixing agent gave up on, to pick
    from, by code, never silent: the judge failed each, and the scene keeps its last try, or
    the clip it had before (or, for a give-up from before 2026-10-23, is said to camera). The
    notice ends the step in the chat: nothing else shows for it. A fix parked on a question
    for the shop owner was said when it was asked (`say_the_agent_asked`)."""
    ended = agent_fix(step)
    assert ended is not None and ended.passed_id is None, "the agent gave up on the step"
    number = step.scene.number
    if ended.paused_at is not None:
        _say_the_fix_paused(step, ended)
        return
    capped = ", the most I may make," if ended.ended_by_cap else ""
    tried = len(_clip_tries(step))
    made = " and made your change" if step.fixes_a_note else ""
    found = f"None of the {tried} tries at scene {number}'s clip{capped} passed my check{made}"
    post_notice(
        step.scene.job,
        notices.TRIES_TO_PICK,
        f"{found}, so {_in_the_ad_meanwhile(step, ended)}. Watch them, and pick one on scene "
        f"{number}'s card to use it instead. " + (scene_way_out(step.scene)),
        Message.Level.INFO,
        step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE),
        tries_of=step.pk,
    )


def _in_the_ad_meanwhile(step: SceneStep, ended: AgentFix) -> str:
    """What a give-up, or a fix parked on a question, left in the ad for the scene, as a
    clause for the chat: "the last try is in the ad", or the like."""
    number = step.scene.number
    if ended.kept is not None and not says_the_line_as_it_stands(step.scene, ended.kept):
        return f"scene {number} needs filming again; its old clip stays a version"
    if ended.said_to_camera:
        return f"scene {number}'s line will be said to camera instead"
    if _kept_a_try(step, ended):
        return f"{_what_is_kept(step)} is in the ad"
    if ended.kept is not None:
        return f"scene {number} keeps the clip it had before"
    return "the last try is in the ad"


def _say_the_fix_paused(step: SceneStep, ended: AgentFix) -> None:
    """The line for a fix the shop owner's pause parked between tries (the wording is
    Claude's, not approved yet): what is in the ad while it waits."""
    number = step.scene.number
    post_notice(
        step.scene.job,
        notices.REPLY,
        f"Scene {number}'s fix stopped between tries for your pause, so "
        f"{_in_the_ad_meanwhile(step, ended)} meanwhile.",
        Message.Level.INFO,
        step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE),
    )


# What swapping in a try is told when its tries aren't on offer any more: the scene has a
# newer clip, or its line changed since.
TRIES_NOT_ON_OFFER = "Those tries aren't on offer any more."
TRY_IN_THE_AD = "That try is already in the ad."


class TryNotOnOffer(Exception):
    """Swapping in a try that can't be swapped in, saying why."""


def _try_to_use(clip: ProducedItem) -> int:
    """The number of the scene version a try of a clip the judge failed every try of is, to
    swap it in for nothing: it must be one of the tries the chat showed, those tries still
    hold the clip in the ad, or the scene stands as the fixing agent's give-up left it, and
    it isn't the one in the ad. Refuses, saying why, otherwise, and while the scene's picture
    or clip is being made."""
    step = tried_at(clip)
    scene = clip.scene
    if (
        step is None
        or scene is None
        or step.kind != SceneStep.Kind.CLIP
        or not step.tries_shown.exists()
        or clip not in _tries_shown(step)
    ):
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    shown = tries_shown(step)
    if shown.in_ad == clip:
        raise TryNotOnOffer(TRY_IN_THE_AD)
    if scene.steps.filter(
        kind__in=[
            SceneStep.Kind.STARTING_PICTURE,
            SceneStep.Kind.CLIP,
            SceneStep.Kind.SCENE_AGENT,
        ],
        status=SceneStep.Status.RUNNING,
    ).exists():
        raise TryNotOnOffer(
            f"Scene {scene.number}'s picture or clip is being made right now. Tap again once "
            "it's ready."
        )
    number = next(version.number for version in versions(scene) if version.clip == clip)
    if not go_back_is_free(scene, number):
        version = versions(scene)[number - 1]
        raise TryNotOnOffer(version_refilm_offer(scene, version))
    return number


def is_a_try_on_offer(clip: ProducedItem) -> bool:
    """Whether `clip` is a try of a clip the judge failed every try of that can be swapped in
    now."""
    try:
        _try_to_use(clip)
    except TryNotOnOffer:
        return False
    return True


def use_try(clip: ProducedItem, *, show: bool = True) -> GoneBack:
    """Swap a try of a clip the judge failed every try of into the ad, as the shop owner
    tapped: its version is gone back to, for nothing, with nothing made, judged or paid for,
    and the chat is shown it, a scene said to camera when the fixing agent gave up showing
    what the try was made for again. The tap is their yes. Refuses, saying why, unless
    `is_a_try_on_offer`.
    Not to `show` it leaves the chat as it was, as go_back."""
    assert clip.scene is not None, "a try is a scene's clip"
    return go_back(clip.scene, _try_to_use(clip), show=show)


def kept_before_it_failed(scene: Scene) -> ProducedItem | None:
    """The clip the scene's newest clip step kept before it failed, as when the worker stopped
    while the judge looked at it or an error came after (spec #247 point 8): the scene isn't
    finished with it, but it can be kept rather than made again. None when that step didn't
    fail, kept no clip, or the fixing agent took it (its tries are picked from instead)."""
    step = scene.steps.filter(kind=SceneStep.Kind.CLIP).order_by("id").last()
    if (
        step is None
        or step.status != SceneStep.Status.FAILED
        or AgentFix.objects.filter(step=step).exists()
    ):
        return None
    return step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").first()


def keep_the_clip_before_it_failed(clip: ProducedItem) -> None:
    """Keep the clip a failed clip step made (`kept_before_it_failed`) as the scene's clip: its
    step counts as finished, so the clip is a version of the scene like any other, and the
    scene is finished; the chat is shown it. Nothing is made, judged or paid for. Raises
    SceneBusy while the scene's picture or clip is being made, and NoSuchVersion when the
    scene changed since the clip was made for it."""
    from . import stale  # It reads scenes as this module keeps them.

    step = clip.step
    scene = clip.scene
    assert step is not None and scene is not None, "a kept clip is a scene step's"
    with transaction.atomic():
        Scene.objects.select_for_update().get(pk=scene.pk)
        if scene.steps.filter(
            kind__in=[
                SceneStep.Kind.STARTING_PICTURE,
                SceneStep.Kind.CLIP,
                SceneStep.Kind.SCENE_AGENT,
            ],
            status=SceneStep.Status.RUNNING,
        ).exists():
            raise SceneBusy(f"scene {scene.number}'s picture or clip is being made")
        picture = scene.current_picture()
        if (
            stale.is_stale(step)
            or stale.current_audio(scene) != clip.made_from
            or (picture is not None and (clip.picture is None or clip.picture.step != picture.step))
        ):
            raise NoSuchVersion(
                f"scene {scene.number} has changed since that clip was made for it, so it no "
                "longer fits the scene"
            )
        SceneStep.objects.filter(pk=step.pk).update(status=SceneStep.Status.FINISHED)
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
        if scene.job.session is not None:
            messages.add(
                scene.job.session,
                role=Message.Role.AGENT,
                carrying=[
                    messages.AttachedFile(
                        Attachment.Kind.VIDEO,
                        clip.file,
                        scene.number,
                        version_label(clip),
                        clip=clip,
                    )
                ],
            )


def what_the_judge_did(step: SceneStep, clip: ProducedItem) -> str:
    """What the clip judge and the fixing agent did before `clip` was kept as a B-roll clip
    step's clip, as a sentence to follow another, for the producer to tell the shop owner:
    nothing when the judge passed its first clip or didn't judge it. A first clip the judge
    couldn't check is kept as it is, and the shop owner is told (Julian, 2026-10-14: "treat
    that as pass"); so is one it couldn't tell is right, for the shop owner to decide (#228)."""
    first = _clip_tries(step)[0]
    verdict = (first.ruling or {}).get("verdict")
    if verdict == "not_checked":
        return " The clip judge couldn't check this clip, so it is kept unchecked."
    if verdict == "unsure":
        # The shop owner was told in the chat what the references never show (#228).
        return (
            " The clip judge couldn't tell whether this clip is right, so it is kept for the "
            "shop owner to check."
        )
    if verdict != "fail":
        return ""
    faults = " ".join(dict.fromkeys(clip_judge.still_failing(_ruling(first))))
    failed = f' The clip judge failed the first clip it made ("{faults}")'
    ended = agent_fix(step)
    if ended is None:
        return f"{failed}, and it couldn't be made again, so it is kept as it is."
    if ended.passed is not None:
        passed = ended.passed.remake
        from jobs import owner_answers  # It imports the watcher, which imports this module.

        answered = (
            " once the shop owner answered its question," if owner_answers.answered(step) else ""
        )
        return (
            f"{failed}, so the fixing agent filmed it again, and{answered} the judge passed try "
            f"{passed}.{_assemble_again(step)}"
        )
    filmed = len(_clip_tries(step)) - 1
    tried = {0: "before filming a try", 1: "after 1 try"}.get(filmed, f"after {filmed} tries")
    if ended.paused_at is not None:
        return (
            f"{failed}, and {tried} the fixing agent's fix stopped for the shop owner's pause, "
            f"so {_what_is_kept(step)} is in the ad meanwhile. {_carry_it_on(step)}"
        )
    gave_up = (
        f"{failed}, and the fixing agent filmed as many tries as it may ({filmed}), none passing"
        if ended.ended_by_cap
        else f"{failed}, and the fixing agent gave up after {filmed} tries"
    )
    if _kept_a_try(step, ended):
        return (
            f"{gave_up}, so {_what_is_kept(step)} is in the ad. Scene {step.scene.number}'s card "
            "shows the shop owner every try, so they can put one in the ad from it, for "
            f"nothing.{_assemble_again(step)}"
        )
    return f"{gave_up}.{_assemble_again(step)}"


def _assemble_again(step: SceneStep) -> str:
    """For a judge-fail fix resumed with the shop owner's answer: the producer assembles the
    ad again to show the scene's new clip (code never does)."""
    from jobs import owner_answers  # It imports the watcher, which imports this module.

    return _ad_behind(step) if owner_answers.answered(step) else ""


def _ad_behind(step: SceneStep) -> str:
    """When the scene's clip changed since the ad was last assembled, a sentence telling the
    producer to assemble it again; else nothing."""
    ad = latest(step.scene.job, ProducedItem.Kind.FINISHED_AD)
    newest = _newest_clip(step.scene)
    if ad is None or newest is None:
        return ""
    shown = {cut["scene"]: cut["clip"] for cut in ad.cuts}.get(step.scene.number)
    if shown is not None and ProducedItem.objects.filter(pk=shown, file=newest.file).exists():
        return ""
    return " The scene's clip changed since the ad was shown: assemble the ad again to show it."


def _carry_it_on(step: SceneStep) -> str:
    """What the producer is told to do with a fix the shop owner's pause parked."""
    return (
        f"After the shop owner's message, call pass_note for scene {step.scene.number} to carry "
        "it on, or leave it."
    )


def what_the_fix_did(step: SceneStep, clip: ProducedItem) -> str:
    """How the producer's fix (fix_scene, door 2) of `step`'s scene ended, as the producer is
    told it with the step's result: the try that passed; parked on the shop owner's pause; or
    no try passing, the last try in the ad (Julian's Q19).
    `clip` is the clip the step kept in the ad."""
    number = step.scene.number
    ended = AgentFix.objects.select_related("passed").get(step=step)
    filmed = len(_clip_tries(step))
    tries = "1 try" if filmed == 1 else f"{filmed} tries"
    if ended.passed is not None:
        return (
            f"Scene {number}'s fix passed: the fixing agent filmed {tries}, and try "
            f"{ended.passed.remake} passed the clip judge with the change made. It is scene "
            f"{number}'s clip now (version {clip.version}). Look at it if you need to, then "
            "assemble the ad to show it."
        )
    meanwhile = _in_the_ad_meanwhile(step, ended)
    after = "before filming a try" if not filmed else f"after {tries}"
    if ended.paused_at is not None:
        return (
            f"Scene {number}'s fix stopped {after} for the shop owner's pause, so {meanwhile} "
            f"meanwhile. {_carry_it_on(step)}"
        )
    capped = " and the fixing agent filmed as many tries as it may" if ended.ended_by_cap else ""
    return (
        f"Scene {number}'s fix ended with no try passing ({tries}){capped}: "
        f'"{ended.gave_up}" So {meanwhile}, and the chat showed the shop owner every try to tap '
        f"one instead, for nothing.{_ad_behind(step)}"
    )


def _ruling(clip: ProducedItem) -> dict[str, Any]:
    """The clip judge's ruling on `clip`, judged."""
    ruling: dict[str, Any] | None = clip.ruling
    assert ruling is not None, "the clip was judged"
    return ruling


def _judge_clip(step: SceneStep, clip: ProducedItem) -> None:
    """Have the clip judge rule on a B-roll clip against its sent list, keeping its ruling on
    the clip."""
    clip_judge.judge_clip(step, clip)


def _make_clip(
    step: SceneStep, *, remake: int = 0, motion: str = "", sent: SentList | None = None
) -> ProducedItem:
    """Make a talking clip or the scene agent's explicit starting-picture clip.

    The submitted and collected model calls are checkpoints, so restarting collects
    the same clip without paying for another render. Historical sent lists are read
    by cards and the look; they cannot be submitted as a new clip.
    """
    scene = step.scene
    job = scene.job
    picture, audio = step.picture, step.made_from
    if sent is None:
        raise ClipFailed("An H3 clip must be made by its scene agent from a starting picture.")
    handoff = sent_handoff(step, sent)
    making, collecting = _clip_purposes(step)

    def tell_its_slow() -> None:
        messages.add(
            step.tool_call.session,
            role=Message.Role.AGENT,
            text=(
                f"Scene {scene.number}'s clip is taking longer than usual. It's still being "
                "made, and I'll tell you when it's ready."
            ),
        )

    # A clip fetched before the worker stopped is kept, whichever video model made it.
    # The step's first clip is the first fetched for it, and each remake the next.
    fetched = _paid_calls(job, collecting, charged_to=step.tool_call).order_by("id")[remake:]
    if fetched and fetched[0].output is not None:
        file = fetched[0].output["file"]
    else:
        video_id = _clip_asked_for(step, handoff, making, collecting, remake=remake)
        if video_id is None:
            video_id = _ask_for_clip(job, making, handoff)
        file = collect_clip(job=job, purpose=collecting, video_id=video_id, when_slow=tell_its_slow)
    # H3's own sound and full picture are kept. Earlier B-roll clips instead receive their
    # separate line audio; earlier talking clips already speak that audio.
    with tempfile.TemporaryDirectory() as folder:
        made = Path(folder) / "clip.mp4"
        made.write_bytes(file_store.read(file))
        seconds = assembly.seconds_of(made)
    with transaction.atomic():
        clip = ProducedItem.objects.create(
            job=job,
            scene=scene,
            step=step,
            kind=ProducedItem.Kind.CLIP,
            version=_next_version(scene, ProducedItem.Kind.CLIP),
            file=file,
            seconds=seconds,
            made_from=audio,
            picture=picture,
            remake=remake or None,
            motion_prompt=motion,
            # The change it was made for, as its version's label: the producer's note.
            change=step.note,
        )
        if sent is not None:
            # Kept with the clip, before it is shown anywhere, so its card shows what it was
            # sent.
            library.keep_sent_list(clip, sent)
    return clip


def sent_handoff(step: SceneStep, sent: library.SentList) -> BrollClipHandoff:
    """Send the scene agent's picture and optional page-video piece. H3's length comes
    from the written line."""
    if sent.mode not in {"start_frame", "reference"} or sent.starting_picture is None:
        raise ClipFailed("A B-roll clip needs its scene agent's starting picture and filming mode.")
    seconds = h3_clip_seconds(len(step.line.split()))
    videos = dict(step.scene.job.videos.values_list("position", "file"))
    if sent.mode == "start_frame":
        return BrollClipHandoff(
            starting_picture=sent.starting_picture.file,
            end_picture=sent.end_picture.file if sent.end_picture else None,
            seconds=seconds,
            prompt=sent.prompt,
            prompt_enhancement=False,
        )
    return BrollClipHandoff(
        example_pictures=[
            sent.starting_picture.file,
            *([sent.end_picture.file] if sent.end_picture else []),
        ],
        reference_videos=[
            ReferenceVideo(
                file=piece.file,
                video=videos[piece.video],
                start=piece.start,
                end=piece.end,
                trim=piece.trim,
            )
            for piece in sent.pieces
        ],
        seconds=seconds,
        prompt=sent.prompt,
        prompt_enhancement=False,
    )


def bring_back_clip(
    clip: ProducedItem, *, picture: ProducedItem | None = None, back_to: int | None = None
) -> ProducedItem:
    """Make an earlier clip the scene's clip again, as its next version: the same file, step,
    picture, audio, movement, judge's ruling and change, with nothing made or paid for again (#132
    review, M1). The scene is finished, as it was when the clip was made. Going back to
    scene version `back_to` gives it the `picture` brought back with it, and says so."""
    scene = clip.scene
    assert scene is not None, "a clip belongs to a scene"
    with transaction.atomic():
        again = ProducedItem.objects.create(
            job=clip.job,
            scene=scene,
            step=clip.step,
            kind=ProducedItem.Kind.CLIP,
            version=_next_version(scene, ProducedItem.Kind.CLIP),
            file=clip.file,
            seconds=clip.seconds,
            made_from=clip.made_from,
            picture=picture or clip.picture,
            ruling=clip.ruling,
            motion_prompt=clip.motion_prompt,
            change=_gone_back(back_to) or clip.change,
            back_to=back_to,
            # The same file, so what it was sent.
            sent=clip.sent,
        )
        # H3 transcripts belong to the clip row. Restoring its identical file must
        # retain those words too; the original step keeps their paid provenance.
        transcript = (
            clip.job.produced.filter(
                kind=ProducedItem.Kind.TRANSCRIPT,
                made_from=clip,
                step__status=SceneStep.Status.FINISHED,
            )
            .order_by("version")
            .last()
        )
        if transcript is not None:
            ProducedItem.objects.create(
                job=clip.job,
                scene=scene,
                step=transcript.step,
                kind=ProducedItem.Kind.TRANSCRIPT,
                version=_next_version(scene, ProducedItem.Kind.TRANSCRIPT),
                text=transcript.text,
                words=transcript.words,
                made_from=again,
            )
        finish_with_clip(scene, again)
    return again


def bring_back_picture(
    picture: ProducedItem, *, back_to: int | None = None
) -> tuple[ProducedItem, bool]:
    """Make an earlier starting picture the scene's starting picture again, as its next
    version: the same file, step and change, so the same photo and prompts, with nothing made,
    judged or paid for again (#143, as `bring_back_clip`). Like any new picture, it plans a
    finished scene again: its clip shows the picture before it (#133). Going back to scene
    version `back_to` says so. Gives the picture brought back, and whether its scene was
    finished."""
    scene = picture.scene
    assert scene is not None, "a starting picture belongs to a scene"
    with transaction.atomic():
        again = ProducedItem.objects.create(
            job=picture.job,
            scene=scene,
            step=picture.step,
            kind=ProducedItem.Kind.STARTING_PICTURE,
            version=_next_version(scene, ProducedItem.Kind.STARTING_PICTURE),
            file=picture.file,
            text=picture.text,
            ruling=picture.ruling,
            sent=picture.sent,
            change=_gone_back(back_to) or picture.change,
            back_to=back_to,
        )
        return again, scene.plan_again()


def _gone_back(back_to: int | None) -> str:
    """The change going back to scene version `back_to` is, as its label: "back to v2"."""
    return f"back to v{back_to}" if back_to is not None else ""


# --- A scene's versions, and going back to one (fix redesign, decisions 8 and 17) ----------


@dataclass(frozen=True)
class Version:
    """One version of a scene, as the shop owner was shown it: a starting picture and the clip
    made from it, numbered from 1 in the order shown. A new picture starts one, its clip to
    come; a new clip of the same picture is one of its own, and so is each try the fixing agent
    filmed of a clip the judge failed. The line lives on its own: a version keeps the line
    its clip says, or its picture was made for (Julian 2026-10-16 22:39, "the picture is
    a picture and the line is a line")."""

    number: int
    # The change it was made for, in the shop owner's words; blank for the first making.
    change: str
    # None for a B-roll scene made from example pictures, which has none.
    picture: ProducedItem | None
    # None until its clip is made.
    clip: ProducedItem | None
    # For a version brought back by going back, the version gone back to.
    back_to: int | None
    # For a clip of a step whose first clip was filmed again, which try it is: "try 1".
    tried: str = ""

    @property
    def label(self) -> str:
        """How the shop owner and the agent deciding fixes see it named: "v1 first version",
        then the change it was made for, as "v2 black toilet" (decision 17), and which try it
        is of a clip filmed again, as "v3 black toilet (try 1)"."""
        tried = f" ({self.tried})" if self.tried and self.tried != self.change else ""
        return f"v{self.number} {_short(self.change) or 'first version'}{tried}"

    @property
    def line(self) -> str:
        """The line its clip says, or its picture was made for while it has no clip."""
        made = self.clip or self.picture
        return made.step.line if made is not None and made.step is not None else ""


# The longest change a version's label names in full: a longer one, such as a producer's
# note of several sentences, is named by its first sentence, or cut at a word.
LONGEST_LABEL = 60


def _short(change: str) -> str:
    """`change` as a version's label names it: whole up to LONGEST_LABEL characters, else its
    first sentence when that fits, else cut at the last word that fits, with an ellipsis."""
    if len(change) <= LONGEST_LABEL:
        return change
    first = change.split(". ", 1)[0].rstrip(".")
    if len(first) <= LONGEST_LABEL:
        return first
    return change[: LONGEST_LABEL - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def versions(scene: Scene, *, landing: Sequence[ProducedItem] = ()) -> list[Version]:
    """The scene's versions, oldest first: each starting picture it was shown and each clip
    kept, in the order made, a clip joining the version of its picture until that has one. A
    step still running has shown nothing yet, but `landing`, what a step is about to show."""
    shown = [
        made
        for made in scene.produced.filter(
            kind__in=[ProducedItem.Kind.STARTING_PICTURE, ProducedItem.Kind.CLIP]
        )
        .select_related("step", "picture")
        .order_by("created_at", "id")
        if made in landing or _was_shown(made)
    ]
    found: list[Version] = []
    for made in shown:
        last = found[-1] if found else None
        tried = _tried(made)
        if made.kind == ProducedItem.Kind.STARTING_PICTURE:
            found.append(Version(len(found) + 1, made.change, made, None, made.back_to))
        elif (
            last is not None
            and last.clip is None
            and last.picture is not None
            and made.picture is not None
            and made.picture.file == last.picture.file
        ):
            # A clip brought back with an earlier picture is still of the picture shown.
            found[-1] = Version(last.number, last.change, last.picture, made, last.back_to, tried)
        else:
            found.append(
                Version(len(found) + 1, made.change, made.picture, made, made.back_to, tried)
            )
    return found


def _tried(made: ProducedItem) -> str:
    """Which try a clip is, when its step's first clip was filmed again: blank otherwise."""
    step = tried_at(made) if made.kind == ProducedItem.Kind.CLIP else made.step
    if made.kind != ProducedItem.Kind.CLIP or step is None or step.kind != SceneStep.Kind.CLIP:
        return ""
    tries = _clip_tries(step)
    if len(tries) == 1 or made not in tries:
        return ""
    return try_name(made)


def motion_of(clip: ProducedItem) -> str:
    """How a B-roll clip was asked to move: its own prompt for a remade clip, else its step's
    for a clip made from a note, else as its picture's step planned."""
    step = clip.step
    planned = step.picture_step if step is not None else None
    return (
        clip.motion_prompt
        or (step.motion_prompt if step is not None else "")
        or (planned.motion_prompt if planned is not None else "")
    )


def _was_shown(made: ProducedItem) -> bool:
    """Whether the shop owner was shown a scene's starting picture or clip: one its step
    finished with. Every clip a clip step made is, its first and each try the fixing agent
    filmed of it, and any brought back. Of a step's clip the old auto-fix remade (a job
    made before the fixing agent), only the one kept was, unless the chat showed every one
    to pick from (#180)."""
    step = made.step
    if step is None:
        return True
    if made.kind == ProducedItem.Kind.STARTING_PICTURE and step.kind == SceneStep.Kind.SCENE_AGENT:
        from . import scene_pictures

        return scene_pictures.visible(made)
    if step.status != SceneStep.Status.FINISHED:
        return False
    remakes = _old_remakes(step) if made.kind == ProducedItem.Kind.CLIP else []
    if not remakes:
        return True
    tries = [step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id")[0], *remakes]
    if made not in tries or _old_tries_were_shown(step, tries):
        # Brought back, or shown to pick from.
        return True
    failed = [auto_fix.failed_answers(clip.ruling or {"verdict": "pass"}) for clip in tries]
    return made == tries[-1] and auto_fix.clip_to_keep(failed) == len(tries) - 1


def _old_remakes(step: SceneStep) -> list[ProducedItem]:
    """The remakes the old auto-fix (#155) made of a clip step's clip the judge failed, kept
    in the step itself, oldest first: none for a step made since, whose tries the fixing
    agent films in steps of their own. A try's own step holds only its try, numbered as a
    remake but no remake of a first clip in its step: it has none."""
    clips = step.produced.filter(kind=ProducedItem.Kind.CLIP)
    first = clips.order_by("id").first()
    if first is None or first.remake is not None:
        return []
    return list(clips.filter(remake__isnull=False).order_by("remake"))


def _old_tries_were_shown(step: SceneStep, tries: list[ProducedItem]) -> bool:
    """Whether the chat showed the shop owner every one of an old auto-fix's clips, the judge
    failing each, to pick from (#180)."""
    every_one_failed = all((clip.ruling or {}).get("verdict") == "fail" for clip in tries)
    return every_one_failed and step.tries_shown.exists()


def version_label(made: ProducedItem) -> str:
    """The label of the scene version a starting picture or clip about to be shown is in."""
    assert made.scene is not None, "a starting picture or clip belongs to a scene"
    landing = [made]
    step = tried_at(made) if made.kind == ProducedItem.Kind.CLIP else made.step
    if (
        made.kind == ProducedItem.Kind.CLIP
        and step is not None
        and step.kind == SceneStep.Kind.CLIP
    ):
        # Every try lands with the one kept, each its own version.
        landing = [*_clip_tries(step), made]
    return next(
        (
            version.label
            for version in reversed(versions(made.scene, landing=landing))
            if made in (version.picture, version.clip)
        ),
        "",
    )


class NoSuchVersion(Exception):
    """Going back to a version the scene doesn't have."""


class NothingToBringBack(NoSuchVersion):
    """Going back to a version with no starting picture (a B-roll scene made from example
    pictures) after the scene's line changed: its clip no longer says the line, and there is
    no picture to make a new one from."""


class SceneBusy(Exception):
    """Going back while the scene's starting picture or clip is being made, which would land
    on top of what was brought back."""


@dataclass(frozen=True)
class GoneBack:
    # The new top version: the picture brought back, and the clip too unless it is to make.
    version: Version
    # Whether its clip must be made from its picture, with the scene's line as it stands:
    # paid, through the clip step and its judge as any clip. The scene is planned until then.
    clip_to_make: bool


def go_back(scene: Scene, number: int, *, show: bool = True) -> GoneBack:
    """Restore the version's picture, clip and filmed scene row together.

    Nothing is made or checked again when the voice and presenter still match. A version
    with an old voice or presenter restores its row and picture for an agreed paid refilm;
    callers must offer its cost and obtain consent first. Card swaps omit the duplicate
    attachments but still post the restoration notice. Refuses while the scene is busy.
    """
    with transaction.atomic():
        Scene.objects.select_for_update().get(pk=scene.pk)
        # Whether the clip still says the line is read from the scene as it now stands.
        scene.refresh_from_db()
        if scene.steps.filter(
            kind__in=[
                SceneStep.Kind.STARTING_PICTURE,
                SceneStep.Kind.CLIP,
                SceneStep.Kind.SCENE_AGENT,
            ],
            status=SceneStep.Status.RUNNING,
        ).exists():
            raise SceneBusy(f"scene {scene.number}'s picture or clip is still being made")
        return _bring_back(scene, number, show=show)


def _bring_back(scene: Scene, number: int, *, show: bool) -> GoneBack:
    """go_back's work, the scene locked and read as it stands, in its transaction."""
    with transaction.atomic():
        shown = versions(scene)
        if not 1 <= number <= len(shown):
            raise NoSuchVersion(
                f"scene {scene.number} has versions 1 to {len(shown)}, not {number}"
            )
        earlier = shown[number - 1]
        before = (scene.line, scene.shows)
        if earlier.clip is not None:
            _shown_again_for(scene, earlier.clip)
        free = earlier.clip is not None and not version_refilm_reason(scene, earlier.clip)
        for field, value in version_scene_row(earlier).items():
            setattr(scene, field, value)
        # This exact row and clip were checked together when filmed.
        if earlier.clip is not None:
            scene.fact_checked = True
            scene.fact_problems = []
        scene.save()
        changed = before != (scene.line, scene.shows)
        # A scene showing the product has no starting picture: its clip is made again from
        # its sent list (spec #210 point 19).
        if earlier.picture is None and not free and not scene.shows:
            raise NothingToBringBack(
                f"scene {scene.number}'s version {number} has no starting picture, and its "
                "clip no longer says the scene's line"
            )
        _use_the_photos_of(scene, earlier)
        picture = (
            bring_back_picture(earlier.picture, back_to=number)[0]
            if earlier.picture is not None
            else None
        )
        if earlier.clip is not None and free:
            clip = bring_back_clip(earlier.clip, picture=picture, back_to=number)
        else:
            clip = None
            scene.plan_again()
        # The bring-backs set the status through their own copy of the scene.
        scene.refresh_from_db(fields=["status"])
        top = versions(scene)[-1]
        if show and scene.job.session is not None:
            messages.add(
                scene.job.session,
                role=Message.Role.AGENT,
                text=restored_version_message(scene, number, changed=changed) if free else "",
                carrying=[
                    messages.AttachedFile(
                        kind,
                        made.file,
                        scene.number,
                        top.label,
                        clip=made if kind == Attachment.Kind.VIDEO else None,
                    )
                    for kind, made in (
                        (Attachment.Kind.PICTURE, picture),
                        (Attachment.Kind.VIDEO, clip),
                    )
                    if made is not None
                ],
            )
        if not show and free and scene.job.session is not None:
            post_notice(
                scene.job,
                notices.REPLY,
                restored_version_message(scene, number, changed=changed),
                Message.Level.INFO,
            )
        return GoneBack(top, clip_to_make=not free)


def version_scene_row(version: Version) -> dict[str, Any]:
    """The row this version was filmed for; old clips keep their step snapshot."""
    made = version.clip or version.picture
    if made is None or made.step is None:
        return {}
    step = made.step
    row = {
        "line": step.line,
        "owner_words": [],
        "shows": step.shows,
        **{field: getattr(step, snapshot) for field, snapshot in STEP_BROLL_FIELDS.items()},
        **{field: getattr(step, field) for field in CLIP_ONLY_INPUTS},
    }
    # Copies keep the original file and step, but have no AgentFixTry of their own.
    filmed = AgentFixTry.objects.filter(clip__file=made.file, clip__scene=made.scene).first()
    if filmed is not None:
        row.update(filmed.scene_row)
    # Before whole-version restores, a try kept the talking style but not the explicit
    # kind. Older talking steps instead left their action or B-roll labels blank.
    if "kind" not in row:
        talking = bool(row.get("talking_style")) or not row["shows"] or not row["person_shown"]
        row["kind"] = Scene.Kind.TALKING if talking else Scene.Kind.BROLL
    row.setdefault("kind_why", "")
    return row


def version_refilm_reason(scene: Scene, clip: ProducedItem) -> str:
    """Only a changed voice or presenter prevents reusing an already filmed version."""
    if not clip_voice_is_current(scene, clip):
        return "made with the old voice"
    expected = copy(scene)
    for name, value in version_scene_row(Version(0, "", clip.picture, clip, None)).items():
        setattr(expected, name, value)
    if clip.step is not None and clip.step.portrait_id is not None:
        portrait = latest(scene.job, ProducedItem.Kind.PORTRAIT)
        if clip.step.portrait_id != (portrait.pk if portrait is not None else None):
            return "made with the old presenter"
    if not clip_presenter_is_current(expected, clip):
        return "made with the old presenter"
    return ""


def version_refilm_offer(scene: Scene, version: Version) -> str:
    """A plain cost offer, before restoring anything that needs paid filming."""
    from agents.money import dollars

    assert version.clip is not None
    reason = version_refilm_reason(scene, version.clip)
    seconds = version.clip.seconds or h3_clip_seconds(len(version.line.split()))
    cost = dollars(catalog.PRICE_PER_SECOND_OF_VIDEO["creatify/boreal-h3"] * Decimal(str(seconds)))
    return (
        f"v{version.number} was {reason}. Filming it again costs about {cost} for one try; "
        "pictures, checks and retries may add to it. Would you like to refilm it?"
    )


def restored_version_message(scene: Scene, number: int, *, changed: bool) -> str:
    text = f"Scene {scene.number}'s v{number} is back, free."
    if changed:
        shows = (scene.shows or "the presenter talking to camera").rstrip(".!?")
        text += f" Says: “{scene.line}” Shows: {shows}."
    return text


def go_back_is_free(scene: Scene, number: int) -> bool:
    """Whether the whole version can return with its current voice and presenter."""
    made = versions(scene)
    if not 1 <= number <= len(made):
        raise NoSuchVersion(f"scene {scene.number} has versions 1 to {len(made)}, not {number}")
    clip = made[number - 1].clip
    return clip is not None and not version_refilm_reason(scene, clip)


def clip_voice_is_current(scene: Scene, clip: ProducedItem) -> bool:
    """Use H3's voice snapshot, or the approved-change checkpoint for older clips.

    Historical talking clips have no sent list. Keep them that way: writing a partial
    sent list would make old cards unreadable. A committed later voice change makes
    every earlier clip stale, including those without a text snapshot.
    """
    from agents import voice_changes

    started = clip.step.started_at if clip.step is not None else clip.created_at
    if voice_changes.changed_after(scene.job, started):
        return False
    words = (clip.sent or {}).get("voice_words")
    return words is None or words in {
        voice_words(scene.job),
        _older_voice_words(scene.job),
    }


def clip_presenter_is_current(scene: Scene, clip: ProducedItem) -> bool:
    """Keep old talking snapshots usable, while honoring a real presenter remake.

    Talking steps historically kept no portrait when their B-roll face label was blank.
    Their starting picture's sent list still identifies the presenter. Older clips without
    that record use the portrait present when the original file was made, never when a
    later free restore copied it.
    """
    if scene.kind != Scene.Kind.TALKING or (clip.step and clip.step.portrait_id is not None):
        return True
    current = latest(scene.job, ProducedItem.Kind.PORTRAIT)
    portrait_id = None
    if clip.picture is not None:
        from .scene_pictures import _place_inputs

        files = _place_inputs(clip.picture)
        portrait_id = (
            scene.job.produced.filter(kind=ProducedItem.Kind.PORTRAIT, file__in=files)
            .values_list("pk", flat=True)
            .first()
        ) or (
            scene.job.produced.filter(file__in=files, person__isnull=False)
            .values_list("person_id", flat=True)
            .first()
        )
    if portrait_id is None:
        original = scene.produced.filter(kind=ProducedItem.Kind.CLIP, file=clip.file).earliest(
            "created_at", "pk"
        )
        portrait_id = (
            scene.job.produced.filter(
                kind=ProducedItem.Kind.PORTRAIT,
                created_at__lte=(
                    original.step.started_at if original.step is not None else original.created_at
                ),
            )
            .order_by("-created_at", "-pk")
            .values_list("pk", flat=True)
            .first()
        )
    return portrait_id == (current.pk if current is not None else None)


def stale_clip_reason(scene: Scene, clip: ProducedItem) -> str:
    """Explain a kept version that cannot finish the scene as it stands now."""
    scene.refresh_from_db()
    if says_the_line_as_it_stands(scene, clip):
        return ""
    changed = []
    if clip.step is not None:
        now = SceneStep.snapshot(scene)
        for field, name in (("place", "place"), ("portrait", "person"), ("line", "line")):
            if getattr(clip.step, field) != now[field]:
                changed.append(name)
    if not clip_presenter_is_current(scene, clip) and "person" not in changed:
        changed.append("person")
    if not clip_voice_is_current(scene, clip):
        changed.append("voice")
    reason = " and ".join(changed) or "scene inputs"
    return (
        f"Scene {scene.number} needs filming again: its {reason} changed since this clip was sent. "
        "The old clip stays a version; it cannot finish the current scene."
    )


def finish_with_clip(scene: Scene, clip: ProducedItem | None) -> bool:
    """Only a clip made for the live scene may mark it finished."""
    scene.refresh_from_db()
    current = clip is not None and says_the_line_as_it_stands(scene, clip)
    scene.status = Scene.Status.FINISHED if current else Scene.Status.PLANNED
    scene.save(update_fields=["status"])
    if not current:
        post_notice(
            scene.job,
            notices.SCENE_CLIP_NOT_CURRENT,
            stale_clip_reason(scene, clip)
            if clip is not None
            else f"Scene {scene.number} needs filming again: no clip exists for its "
            "restored finished status.",
            Message.Level.PROBLEM,
            scene=scene.number,
        )
    return current


def says_the_line_as_it_stands(scene: Scene, clip: ProducedItem) -> bool:
    """Whether `clip` still says the scene's line, in the person's voice, and was made for
    what the scene shows: the clip the ad is assembled from, if it were the newest."""
    if not clip_voice_is_current(scene, clip) or not clip_presenter_is_current(scene, clip):
        return False
    if clip.made_from_id is None:
        return scene.produced.filter(
            pk=clip.pk, **SceneStep.made_for(scene, through="step__")
        ).exists()
    audio = clip.made_from
    return (
        audio is not None
        and audio.made_from == latest(scene.job, ProducedItem.Kind.VOICE)
        and scene.produced.filter(
            pk=clip.pk, **SceneStep.made_for(scene, through="step__")
        ).exists()
    )


def _use_the_photos_of(scene: Scene, version: Version) -> None:
    """Have the scene use the shop owner's photos `version` was made with, and only those:
    any used since is set aside, and any dropped since is used again."""
    rows = {
        row.pk
        for made in (version.picture, version.clip)
        if made is not None and made.step is not None
        for row in made.step.reference_photos.all()
    }
    scene.photos_in_use().exclude(pk__in=rows).update(set_aside_at=timezone.now())
    scene.reference_photos.filter(pk__in=rows).update(set_aside_at=None)


def _ask_for_clip(job: Job, making: str, handoff: BrollClipHandoff) -> str:
    """Ask the video model for the clip `handoff` describes, and pay for it. Gives its id."""
    return submit_broll_clip(
        job=job,
        purpose=making,
        starting_picture_key=handoff.starting_picture,
        end_picture_key=handoff.end_picture,
        example_picture_keys=handoff.example_pictures,
        seconds=handoff.seconds,
        prompt=handoff.prompt,
        reference_videos=handoff.reference_videos,
        prompt_enhancement=handoff.prompt_enhancement,
    )


def _clip_purposes(step: SceneStep) -> tuple[str, str]:
    """What a scene's clip is asked for and collected as: talking clips and B-roll ones are
    each made by their own video model."""
    return "make_broll_clip", "collect_broll_clip"


def _clip_asked_for(
    step: SceneStep,
    handoff: BrollClipHandoff,
    making: str,
    collecting: str,
    *,
    remake: int = 0,
) -> str | None:
    """The id of a clip already paid for with this same handoff that may still be made, so
    it is waited for rather than paid for again: asked for by this step before the worker
    stopped, or by an earlier one that stopped or gave up while the video service was down.
    None if there is none, or the video model said it couldn't make it, or it was fetched:
    then it was kept, or it was refused. `making` and `collecting` are the purposes this kind
    of clip is asked for and collected as."""
    job = step.scene.job
    if making not in catalog.MODEL_FOR_PURPOSE or collecting not in catalog.MODEL_FOR_PURPOSE:
        raise ClipFailed("this clip purpose was retired, so its paid render cannot be collected")
    # The step's first clip is the first asked for by it, and each remake the next.
    asked_by_this_step = (
        job.model_calls.filter(
            purpose=making, outcome=ModelCall.Outcome.SUCCEEDED, tool_call=step.tool_call
        )
        .order_by("id")[remake:]
        .first()
    )
    if asked_by_this_step is not None and asked_by_this_step.output is not None:
        # Such as one asked of the old Boreal before Boreal-H3 replaced it: no service here
        # can wait for it, and asking again might pay twice.
        if asked_by_this_step.model != catalog.MODEL_FOR_PURPOSE[making]:
            raise ClipFailed(
                f"it was asked of {asked_by_this_step.model}, a video model no longer used, "
                "so it can't be collected"
            )
        return str(asked_by_this_step.output["video_id"])
    asked = job.model_calls.filter(
        purpose=making, outcome=ModelCall.Outcome.SUCCEEDED, handoff=handoff.model_dump()
    ).last()
    if asked is None or asked.output is None:
        return None
    video_id = str(asked.output["video_id"])
    collected = job.model_calls.filter(purpose=collecting, handoff={"video_id": video_id})
    # Given up on while the service was down, it may still be made. Failed (ClipFailed),
    # it never will be.
    failed = collected.filter(error__startswith=f"{ClipFailed.__name__}:").exists()
    fetched = collected.filter(outcome=ModelCall.Outcome.SUCCEEDED).exists()
    return None if failed or fetched else video_id


def clip_transcript(clip: ProducedItem) -> ProducedItem | None:
    """The words timed in this exact clip, or in its line audio for historical clips."""
    assert clip.scene is not None
    return (
        clip.scene.produced.filter(
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
            made_from=clip if clip.made_from_id is None else clip.made_from,
        )
        .order_by("version")
        .last()
    )


def timed_caption_words(
    clip: ProducedItem, transcript: ProducedItem | None, *, line: str
) -> list[dict[str, Any]]:
    """The written line timed for this clip, shared by the finished ad and its preview."""
    words = transcript.words if transcript is not None else []
    checked = (clip.ruling or {}).get("word_check", {})
    caption_line = (
        checked.get("caption_line") if checked.get("verdict") == "pass" else None
    ) or line
    return assembly.timed_script(caption_line, words) if words else []


def assemble_ad(
    job: Job, scenes: list[tuple[ProducedItem, ProducedItem | None]], music: ProducedItem
) -> ProducedItem:
    """Join the clips, their sound, written captions and overlays with music underneath.

    Every retained and new clip plays with its own sound, except the silence before the
    first clip's first word, cut to leave the ad opening on it, and any silence over
    assembly.AFTER_LAST_WORD_SECONDS after a clip's last word (#442). Captions use each
    clip's transcript, including historical audio-backed transcripts, when available. Keep
    the result as the next ad version, with no model call.
    """
    whole = []
    timed = []
    last_words: list[float | None] = []
    for clip, transcript in scenes:
        assert clip.scene is not None and clip.seconds is not None, "a clip is a scene's, measured"
        words = transcript.words if transcript is not None else []
        last_word = assembly.last_word_end(words)
        if words and not assembly.usable_last_word(last_word, clip.seconds):
            post_notice(
                job,
                notices.CLOSING_SILENCE_NOT_CUT,
                f"Scene {clip.scene.number}'s clip plays to its end: its transcript's last word "
                f"has no usable time ({words[-1].get('end')!r} in a {clip.seconds:g} s clip), so "
                "any silence after the line is kept.",
                Message.Level.PROBLEM,
            )
        last_words.append(last_word)
        whole.append((clip.scene.number, clip.pk, clip.seconds, clip.scene.overlay))
        timed.append(timed_caption_words(clip, transcript, line=clip.scene.line))
    # The ad opens on its hook's first word, not on the silence H3 left before it.
    first_word = timed[0][0]["start"] if timed and timed[0] else None
    # And moves on from each clip soon after its last word, not after a silent stall.
    planned = assembly.planned_cuts(
        whole,
        opening_cut=assembly.opening_silence(first_word, whole[0][2]) if whole else 0.0,
        last_words=last_words,
    )
    drawn = assembly.captions(planned, timed)
    card = end_card_of(job, planned)
    photo = end_card_photo(job)
    # ffmpeg reads and writes files on this machine, and the clips are in the file store.
    with tempfile.TemporaryDirectory() as folder:
        parts = []
        for (clip, _), cut in zip(scenes, planned, strict=True):
            path = Path(folder) / f"scene-{cut.scene}.mp4"
            path.write_bytes(file_store.read(clip.file))
            parts.append((path, cut))
        under = Path(folder) / "music"
        under.write_bytes(file_store.read(music.file))
        on_the_card = None
        if photo is not None:
            try:
                flat = assembly.flat_photo(file_store.read(photo.file))
            except ValueError as unreadable:
                post_notice(
                    job,
                    notices.END_CARD_WITHOUT_PHOTO,
                    f"Product photo {photo.position} couldn't go on the end card ({unreadable}), "
                    "so the ad ends on a card with the product's name, line, price and website "
                    "but no photo.",
                    Message.Level.PROBLEM,
                )
                photo = None
            else:
                on_the_card = Path(folder) / "end-card-photo.png"
                on_the_card.write_bytes(flat)
        ad = Path(folder) / "ad.mp4"
        assembly.join(parts, ad, music=under, drawn=drawn, end_card=card, photo=on_the_card)
        file = file_store.save("ad.mp4", ad.read_bytes())
    last = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).aggregate(last=Max("version"))
    return ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.FINISHED_AD,
        version=(last["last"] or 0) + 1,
        file=file,
        made_from=music,
        seconds=card.end,
        cuts=[asdict(cut) for cut in planned],
        captions=[asdict(caption) for caption in drawn],
        end_card=end_card_record(card, photo),
    )


def end_card_of(job: Job, cuts: Sequence[assembly.Cut]) -> assembly.EndCard:
    """The end card `job`'s ad ends on after `cuts` (spec #401): the product's name, the
    plan's end card line and price, and the website of the product's link."""
    return assembly.end_card_after(
        cuts,
        END_CARD_SECONDS,
        name=job.product_name,
        line=job.end_card_line,
        price=job.end_card_price,
        website=assembly.website_of(job.product_url),
    )


def end_card_photo(job: Job) -> ProductPhoto | None:
    """The product photo `job`'s end card shows large: the first in the ad's colour, preferring
    one of the product alone, with no stranger's face and not cut from a video; else the
    first photo with no stranger's face. None, with a developer notice, when there is none:
    the card is drawn without a photo."""
    usable = job.photos.filter(has_face=False, from_video=None)
    photo = (
        usable.filter(shows_product_colour=True).order_by("-alone", "position").first()
        or usable.order_by("position").first()
    )
    if photo is None:
        post_notice(
            job,
            notices.END_CARD_WITHOUT_PHOTO,
            "No product photo without a stranger's face could go on the end card, so the ad "
            "ends on a card with the product's name, line, price and website but no photo.",
            Message.Level.PROBLEM,
        )
    return photo


def end_card_record(card: assembly.EndCard, photo: ProductPhoto | None) -> dict[str, Any]:
    """The end card as a finished ad keeps it: its text and times, and its photo's file."""
    return {**asdict(card), "file": photo.file if photo is not None else ""}


def _next_version(scene: Scene, kind: ProducedItem.Kind) -> int:
    last = scene.produced.filter(kind=kind).aggregate(last=Max("version"))["last"]
    return (last or 0) + 1


def latest(job: Job, kind: ProducedItem.Kind) -> ProducedItem | None:
    """The job's latest version of `kind`, if it has one."""
    return job.produced.filter(kind=kind).order_by("version").last()


def _conversation(job: Job, *, until: datetime | None = None) -> list[ChatMessage]:
    """What the user and the producer have said, for the models that plan and check the ad
    and plan its scenes: all of it, or what was said by `until`. Facts may come from the
    user's words; the producer's show what the user was answering. Notices are code's
    words to the user, not the producer's, and are left out. So is the page the user pasted
    in the page panel: it is the page text, checked as the page, not their words."""
    if job.session is None:
        # A job started before sessions existed has no conversation.
        return []
    said = (
        job.session.messages.exclude(role=Message.Role.NOTICE)
        .exclude(pasted_page=True)
        .prefetch_related("attachments")
    )
    if until is not None:
        said = said.filter(created_at__lte=until)
    return [
        ChatMessage(
            by="user" if message.role == Message.Role.USER else "producer",
            text=messages.as_read(message),
        )
        for message in said
    ]
