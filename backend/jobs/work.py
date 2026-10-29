"""The work the producer's tools do on a job: reading its page, planning it, making its
person, running the planning checks, making the music, making each scene and assembling the
finished ad."""

import contextvars
import hashlib
import io
import json
import logging
import math
import mimetypes
import re
import tempfile
import wave
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from pydantic import ValidationError

from adforge import file_store
from adforge.retry import OutsideServiceDown, with_retries
from chat import messages
from chat.models import Attachment, Message
from gateway import catalog
from gateway.gateway import (
    IMAGE_TYPE_NAMES,
    IMAGE_TYPES,
    UnreadableImage,
    call_model,
    collect_clip,
    design_voice,
    draw_picture,
    edit_picture,
    make_music,
    past_the_safety_filter,
    refused_by_filter,
    refused_by_halving,
    speak,
    submit_broll_clip,
    submit_clip,
    transcribe,
    transcription_from,
    transcription_output,
)
from gateway.models import ModelCall
from gateway.types import (
    LEAST_BROLL_SECONDS,
    LEAST_CLIP_SECONDS,
    MOST_BROLL_SECONDS,
    BlockedBySafetyFilter,
    BrollClipHandoff,
    ClipFailed,
    ClipHandoff,
    Handoff,
    Image,
    Judgement,
    MusicHandoff,
    ReferenceVideo,
    UnusableReply,
)

from . import (
    assembly,
    auto_fix,
    clip_judge,
    cut_and_send,
    cut_out,
    firecrawl,
    fix_check,
    library,
    library_items,
    library_sheets,
    owner_parts,
    page,
    page_text,
    photos,
    picker_grids,
    reference_photos,
)
from .checks import (
    FACT_CHECK_INSTRUCTIONS,
    LENGTH_ALLOWANCE_SECONDS,
    LENGTHEN_LINE_INSTRUCTIONS,
    LONGEST_BROLL_LINE_SECONDS,
    LONGEST_LINE_SECONDS,
    MOST_BROLL_SHORTENINGS,
    MOST_LENGTHENINGS,
    MOST_REWRITES,
    REWRITE_INSTRUCTIONS,
    SHORTEN_INSTRUCTIONS,
    SHORTEN_LINE_INSTRUCTIONS,
    SHORTEST_BROLL_LINE_SECONDS,
    FactCheck,
    FactCheckHandoff,
    LengthenLineHandoff,
    LineToCheck,
    Problem,
    RewriteHandoff,
    RewrittenLine,
    ShortenHandoff,
    ShortenLineHandoff,
    count_words,
    fact_check_for,
    fewest_words_in_a_broll_line,
    fits_target,
    line_seconds,
    most_words,
    most_words_in_a_broll_line,
    most_words_in_a_line,
    rewritten_scene_for,
    script_seconds,
    shortened_script_for,
)
from .job_locks import one_job_at_a_time as _one_job_at_a_time
from .library import Drawn, PictureLabel, SentList, watch_of
from .models import (
    BROLL_FIELDS,
    PLANNED_FOR_THE_ACTION,
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
from .picture_check import (
    ASKS,
    FAILS_TO_FAIL,
    STARTING_PICTURE_CHECK,
    StartingPictureVerdict,
)
from .planning import (
    PLAN_INSTRUCTIONS,
    ChatMessage,
    Need,
    PlanHandoff,
    ProducerDecision,
    ScenePicture,
    producer_decision_for,
)
from .reference_photos import ReferencePhotoUnusable
from .scenes import (
    BEFORE_PICTURE_INSTRUCTIONS,
    PERSON_IN_ROOM_PROMPT,
    ROOM_ALONE_PROMPT,
    SCENE_NEW_PLACE_PROMPT,
    SCENE_PLACE_EDIT_PROMPT,
    STARTING_PICTURE_INSTRUCTIONS,
    BeforePictureHandoff,
    BeforePicturePlan,
    BrollSceneToDraw,
    ClipFromNoteChoice,
    ClipFromReferencesHandoff,
    StartingPictureChoice,
    StartingPictureHandoff,
    clip_from_references_instructions,
    pose_for,
    starting_picture_choice_for,
    talking_motion_prompt,
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
    if not settings.FIRECRAWL_API_KEY:
        post_notice(job, NO_FIRECRAWL, Message.Level.PROBLEM)
        return PageRead(_plain_download(link))
    answers = _firecrawl_answers(job, link)
    marked, record, read = answers["marked"], answers["product"], answers["page"]
    if isinstance(read, page.PageUnreadable):
        raise read
    if isinstance(read, Exception):
        post_notice(
            job,
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
    asks: dict[_FirecrawlCall, Callable[[], Any]] = {
        "page": lambda: firecrawl.read_page(link),
        "marked": lambda: firecrawl.marked_screenshot(link),
        "product": lambda: firecrawl.product_record(link),
    }
    # The calls only wait on Firecrawl; the answers are saved here, by this thread.
    with ThreadPoolExecutor(max_workers=3) as pool:
        asked = {pool.submit(ask): call for call, ask in asks.items() if call not in answers}
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
        marks=marks,
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
    matched back to the page, then the prices in Firecrawl's `record` of it, then the product
    data the page declares without its prices: the record's are the only ones that count.
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
    prices = page_text.record_prices(record) if isinstance(record, dict) else ""
    copied_text = "\n".join(page_text.match_back(copied.passages, on_the_page))
    return copied_text + prices + page_text.without_prices(product_page.declared)


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
        except (UnusableReply, OutsideServiceDown) as error:
            post_notice(
                job,
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
    notices: list[tuple[str, Message.Level]] = []
    record = read.record
    if isinstance(record, firecrawl.FirecrawlFailed):
        notices.append(
            (
                f"Firecrawl couldn't get the shop's record of the product ({record}), so its "
                "photos were picked without the official photos to compare them with: a "
                "look-alike product's photo is a little more likely to get through.",
                Message.Level.PROBLEM,
            )
        )
        record = None
    elif record is None:
        notices.append(
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
        notices.append(
            (
                f"Picking this product's photos off the page failed ({error}). {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, notices)
    if left_out:
        notices.append((_left_out_by_the_filter(left_out), Message.Level.PROBLEM))
    if picked.page_blocked:
        notices.append((PAGE_BLOCKED, Message.Level.PROBLEM))
        return Picked(None, notices, blocked=True)
    links = photos.picked_links(
        picked, read.marked.marks, photos.record_photo_urls(record) if record else []
    )
    videos = tuple(_picked_videos(picked, read.marked.marks))
    if not links:
        notices.append(
            (
                f"The photo picker found no photo of this product on the page. {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, notices, videos=videos)
    labels, copies, unlabelled = _labels_and_copies(picked, links)
    if unlabelled:
        notices.append((_kept_unlabelled(unlabelled), Message.Level.PROBLEM))
    return Picked(
        [link.url for link in links],
        notices,
        frozenset(link.url for link in links if link.has_face),
        unnoted=frozenset(link.url for link in links if link.has_face is None),
        videos=videos,
        as_given={link.url: link.as_given for link in links if link.as_given != link.url},
        labels=labels,
        copies=copies,
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
            labels[link.url] = found.label_of()
        except ValidationError:
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
    once more, shown the rest of them drawn again in grids; when that can't be had, the
    answer without the grids stands. Raises BlockedBySafetyFilter when it refuses even the
    screenshot."""

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
        # The tiles refused alone, refused only together or not found: the pick made without
        # the grids stands.
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
    in grids. Raises when that answer can't be had."""
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

    def asked_with(some: list[picker_grids.CutTile]) -> photos.PickedPhotos:
        return asked(_with_grids_drawn_again(job, kept, some))

    refused = refused_by_halving(lambda some: refused_by_filter(asked_with, some), cut)
    return asked_with([tile for tile in cut if tile not in refused]), [
        tile.label for tile in refused
    ]


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
    this_read: list[ProductPhoto] = []
    count = page_photos.count()
    skipped = []
    for url in urls:
        if count >= page.MAX_PHOTOS:
            break
        if url in links:
            continue
        group = list(copies.get(url, (url,))) if copies is not None else []
        if copies is not None and (copy := _kept_copy(kept_for, [url, *group])) is not None:
            if copy in this_read:
                _merge_face(copy, url, faces)
            continue
        try:
            photo = _photo_at(url, (as_given or {}).get(url))
        except (page.PageUnreadable, OutsideServiceDown) as error:
            skipped.append(SkippedPhoto(url, str(error).rstrip(".") + "."))
            continue
        # Only formats the models can read, so every kept photo can be shown to them.
        if photo.content_type not in IMAGE_TYPES:
            skipped.append(
                SkippedPhoto(
                    url,
                    f"It came back as {photo.content_type or 'an unknown type'}, "
                    f"not a {IMAGE_TYPE_NAMES} image.",
                )
            )
            continue
        size = photos.size_of(photo.content)
        if size is not None and min(size) < photos.SMALLEST_PHOTO:
            skipped.append(
                SkippedPhoto(
                    url, f"It's only {size[0]} by {size[1]} px, too small to be a product photo."
                )
            )
            continue
        saved += 1
        count += 1
        links.add(url)
        has_face = url in faces if faces is not None and url not in unnoted else None
        kept = keep_photo(
            job,
            saved,
            photo.content,
            photo.content_type,
            source_url=url,
            has_face=has_face,
            picked_as=(labels or {}).get(url),
            copy_links=group,
        )
        this_read.append(kept)
        _keep_for_its_copies(kept_for, kept, [url, *group])
    return skipped


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
) -> ProductPhoto:
    """Store a product photo with the job, with its Face note, or with None, noted by a call
    of its own. An uploaded photo has no source link. A video picture is kept with the video
    it was cut `from_video`, its second there and its one-line `note`. A page photo the
    picker kept has the fields it was `picked_as` and the links of its copy group."""
    extension = mimetypes.guess_extension(content_type) or ""
    key = file_store.save(f"jobs/{job.pk}/photos/{position}{extension}", content)
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
    )


def note_face(job: Job, source: str, key: str) -> bool:
    """Whether the photo in the file store at `key` shows a stranger's face, by a call of its
    own: for a photo the picker never saw. `source` names it by where it came from, so a page
    read again pays for nothing twice though its photos are stored again under new keys. A
    failed call counts as a face, the safe side: such a photo is only used when no other shows
    what's needed. That fallback is said in red, with its warning line."""
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
    except (UnusableReply, OutsideServiceDown, UnreadableImage) as error:
        logger.warning("Job %s: photo %s's face note failed (%s)", job.pk, source, error)
        post_notice(job, FACE_NOT_NOTED, Message.Level.PROBLEM)
        return True
    return noted.has_face


# What the shop owner is told in red when a photo's face note failed, so it counts as showing a
# face. NOT APPROVED YET: the wording.
FACE_NOT_NOTED = (
    "Checking whether one of the product's photos shows a stranger's face didn't work this "
    "time, so it counts as showing one: it is used only when no other photo shows what the ad "
    "needs."
)


def plan(job: Job) -> ProducerDecision:
    """Have the planner plan the ad from the video moments, the library's sheets, the page and
    the conversation, and keep the plan. A decision to ask the user keeps nothing."""
    sheets = library_sheets.sheets_of(job)
    library_sheets.tell_not_shown(job, sheets)
    library, moments = _library_numbers(job), moments_by_video(job)
    decision, left_out = library_sheets.past_the_filter(
        job,
        sheets,
        lambda images: call_model(
            job=job,
            purpose="plan_ad",
            instructions=PLAN_INSTRUCTIONS,
            handoff=plan_handoff(job, job.page_text, sheets),
            output=producer_decision_for(library=library, moments=moments),
            images=images,
        ),
    )
    if left_out:
        named = [image.label.partition(" (")[0].lower() for image in left_out]
        post_left_out_by_the_filter(job, named, "the ad was planned")
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
                shows=scene.shows or "",
                overlay=" ".join((scene.overlay or "").split()),
                part=scene.part or "",
                second_state=scene.second_state,
                **{**scene.broll_details(), "needs": photos_of.needs(scene.needs)},
                owner_part=scene.owner_part.model_dump() if scene.owner_part else None,
                scene_pictures=[
                    picture.model_dump(by_alias=True) for picture in scene.scene_pictures
                ],
                piece=scene.piece.model_dump() if scene.piece else None,
            )
            for number, scene in enumerate(planned.scenes, start=1)
        )
        job.product_name = planned.product_name
        job.product_colour = planned.product_colour
        job.product_size = planned.product_size
        job.product_behaviour = planned.product_behaviour
        job.person_gender = planned.person_gender
        job.person_looks = planned.person_looks
        job.person_voice = planned.person_voice
        job.script_format = planned.script_format or ""
        job.status = Job.Status.PLANNED
        job.save(
            update_fields=[
                "product_name",
                "product_colour",
                "product_size",
                "product_behaviour",
                "person_gender",
                "person_looks",
                "person_voice",
                "script_format",
                "status",
            ]
        )
        # A picture left out past the safety filter was never seen, so never marked.
        unseen = sheets.numbers_on(left_out)
        job.photos.filter(
            position__in=photos_of.photos(
                [number for number in planned.colour_photos if number not in unseen], "colour"
            )
        ).update(shows_product_colour=True)
    photos_of.tell(job)
    return decision


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
    return PlanHandoff(
        moments=library_sheets.moments_text(job),
        library_pictures=sheets.library_pictures,
        pictures_not_shown=sheets.not_shown,
        video_transcripts=_video_transcripts(job),
        page_text=page.for_model(page_text),
        product_url=job.product_url,
        target_seconds=job.target_seconds,
        conversation=_conversation(job),
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
                post_notice(job, text, Message.Level.PROBLEM)


def _photos_of_pictures(job: Job) -> _PhotosOfPictures:
    return _PhotosOfPictures(
        library_sheets.photo_positions(job), library_sheets.recoloured_numbers(job)
    )


def _labelled(photo: ProductPhoto) -> Image:
    """A photo as the planner and the fact check are shown it: by its number, and a video
    picture also by the second of the product's video it was cut at and what it shows."""
    label = f"Photo {photo.position}"
    if photo.from_video_id is not None:
        label += f" (from the product's video at {photo.at_second:.1f} s: {photo.note})"
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


def _how_the_voice_sounds(job: Job) -> str:
    """The voice as it is designed: the plan's gender first, then its description, so the
    voice presents as the person the portrait shows rather than as a random pick. A job
    planned before the plan said the gender has only the description."""
    if not job.person_gender:
        return job.person_voice
    return f"A {job.person_gender}'s voice. {job.person_voice}"


def create_person(job: Job) -> tuple[ProducedItem, ProducedItem]:
    """Make the person who presents the ad: a portrait, and a voice made to match whose
    speaking speed is measured on the script. Gives back the portrait and the voice.

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
    voice = latest(job, ProducedItem.Kind.VOICE)
    if voice is None:
        paid_for = _paid_for_before(job, "design_voice")
        voice_id = (
            paid_for["voice_id"]
            if paid_for
            else design_voice(
                job=job,
                purpose="design_voice",
                description=_how_the_voice_sounds(job),
                sample=job.scenes.values_list("line", flat=True)[0],
            )
        )
        voice = ProducedItem.objects.create(
            job=job, kind=ProducedItem.Kind.VOICE, voice_id=voice_id
        )
    if voice.words_per_second is None:
        _measure_voice(job, voice)
    return portrait, voice


# How the producer's own instruction is put on top of a standard prompt for the person
# (spec #247 point 6: a tool with an AI inside takes the producer's detailed instruction).
# Not approved yet: the wording.
ON_TOP = "{standard}\nOn top of that, which wins where the two differ: {instruction}"


def _on_top(standard: str, instruction: str) -> str:
    """`standard` with the producer's `instruction` on top, or alone when there is none."""
    instruction = instruction.strip()
    return ON_TOP.format(standard=standard, instruction=instruction) if instruction else standard


def remake_person(
    job: Job, *, look: bool, voice: bool, instruction: str, call: ToolCall
) -> tuple[ProducedItem | None, ProducedItem | None]:
    """Make the presenter again (spec #247 point 5): a new portrait when `look`, a new voice,
    measured on the script, when `voice`, each from the plan's person as it stands with the
    producer's `instruction` on top of the standard prompt, each kept as the next version.
    Gives back what was made: the new portrait, then the new voice, None for what wasn't.

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
    made_voice = None
    if voice:
        paid_for = _paid_for_before(job, "design_voice", charged_to=call)
        voice_id = (
            paid_for["voice_id"]
            if paid_for
            else design_voice(
                job=job,
                purpose="design_voice",
                description=_on_top(_how_the_voice_sounds(job), instruction),
                sample=job.scenes.values_list("line", flat=True)[0],
            )
        )
        made_voice = _job_version(job, ProducedItem.Kind.VOICE, voice_id=voice_id)
        if made_voice.words_per_second is None:
            _measure_voice(job, made_voice, charged_to=call)
    return portrait, made_voice


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
    job: Job, purpose: str, *, charged_to: ToolCall | None = None
) -> dict[str, Any] | None:
    """What a call for `purpose` made before the worker stopped, if it was paid for but not
    kept: for the job, or for the tool call it was `charged_to`. Every call is recorded as
    soon as it succeeds, so a restart reuses what it made."""
    call = _paid_calls(job, purpose, charged_to=charged_to).last()
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


def _measure_voice(job: Job, voice: ProducedItem, *, charged_to: ToolCall | None = None) -> None:
    """Measure how fast the voice really speaks by having it read the whole script: no
    speaking speed is assumed.

    Speech paid for before a worker stopped is read back rather than spoken again. It was
    spoken from this same script: making the person is what runs again first after a
    restart, so nothing gets a turn in between to rewrite a line."""
    script = " ".join(job.scenes.values_list("line", flat=True))
    paid_for = _paid_for_before(job, "measure_voice", charged_to=charged_to)
    voice.file = (
        paid_for["file"]
        if paid_for
        else speak(job=job, purpose="measure_voice", voice_id=voice.voice_id, text=script)
    )
    voice.words_per_second = count_words(script) / _seconds(file_store.read(voice.file))
    voice.save(update_fields=["file", "words_per_second"])


def _seconds(wav: bytes) -> float:
    """How long a WAV recording lasts."""
    with wave.open(io.BytesIO(wav)) as audio:
        return float(audio.getnframes() / audio.getframerate())


@dataclass(frozen=True)
class Asking:
    """Something the planning checks can't settle without the user: what it is about, the
    question with the facts they need to answer it, and one sentence on why it is asked."""

    about: Literal["unclear_page", "line", "line_length", "length"]
    question: str
    reason: str
    # The scene whose line is asked about, for a line or a line's length.
    scene: Scene | None = None


def run_checks(job: Job) -> Asking | None:
    """Check the script before anything is rendered: every line, and what each scene shows,
    against the page; then each line against how long its clip can last, and the whole
    script against the target length. Each problem is fixed, or asked about. None once
    every check has passed.

    Run again after a restart, or after the user answers, the checks carry on from where
    they stopped: lines already checked stay checked."""
    while True:
        if job.scenes.filter(fact_checked=False).exists():
            asking = _fact_check(job)
        else:
            fit = _fit_length(job)
            if fit is True:
                job.status = Job.Status.READY_TO_RENDER
                job.save(update_fields=["status"])
                return None
            asking = fit if isinstance(fit, Asking) else None
        if asking is not None:
            return asking


def _fact_check(job: Job) -> Asking | None:
    unchecked = list(job.scenes.filter(fact_checked=False))
    conversation = _conversation(job)
    # A line whose failure was stored before a restart is fixed from that failure, rather
    # than checked, and paid for, again.
    scenes = [scene for scene in unchecked if not _needs_fixing(scene)]
    if scenes:
        unclear = _checked(job, scenes, conversation)
        if unclear is not None:
            return unclear
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


def _checked(job: Job, scenes: list[Scene], conversation: list[ChatMessage]) -> Asking | None:
    """Fact-check the scenes' lines and what they show, storing why each that failed did.
    When the page itself is unclear, gives back what to ask the user instead."""
    check = _ask_fact_check(job, [_to_check(scene) for scene in scenes], conversation)
    if check.decision == "unclear":
        assert check.question is not None
        return Asking(about="unclear_page", question=check.question, reason=check.reason)
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
    # the ad's colour, and those the scenes picked from the library (spec #210 point 11). A
    # script where the person talks throughout is checked on text alone.
    positions = library_sheets.photo_positions(job) if showing else {}
    needed = {
        positions[picture["number"]]
        for scene in job.scenes.filter(number__in=showing)
        for picture in scene.scene_pictures
        if picture["number"] in positions
    }
    # A plan may mark no photo in the ad's colour: then every photo that fits is shown.
    marked = job.photos.filter(shows_product_colour=True).exists()
    photos = (
        job.photos.filter(Q(shows_product_colour=True) | Q(position__in=needed)).order_by(
            "position"
        )
        if showing and marked
        else _photos_to_pick_from(job, clip_judge.MOST_PICTURES)
        if showing
        else []
    )
    return call_model(
        job=job,
        purpose="fact_check",
        instructions=FACT_CHECK_INSTRUCTIONS,
        handoff=FactCheckHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            lines=lines,
            video_transcripts=_video_transcripts(job),
        ),
        output=fact_check_for([line.scene for line in lines], showing=showing),
        images=[_labelled(photo) for photo in photos],
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


def _rewrite_scene(job: Job, scene: Scene, conversation: list[ChatMessage]) -> None:
    """Have the scene rewritten whole: its line, what it shows and its B-roll details. A
    scene that shows something is rewritten seeing the library's sheets, as the planner does,
    to say what it needs."""
    # A scene where the person talks is rewritten on text alone: no sheet is drawn for it.
    sheets = library_sheets.sheets_of(job, drawn=bool(scene.shows))
    photos_of = _photos_of_pictures(job)
    marked = set(job.photos.filter(shows_product_colour=True).values_list("position", flat=True))
    colour_photos = photos_of.colour_numbers(marked)
    if scene.shows:
        library_sheets.tell_not_shown(job, sheets)
    rewrite = call_model(
        job=job,
        purpose="rewrite_line",
        instructions=REWRITE_INSTRUCTIONS,
        handoff=RewriteHandoff(
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
                    # Failures stored before scenes could show something were of the line.
                    wrong=problem.get("wrong", "line"),
                    problem=problem["problem"],
                    page_says=problem["page_says"],
                )
                for problem in scene.fact_problems
            ],
        ),
        output=rewritten_scene_for(
            scene.number,
            shows_something=bool(scene.shows),
            library=_library_numbers(job),
        ),
        images=sheets.images if scene.shows else [],
    )
    scene.fact_problems[-1]["rewritten"] = True
    scene.change_line(
        rewrite.line,
        rewrite.shows or "",
        broll={**rewrite.broll_details(), "needs": photos_of.needs(rewrite.needs)},
    )
    photos_of.tell(job)
    with transaction.atomic():
        if rewrite.shows is None:
            # A talking scene shows no second state, and the scene after it no longer follows
            # the first state it was made from, as in _say_it_to_camera.
            scene.second_state = False
            job.scenes.filter(number=scene.number + 1).update(second_state=False)
        scene.save(
            update_fields=[
                "line",
                "shortened_from",
                "shows",
                *BROLL_FIELDS,
                *PLANNED_FOR_THE_ACTION,
                "status",
                "fact_problems",
                "second_state",
            ]
        )


def _about_line(scene: Scene) -> Asking:
    last = scene.fact_problems[-1]
    return Asking(
        about="line",
        question=(
            f"Scene {scene.number}'s line still fails the fact check after "
            f'{MOST_REWRITES} rewrites: "{scene.line}"{_while_its_said(scene)} '
            f"{last['problem']} The page says: {last['page_says']}"
        ),
        reason=(
            f"The line was rewritten {MOST_REWRITES} times and still failed the fact check, "
            "so you decide: the check itself may be wrong."
        ),
        scene=scene,
    )


def _while_its_said(scene: Scene) -> str:
    """What a scene shows while its line is said, as the user is told it, or nothing when
    the person says it to camera. The user is never told the kinds of scene apart."""
    return f" While it's said, the ad shows: {scene.shows.rstrip('.')}." if scene.shows else ""


def _fit_length(job: Job) -> bool | Asking:
    """Whether the script can go on to be rendered at its length: every line fitting its
    clip, and the whole script within its target. If it can't, a line or the script is
    rewritten, or a B-roll scene made a talking one, giving False so the checks go round
    again, or the user is asked."""
    voice = latest(job, ProducedItem.Kind.VOICE)
    assert voice is not None and voice.words_per_second is not None
    words_per_second = voice.words_per_second
    to_fit = _a_line_to_fit(job, words_per_second)
    if to_fit is not None:
        scene, fit = to_fit
        if fit == "ask":
            return _ask_for_a_shorter_line(scene, words_per_second)
        if fit == "lengthen":
            _lengthen_line(job, scene, words_per_second)
        elif fit == "shorten_broll":
            _shorten_line(job, scene, most_words_in_a_broll_line(words_per_second))
        elif fit == "shorten_talking":
            _shorten_line(job, scene, most_words_in_a_line(words_per_second))
        else:
            _say_it_to_camera(scene)
        return False
    target = job.target_seconds
    if target is None or job.length_choice == Job.LengthChoice.KEEP_LONGER:
        return True
    lines = list(job.scenes.values_list("line", flat=True))
    seconds = script_seconds(lines, words_per_second)
    if fits_target(seconds, target):
        return True
    if (
        job.length_choice == Job.LengthChoice.SHORTEN
        and _shortened_since_the_user_spoke(job) < MOST_REWRITES
    ):
        _shorten(job, words_per_second)
        return False
    # Shortening again takes the user choosing it again.
    job.length_choice = ""
    job.save(update_fields=["length_choice"])
    return Asking(
        about="length",
        question=(
            f"Your script runs about {seconds:.1f} seconds, {seconds - target:.1f} over your "
            f"{target}-second target."
        ),
        reason=(
            f"At the voice's measured speed the script runs {seconds:.1f} seconds, more "
            f"than {LENGTH_ALLOWANCE_SECONDS} seconds over the {target} seconds you asked for."
        ),
    )


# What a line that doesn't fit its clip needs: a B-roll line lengthened or shortened, or its
# scene made a talking one; a talking line shortened, or the user asked for a shorter one.
Fit = Literal["lengthen", "shorten_broll", "say_to_camera", "shorten_talking", "ask"]


def _a_line_to_fit(job: Job, words_per_second: float) -> tuple[Scene, Fit] | None:
    """The first scene whose line doesn't fit its clip, and what it needs."""
    for scene in job.scenes.all():
        fit = _fit_for(job, scene, words_per_second)
        if fit is not None:
            return scene, fit
    return None


def _fit_for(job: Job, scene: Scene, words_per_second: float) -> Fit | None:
    """What a scene's line needs to fit its clip, or None when it fits, or is kept as it is.

    A talking line may take up to LONGEST_LINE_SECONDS to say: longer, it is shortened
    MOST_REWRITES times since the user last spoke, then they are asked. A B-roll line must
    take SHORTEST_BROLL_LINE_SECONDS to LONGEST_BROLL_LINE_SECONDS: too short, it is
    lengthened MOST_LENGTHENINGS times, then kept; too long, it is shortened
    MOST_BROLL_SHORTENINGS times, then its scene becomes a talking scene, whichever scene it
    is: a middle scene may fall back to the person talking (round 2 fixes). A B-roll line the
    user chose is never rewritten: too long, it is the same, at once. Tries are counted per
    scene, ever."""
    seconds = line_seconds(scene.line, words_per_second)
    if not scene.shows:
        if seconds <= LONGEST_LINE_SECONDS:
            return None
        shortened = _shortenings(job, scene, of_broll=False, since=_last_heard_from_the_user(job))
        return "shorten_talking" if shortened < MOST_REWRITES else "ask"
    chosen = _chosen_by_the_user(scene)
    if seconds > LONGEST_BROLL_LINE_SECONDS:
        if chosen or _shortenings(job, scene, of_broll=True) >= MOST_BROLL_SHORTENINGS:
            return "say_to_camera"
        return "shorten_broll"
    if seconds < SHORTEST_BROLL_LINE_SECONDS and not chosen:
        lengthened = job.model_calls.filter(
            purpose="lengthen_line",
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__scene=scene.number,
        ).count()
        if lengthened < MOST_LENGTHENINGS:
            return "lengthen"
    return None


def _chosen_by_the_user(scene: Scene) -> bool:
    """Whether the user chose the scene's line, keeping it or giving their own, when the
    checks asked them about it: it passed without its last failure being rewritten."""
    return scene.fact_checked and _needs_fixing(scene)


def _shortenings(job: Job, scene: Scene, *, of_broll: bool, since: datetime | None = None) -> int:
    """Times the scene's line was shortened while it was a B-roll scene, or a talking one,
    since `since` if given."""
    calls = job.model_calls.filter(
        purpose="shorten_line", outcome=ModelCall.Outcome.SUCCEEDED, handoff__scene=scene.number
    )
    if since is not None:
        calls = calls.filter(created_at__gt=since)
    return sum(
        1
        for handoff in calls.values_list("handoff", flat=True)
        if _showed_something(handoff, scene.number) == of_broll
    )


def _showed_something(handoff: dict[str, Any], number: int) -> bool:
    """Whether scene `number` showed something in the script a model was handed."""
    return any(
        each["scene"] == number and each.get("shows") is not None for each in handoff["script"]
    )


def _ask_for_a_shorter_line(scene: Scene, words_per_second: float) -> Asking:
    """Ask the user for a shorter talking line, once it was shortened as often as allowed and
    is still too long for its clip. A B-roll line too long is said to camera instead."""
    assert not scene.shows, "a B-roll line too long for its clip is said to camera instead"
    seconds = line_seconds(scene.line, words_per_second)
    return Asking(
        about="line_length",
        question=(
            f"Scene {scene.number}'s line still takes about {seconds:.1f} seconds to say "
            f"after {MOST_REWRITES} shortenings, and a scene can last at most "
            f'{LONGEST_LINE_SECONDS} seconds: "{scene.line}"'
        ),
        reason=(
            f"The line was shortened {MOST_REWRITES} times and is still too long for one "
            "scene, so you choose a shorter line."
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
        instructions=SHORTEN_LINE_INSTRUCTIONS,
        handoff=ShortenLineHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            script=[_to_check(each) for each in job.scenes.all()],
            scene=scene.number,
            most_words=most_words,
        ),
        output=RewrittenLine,
        pay_once=pay_once,
    )
    return " ".join(shortened.line.split())


def _lengthen_line(job: Job, scene: Scene, words_per_second: float) -> None:
    lengthened = call_model(
        job=job,
        purpose="lengthen_line",
        instructions=LENGTHEN_LINE_INSTRUCTIONS,
        handoff=LengthenLineHandoff(
            page_text=page.for_model(job.page_text),
            conversation=_conversation(job),
            product_colour=job.product_colour,
            script=[_to_check(each) for each in job.scenes.all()],
            scene=scene.number,
            fewest_words=fewest_words_in_a_broll_line(words_per_second),
        ),
        output=RewrittenLine,
    )
    _new_line(scene, lengthened.line)


def _new_line(scene: Scene, line: str) -> None:
    """Give the scene a rewritten line, which is fact checked again."""
    scene.change_line(" ".join(line.split()))
    scene.fact_checked = False
    scene.fact_problems = []
    scene.save(update_fields=["line", "shortened_from", "status", "fact_checked", "fact_problems"])


def _shortened_since_the_user_spoke(job: Job) -> int:
    """Times the script was shortened since the user last said anything. Each time they
    choose to shorten it, it gets MOST_REWRITES more tries before they are asked again."""
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


def _shorten(job: Job, words_per_second: float) -> None:
    """Have the script rewritten to fit its target. Each line comes back with the scene it
    comes from, and takes that scene's "shows", overlay, part and B-roll fields with it, so a
    dropped scene takes them away and a line never moves under another scene's picture. A
    second state stays one only while the first is still kept just before it."""
    assert job.target_seconds is not None
    script = [_to_check(scene) for scene in job.scenes.all()]
    shortened = call_model(
        job=job,
        purpose="shorten_script",
        instructions=SHORTEN_INSTRUCTIONS,
        handoff=ShortenHandoff(
            page_text=page.for_model(job.page_text),
            conversation=_conversation(job),
            product_colour=job.product_colour,
            target_seconds=job.target_seconds,
            most_words=most_words(job.target_seconds, words_per_second),
            script=script,
        ),
        output=shortened_script_for(script),
    )
    with transaction.atomic():
        scenes = list(job.scenes.all())
        was = {
            scene.number: (scene.shows, scene.overlay, scene.part, _broll_fields(scene))
            for scene in scenes
        }
        # A line that passed the fact check word for word, showing the same, still has;
        # anything else is new.
        checked = {(scene.line, scene.shows) for scene in scenes if scene.fact_checked}
        # A line the user chose stays theirs when it moves: it is never rewritten.
        problems = {scene.number: scene.fact_problems for scene in scenes}
        # A second state stays one only while the first still plays just before it.
        second_states = {scene.number for scene in scenes if scene.second_state}
        previous_kept = None
        for scene, kept in zip(scenes, shortened.lines, strict=False):
            shows, overlay, part, broll = was[kept.scene]
            second_state = kept.scene in second_states and previous_kept == kept.scene - 1
            previous_kept = kept.scene
            if (
                scene.line,
                scene.shows,
                scene.overlay,
                scene.part,
                scene.second_state,
                _broll_fields(scene),
            ) != (kept.line, shows, overlay, part, second_state, broll):
                scene.change_line(kept.line, shows=shows)
                scene.overlay = overlay
                scene.part = part
                scene.second_state = second_state
                for field, value in broll.items():
                    setattr(scene, field, value)
                scene.fact_checked = (kept.line, shows) in checked
                scene.fact_problems = problems[kept.scene] if scene.fact_checked else []
                scene.save(
                    update_fields=[
                        "line",
                        "shortened_from",
                        "shows",
                        "overlay",
                        "part",
                        "second_state",
                        *BROLL_FIELDS,
                        *PLANNED_FOR_THE_ACTION,
                        "status",
                        "fact_checked",
                        "fact_problems",
                    ]
                )
        for scene in scenes[len(shortened.lines) :]:
            scene.delete()


def why_the_checks_passed(job: Job) -> str:
    """One sentence on why the script can go on to be rendered."""
    if job.target_seconds is None:
        return "Every line matches the product page."
    if job.length_choice == Job.LengthChoice.KEEP_LONGER:
        return (
            "Every line matches the product page. The script runs longer than your "
            f"{job.target_seconds}-second target, as you chose."
        )
    return (
        "Every line matches the product page, and the script fits your "
        f"{job.target_seconds}-second target."
    )


def why_the_checks_havent_passed(job: Job) -> str | None:
    """Why the script can't go on to be rendered yet, or None once the planning checks have
    passed: every line has passed the fact check, every line fits its clip (a talking line
    takes up to 18 seconds to say, a B-roll line 4 to 14, unless it was kept after its
    tries), and the script fits its target length or the user chose to keep it longer."""
    unchecked = job.scenes.filter(fact_checked=False).first()
    if unchecked is not None:
        return (
            f"scene {unchecked.number}'s line hasn't passed the fact check, and nothing is made "
            "until every line has. Run the planning checks first."
        )
    voice = latest(job, ProducedItem.Kind.VOICE)
    if voice is None or voice.words_per_second is None:
        return (
            "the person hasn't been made yet, and every line's length is checked with their "
            "voice. Create the person, then run the planning checks."
        )
    to_fit = _a_line_to_fit(job, voice.words_per_second)
    if to_fit is not None:
        scene, fit = to_fit
        how_long = (
            "is too short for its clip"
            if fit == "lengthen"
            else "takes longer to say than a scene can last"
        )
        return f"scene {scene.number}'s line {how_long}. Run the planning checks first."
    target = job.target_seconds
    if target is None or job.length_choice == Job.LengthChoice.KEEP_LONGER:
        return None
    seconds = script_seconds(
        list(job.scenes.values_list("line", flat=True)), voice.words_per_second
    )
    if not fits_target(seconds, target):
        return (
            f"the script runs about {seconds:.1f} seconds, over the {target}-second target, and "
            "the shop owner hasn't chosen to keep it longer. Run the planning checks first."
        )
    return None


# The music is made before the clips, so how long the ad will be is only known roughly: this
# much more is made than the voice takes to say the script, so the music doesn't run out.
MUSIC_SPARE_SECONDS = 5

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


def music_seconds(job: Job, voice: ProducedItem) -> int:
    """How much music the ad needs: as long as the voice takes to say the script, measured,
    and MUSIC_SPARE_SECONDS more."""
    assert voice.words_per_second is not None, "the voice is measured when it is made"
    lines = list(job.scenes.values_list("line", flat=True))
    return math.ceil(script_seconds(lines, voice.words_per_second)) + MUSIC_SPARE_SECONDS


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


def make_starting_picture(step: SceneStep) -> ProducedItem:
    """Make a talking scene's starting picture: a model picks the product photo that suits the
    line best and writes the prompt, then the picture is made from the portrait and that
    photo. Gives back the picture, kept as the scene's next version. A scene that shows the
    product has none: its clip is made from its sent list (spec #210 point 19).

    Run again, as after a worker stopped, it pays for nothing already paid for: the choice
    is made from the conversation as it was when the step started, so it is answered from
    its record, and a picture made but not kept is kept rather than made again."""
    assert not step.shows, "a B-roll clip is made from its sent list, never a starting picture"
    made = step.produced.first()
    if made is not None:
        return made
    scene = step.scene
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    assert portrait is not None, "the tool refuses a scene whose person isn't made"
    # Every product photo that fits beside the portrait, the plan's marks of those in the ad's
    # colour only advice (Julian 2026-10-22): a product seen only in a pack or line-up is
    # still made.
    photos = _photos_to_pick_from(job, clip_judge.MOST_PICTURES - 1)
    numbers = [photo.position for photo in photos]
    note = step.note
    handoff = StartingPictureHandoff(
        scene=scene.number,
        line=step.line,
        script=list(job.scenes.values_list("line", flat=True)),
        product_colour=job.product_colour,
        colour_photos=[photo.position for photo in photos if photo.shows_product_colour],
        person_looks=job.person_looks,
        pose=pose_for(job.product_size),
        note=note or None,
        conversation=_conversation(job, until=step.started_at),
    )
    images = [
        Image(label="The portrait", key=portrait.file),
        *(Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos),
    ]
    choice = _chosen_past_the_safety_filter(
        scene,
        lambda shown, kept: call_model(
            job=job,
            purpose="choose_starting_picture",
            instructions=STARTING_PICTURE_INSTRUCTIONS,
            handoff=handoff.model_copy(update=_colour_photos_kept(handoff.colour_photos, kept)),
            output=starting_picture_choice_for(kept, StartingPictureChoice),
            images=shown,
            pay_once=True,
        ),
        images,
        numbers,
    )
    step.photo = job.photos.get(position=choice.photo)
    step.photo_reason = choice.photo_reason
    step.prompt = choice.prompt
    step.prompt_reason = choice.prompt_reason
    step.save(update_fields=["photo", "photo_reason", "prompt", "prompt_reason"])
    return _keep_starting_picture(step, choice.prompt, [portrait.file, step.photo.file])


# How a product photo is labelled for a model that picks one of them.
PHOTO_LABEL = re.compile(r"Photo (\d+)")


def _chosen_past_the_safety_filter[Choice](
    scene: Scene,
    ask: Callable[[list[Image], list[int]], Choice],
    images: list[Image],
    numbers: list[int],
) -> Choice:
    """The answer of a model that picks a scene's photo from the product photos `numbers`
    among `images`, past its safety filter: the product photos it refuses are left out, at
    least one kept, and the chat is told in red which. `ask` is handed the pictures to show
    and the numbers of the photos it may pick among them."""

    def photo(image: Image) -> int | None:
        found = PHOTO_LABEL.fullmatch(image.label)
        return int(found.group(1)) if found else None

    choice, left_out = past_the_safety_filter(
        lambda shown: ask(shown, [n for n in numbers if n in {photo(i) for i in shown}]),
        images,
        may_leave_out=lambda image: photo(image) is not None,
        fewest_kept=1,
    )
    if left_out:
        post_left_out_by_the_filter(
            scene.job,
            [f"photo {photo(image)}" for image in left_out],
            f"scene {scene.number}'s starting picture was planned",
        )
    return choice


def _colour_photos_kept(colour_photos: list[int], kept: list[int]) -> dict[str, list[int]]:
    """The photos in the ad's colour among those a model is shown, as a handoff update."""
    return {"colour_photos": [number for number in colour_photos if number in kept]}


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
    check's filter refuses to look at is kept unchecked, said in red."""

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
            post_refused_by_the_filter(job, named, "it is kept unchecked: check it yourself")
        return said

    return check


def _during_the_clip(step: SceneStep) -> list[ReferencePhoto]:
    """The shop owner's photos of things that only show during the step's scene's clip, as
    the step started: a clip made with any is made in reference mode."""
    return [row for row in step.reference_photos.all() if row.use == ReferencePhoto.Use.CLIP]


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


def person_and_room(step: SceneStep) -> tuple[ProducedItem, ProducedItem]:
    """The ad's person-in-room picture and its room alone (spec #210 point 12): the room alone
    is the before-picture's room with nothing of any scene in it, for the scenes nobody is
    seen in (spec 2 item 53); the person-in-room picture is the presenter, from the portrait,
    standing straight and still in that room, nothing else happening, for the scenes they are
    seen in. The room is drawn once per ad, the presenter in it once per portrait (a remade
    person is drawn there again), with the picture model ordered as ROOM_ALONE_PROMPT and
    PERSON_IN_ROOM_PROMPT say, and each shown in the chat once. A step whose scene shows the
    presenter in a place of its own also has them drawn there, when they aren't yet
    (`presenter_in_place`). Gives back the ad's person-in-room picture, then its room alone;
    PlaceNotDrawn when one can't be drawn.

    Run again, as after a worker stopped, it pays for nothing already paid for."""
    job = step.scene.job
    before = _before_picture(step)
    with _one_job_at_a_time(PERSON_AND_ROOM_LOCK, job):
        room = latest(job, ProducedItem.Kind.ROOM_ALONE) or _draw_once(
            job, ProducedItem.Kind.ROOM_ALONE, "make_room_alone", ROOM_ALONE_PROMPT, before
        )
        person = ad_place(job, "person_in_room")
        if person is None:
            portrait = latest(job, ProducedItem.Kind.PORTRAIT)
            if portrait is None:
                # Nobody to draw in it: said in red, and the clip is sent no such picture.
                raise PlaceNotDrawn(ProducedItem.Kind.PERSON_IN_ROOM)
            person = _draw_once(
                job,
                ProducedItem.Kind.PERSON_IN_ROOM,
                "make_person_in_room",
                PERSON_IN_ROOM_PROMPT,
                room,
                portrait=portrait,
            )
        show_once(job, [person, room])
        scene = Scene.objects.get(pk=step.scene_id)
        if (
            scene.place is not None
            and step.person_shown == Scene.PersonShown.HAS_FACE
            and own_place(scene, "person_in_room") is None
        ):
            try:
                presenter_in_place(scene)
            except Exception as error:
                raise PlaceNotDrawn(ProducedItem.Kind.SCENE_PERSON_IN_ROOM) from error
    return person, room


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
    drawn = drawn_for(person_shown)
    return drawn, place_of(scene, drawn)


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
    picture it was made from, or edited from, was drawn in."""
    found: ProducedItem | None = place
    while found is not None and found.kind not in _ROOMS:
        found = found.made_from
    return found


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
    """`scene`'s own room, drawn fresh from the description `place` as SCENE_NEW_PLACE_PROMPT
    orders, then, for a person-in-room picture, the presenter standing in it as the ad's is
    drawn (PERSON_IN_ROOM_PROMPT): for that scene only, and its place from now on
    (Scene.place). Gives back what was made, the picture asked for last. Run again, it pays
    for nothing already paid for."""
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if drawn == "person_in_room" and portrait is None:
        raise NoPresenterYet()
    prompt = SCENE_NEW_PLACE_PROMPT.format(place=place.strip())
    paid_for = _paid_calls(job, "draw_scene_room").filter(handoff={"prompt": prompt}).last()
    file = (
        paid_for.output["file"]
        if paid_for is not None and paid_for.output is not None
        else draw_picture(job=job, purpose="draw_scene_room", prompt=prompt)
    )
    room = _kept_place(scene, ProducedItem.Kind.SCENE_ROOM, file, made_from=None, prompt=prompt)
    _set_place(scene, room)
    if drawn == "room":
        return [room]
    person = presenter_in_place(scene)
    _set_place(scene, person)
    return [room, person]


def presenter_in_place(scene: Scene) -> ProducedItem:
    """The presenter, from the portrait as it stands, drawn standing in the room of the
    scene's own place as the ad's is drawn (PERSON_IN_ROOM_PROMPT): for a scene whose place
    is a room alone, or whose presenter picture shows the person as they were. The scene's
    place stays as it is: the picture is of it. NoPresenterYet before the person is made.
    Run again, it pays for nothing already paid for."""
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    if portrait is None:
        raise NoPresenterYet()
    place = newest_own_place(scene)
    assert place is not None, "only a scene with its own place has the presenter drawn in it"
    room = _room_of(place)
    assert room is not None, "every picture of a place shows a room"
    pictures = [portrait.file, room.file]
    person = _edited_once(job, "make_scene_person_in_room", PERSON_IN_ROOM_PROMPT, pictures)
    return _kept_place(
        scene,
        ProducedItem.Kind.SCENE_PERSON_IN_ROOM,
        person,
        made_from=room,
        prompt=PERSON_IN_ROOM_PROMPT,
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


def say_the_place_wasnt_drawn(job: Job, failed: PlaceNotDrawn) -> None:
    """Tell the chat, in red, that one of the ad's pictures of its place, or the presenter in a
    scene's own place, couldn't be drawn: the clip being made is sent no picture of its place
    (cut-and-send says so for its scene), and the next B-roll clip draws it again."""
    logger.error("Job %s: %s", job.pk, failed, exc_info=failed.__cause__)
    post_notice(
        job,
        f"The {PLACE_PICTURE_NAMES[failed.kind]} couldn't be drawn, so the ad doesn't have it "
        "yet. It is drawn again when the next scene showing the product gets its clip.",
        Message.Level.PROBLEM,
    )


def _draw_once(
    job: Job,
    kind: ProducedItem.Kind,
    purpose: str,
    prompt: str,
    room: ProducedItem,
    *,
    portrait: ProducedItem | None = None,
) -> ProducedItem:
    """Have the picture model draw `kind` from the portrait, if given, then `room`, as
    `prompt` orders, unless it was drawn before the worker stopped, and keep it."""
    pictures = [*([portrait.file] if portrait else []), room.file]
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
        made_from=room,
        person=portrait,
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
        post_refused_by_the_filter(
            job,
            named,
            "it is kept unchecked: check it yourself"
            if passes + len(problems) == 0
            else "some of its checks couldn't be made",
        )
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


def _keep_starting_picture(step: SceneStep, prompt: str, pictures: list[str]) -> ProducedItem:
    """Have the picture model make the step's starting picture from `pictures`, as `prompt`
    says, unless it was made before the worker stopped, and keep it as the scene's next
    version."""
    job = step.scene.job
    paid_for = _paid_for_before(job, "make_starting_picture", charged_to=step.tool_call)
    file = (
        paid_for["file"]
        if paid_for
        else edit_picture(
            job=job, purpose="make_starting_picture", prompt=prompt, pictures=pictures
        )
    )
    scene = step.scene
    return ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=_next_version(scene, ProducedItem.Kind.STARTING_PICTURE),
        file=file,
        # The change it was made for, as its version's label: the producer's note.
        change=step.note,
    )


def make_line_audio(step: SceneStep) -> ProducedItem:
    """Have the person's voice say the scene's line: the voice and the line as they were when
    the step started. Gives back the audio, kept as the scene's next version. A B-roll line
    whose audio is too long for any clip is then shortened, or its scene is said to camera
    instead (see `_fit_its_clip`).

    Run again, as after a worker stopped, it pays for nothing already paid for: audio made
    but not kept is kept rather than spoken again."""
    audio = step.produced.first() or _speak_line(step)
    _fit_its_clip(step, audio)
    return audio


def too_long_for_a_clip(step: SceneStep, audio: ProducedItem) -> bool:
    """Whether a B-roll line's audio takes longer to say than any B-roll clip lasts."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    return bool(step.shows) and audio.seconds > MOST_BROLL_SECONDS


def _fit_its_clip(step: SceneStep, audio: ProducedItem) -> None:
    """For a B-roll line whose audio is too long for any clip, before any clip is paid for:
    shorten the line and fact check it again, so its audio is made again. Once it was
    shortened MOST_BROLL_SHORTENINGS times, or if the shorter line fails the fact check, the
    scene is said to camera instead, and the chat says why. Nobody is asked.

    Run again, as after a worker stopped, it pays for nothing already paid for, and does
    nothing once the scene has changed since the step started."""
    if not too_long_for_a_clip(step, audio):
        return
    assert audio.seconds is not None
    scene = Scene.objects.get(pk=step.scene_id)
    if (scene.line, scene.shows) != (step.line, step.shows):
        return
    job = scene.job
    # Shortenings at planning count too. This step's own, paid for before a restart, is
    # used rather than counted.
    shortened = (
        job.model_calls.filter(
            purpose="shorten_line",
            outcome=ModelCall.Outcome.SUCCEEDED,
            handoff__scene=scene.number,
        )
        .exclude(tool_call=step.tool_call)
        .count()
    )
    if shortened >= MOST_BROLL_SHORTENINGS:
        _say_it_to_camera(scene)
        return
    conversation = _conversation(job, until=step.started_at)
    # At the pace this line was really said.
    most_words = math.floor(LONGEST_BROLL_LINE_SECONDS * count_words(scene.line) / audio.seconds)
    line = _shorter_line(job, scene, most_words, conversation, pay_once=True)
    check = _ask_fact_check(
        job,
        [_to_check(scene).model_copy(update={"line": line})],
        conversation,
        pay_once=True,
    )
    if check.decision == "unclear" or check.lines[0].verdict != "ok":
        _say_it_to_camera(scene)
        return
    was = [*scene.shortened_from, scene.line]
    scene.change_line(line)
    scene.shortened_from = was
    scene.save(update_fields=["line", "shortened_from", "status"])


def _say_it_to_camera(scene: Scene) -> None:
    """Have the person say a B-roll scene's line to camera, as it stands, and tell the user
    why: its line is too long for a B-roll clip. A talking scene shows no second
    state, and the scene after it no longer follows the first state it was made from."""
    with transaction.atomic():
        scene.change_line(scene.line, shows="")
        scene.shortened_from = []
        scene.second_state = False
        scene.save(
            update_fields=[
                "shows",
                *BROLL_FIELDS,
                *PLANNED_FOR_THE_ACTION,
                "shortened_from",
                "second_state",
                "status",
            ]
        )
        scene.job.scenes.filter(number=scene.number + 1).update(second_state=False)
        post_notice(
            scene.job,
            f"Scene {scene.number} couldn't be made as a product shot because its line is too "
            "long for a clip, so it will be said to camera instead.",
            Message.Level.INFO,
        )


def _speak_line(step: SceneStep) -> ProducedItem:
    scene = step.scene
    job = scene.job
    voice = step.made_from
    assert voice is not None, "a line's audio step is started with its voice"
    paid_for = _paid_for_before(job, "speak_line", charged_to=step.tool_call)
    file = (
        paid_for["file"]
        if paid_for
        else speak(job=job, purpose="speak_line", voice_id=voice.voice_id, text=step.line)
    )
    return ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.LINE_AUDIO,
        version=_next_version(scene, ProducedItem.Kind.LINE_AUDIO),
        file=file,
        seconds=_seconds(file_store.read(file)),
        made_from=voice,
    )


def transcribe_line_audio(step: SceneStep) -> ProducedItem:
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
        else transcribe(job=job, purpose="transcribe_line", audio_key=audio.file)
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


def make_clip(step: SceneStep) -> ProducedItem:
    """Have the video model animate the starting picture to speak the audio the step was
    started for. Gives back the clip, kept as the scene's next version, and marks the scene
    finished. A scene that shows the product rather than the person talking is made with no
    sound, from its starting picture, in the fewest whole seconds that cover the audio; the
    audio is then laid over its start, and it is kept whole, as long as it was made. A
    talking clip lasts as long as its audio.

    Run again, as after a worker stopped, it pays for nothing already paid for: a clip
    asked for is waited for rather than asked for again, and one fetched is kept rather
    than fetched again."""
    if step.fixes_a_note:
        return _fixed_by_the_agent(step)
    made = step.produced.order_by("id").first()
    if made is None:
        made = _make_clip(step)
    if step.shows and settings.QUALITY_CHECKS:
        return _auto_fixed(step, made)
    return made


def _fixed_by_the_agent(step: SceneStep) -> ProducedItem:
    """A door 2 step: the fixing agent owns the scene, from the shop owner's note, the goal
    and the look's words the step holds, until a try passes the clip judge and has the
    change, or it gives up. Gives the passing try's clip.

    The producer's fix (fix_scene, a step of its own) ends as door 1's does (Julian's Q19,
    one rule for both doors): on a give-up, at the tries cap, or parked on a question for the
    shop owner or on their pause, the last try stays in the ad, every try shown to swap.
    Run again with the owner's answer or after the pause, it carries on from its
    checkpoints."""
    # The agent works the job's steps, so it is imported when it is used, as tasks.py is.
    from agents import fixing_agent
    from jobs import owner_answers  # It imports the watcher, which imports this module.

    before = step.shown_before(ProducedItem.Kind.CLIP)
    owner_answers.take_the_answer(step)
    if agent_fix(step) is None:
        try:
            fixing_agent.fix_scene(
                step,
                door="owner",
                failure=fixing_agent.Failure(
                    note=step.owner_note,
                    ruling=before.ruling if before is not None else None,
                    clip=before,
                    look=step.look_words,
                ),
                goal=step.fix_goal,
            )
        except Exception:
            if _carried_on(step):
                # The last try was in the ad while the fix waited: it stays.
                return _kept_when_stopped(step, None)
            # The agent stopped: the scene keeps its clip from before, never a try it filmed.
            back_to_the_clip_before(step)
            raise
    ended = _ended(step)
    if ended.passed is not None:
        return ended.passed
    assert ended.kept is not None, "a give-up of the producer's fix keeps a clip in the ad"
    return ended.kept


def _auto_fixed(step: SceneStep, first: ProducedItem) -> ProducedItem:
    """The clip a B-roll clip step ends with, its `first` clip judged: one the judge passed,
    couldn't check, or couldn't tell is right (unsure: the shop owner decides, #228), is kept.
    One it failed is handed to the fixing agent at once (door 1), which films the scene again,
    try after try, until the judge passes a try or it gives up; every try is a version of the
    scene. A try the judge passed is the scene's clip. On a give-up the shop owner is shown
    every try to pick from (`say_the_agent_gave_up`): a scene the producer made keeps its last
    try in the ad (Julian, 2026-10-23), and a fix's scene keeps the clip it had before.

    Run again, as after a worker stopped, it pays for nothing already paid for: a clip judged
    is kept, the agent carries on from its checkpoints, and how it ended is read back."""
    if "verdict" not in (first.ruling or {}):
        _judge_clip(step, first)
    if _ruling(first)["verdict"] != "fail":
        return first
    # A fix parked on a question the shop owner answered is opened again with the answer, so
    # the agent carries on below. It watches videos, and the watcher saves video pictures
    # here, so it is imported where it is used.
    from jobs import owner_answers

    owner_answers.take_the_answer(step)
    if agent_fix(step) is None:
        # The agent's module runs the scene's work, so it is imported where it is used.
        from agents import fixing_agent

        try:
            outcome = fixing_agent.fix_scene(
                step,
                door="judge",
                failure=fixing_agent.Failure(ruling=_ruling(first)),
                goal=None,
                cap=fixing_agent.MOST_TRIES,
            )
        except Exception:
            # An agent that can't carry on doesn't fail the step: the first clip is kept.
            logger.exception("The fixing agent stopped at scene %s's clip", step.scene.number)
            return _kept_when_stopped(step, first)
        if outcome.passed is None and outcome.gave_up is None:
            # It stopped before, as a step run again finds it.
            return _kept_when_stopped(step, first)
    ended = _ended(step)
    if ended.passed is not None:
        return ended.passed
    newest = _newest_clip(step.scene)
    assert newest is not None, "the step made its clip"
    return newest


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
    """Whether `step`'s fix was parked and carried on since: with the shop owner's answer to
    its question, or after their pause."""
    from jobs import owner_answers  # It imports the watcher, which imports this module.

    return (
        owner_answers.answered(step)
        or AgentFix.objects.filter(step=step, resumed_at__isnull=False).exists()
    )


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
        row = filmed.scene_row
        was = (scene.line, scene.shows, scene.result)
        scene.change_line(
            row["line"],
            row["shows"],
            broll={field: row[field] for field in ("result", "broll_kind", "person_shown")},
        )
        # A try's new line, or what it shows, was fact checked before it was filmed.
        if (scene.line, scene.shows, scene.result) != was:
            scene.fact_checked = True
            scene.fact_problems = []
    scene.status = Scene.Status.FINISHED
    scene.save()
    if _newest_clip(scene) == kept:
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
    from agents import fixing_agent

    fixing = AgentFix.objects.filter(step=step).first()
    if fixing is None:
        return
    if fixing.finished_at is None:
        fixing_agent.give_up(fixing, STOPPED_BY_A_RESTART)
    if agent_fix(step) is None or fixing.passed_id is not None:
        return
    ended = _ended(step)
    # The step fails, so a clip of its own is no clip of the ad's: only a try, filmed in a step
    # of its own, is. With no try filmed, the scene waits for its clip to be made again, and a
    # question the fix was parked on is withdrawn: no answer can resume a fix whose step failed.
    # A step run again with the shop owner's answer had its own clip in the ad already: it
    # stays there.
    from jobs import owner_answers  # It imports the watcher, which imports this module.

    if (
        ended.kept is not None
        and ended.kept.step_id == step.pk
        and not owner_answers.resuming(step)
    ):
        Scene.objects.get(pk=step.scene_id).plan_again()
        OwnerQuestion.objects.filter(agent_fix=ended, answered_at__isnull=True).delete()


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


def waits_to_be_said_to_camera(scene: Scene) -> bool:
    """Whether a scene the fixing agent gave up on, said to camera since, still waits for a
    clip made for it as it stands: the producer makes it, as before the scene's first clip."""
    return (
        not scene.shows
        and AgentFix.objects.filter(step__scene=scene, said_to_camera=True).exists()
        and not scene.produced.filter(
            kind=ProducedItem.Kind.CLIP, **SceneStep.made_for(scene, through="step__")
        ).exists()
    )


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


def kept_clip(step: SceneStep) -> ProducedItem:
    """The clip a finished B-roll clip step kept: the scene's newest clip from the step, as a
    clip kept from before the last is brought back as."""
    kept = step.scene.produced.filter(kind=ProducedItem.Kind.CLIP, step=step).order_by("id").last()
    assert kept is not None, "a finished clip step kept a clip"
    return kept


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
    scene = Scene.objects.get(pk=step.scene_id)
    if current is not None and not _says_the_line_as_it_stands(scene, current):
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
                problem=next(iter(clip_judge.still_failing(_ruling(clip))), ""),
            )
            for clip in tries
        ],
        in_ad=in_ad,
        picked=ended.picked_clip if ended is not None else None,
        reason=ended.gave_up if ended is not None else "",
        on_offer=in_ad is not None or _as_the_give_up_left_it(step),
    )


def _as_the_give_up_left_it(step: SceneStep) -> bool:
    """Whether the scene of a clip step the fixing agent gave up on still stands as the
    give-up left it, so a try can be swapped in: said to camera with the line it had, or its
    clip from before still in the ad."""
    ended = agent_fix(step)
    if ended is None or ended.passed_id is not None:
        return False
    scene = Scene.objects.get(pk=step.scene_id)
    if ended.said_to_camera:
        return not scene.shows and scene.line == step.line
    made = versions(scene)
    return (
        ended.kept is not None
        and bool(made)
        and made[-1].clip == ended.kept
        and _says_the_line_as_it_stands(scene, ended.kept)
    )


def open_question(scene: Scene) -> OwnerQuestion | None:
    """The scene's open question for the shop owner: the one the fixing agent asked when it
    parked its fix, not answered yet (spec #210 point 23). Its `question` is the agent's
    words, to show on the scene's card; its `agent_fix` and `tool_call` the fix and the
    ask_owner call the answer resumes (`fixing_agent.answer_question`)."""
    return (
        OwnerQuestion.objects.filter(scene=scene, answered_at__isnull=True)
        .select_related("agent_fix", "tool_call")
        .order_by("-asked_at", "-id")
        .first()
    )


def is_parked(fixing: AgentFix) -> bool:
    """Whether the fixing agent's fix is parked on a question the shop owner hasn't
    answered: it ended as a give-up does, and waits."""
    return OwnerQuestion.objects.filter(agent_fix=fixing, answered_at__isnull=True).exists()


def say_the_agent_gave_up(step: SceneStep) -> None:
    """Show the shop owner every try of a B-roll clip the fixing agent gave up on, to pick
    from, by code, never silent: the judge failed each, and the scene keeps its last try, or
    the clip it had before (or, for a give-up from before 2026-10-23, is said to camera). The
    notice ends the step in the chat: nothing else shows for it. A fix parked on a question
    for the shop owner is said in one red line instead, pointing at the scene's card."""
    ended = agent_fix(step)
    assert ended is not None and ended.passed_id is None, "the agent gave up on the step"
    number = step.scene.number
    if ended.paused_at is not None:
        _say_the_fix_paused(step, ended)
        return
    if is_parked(ended):
        _say_the_agent_asked(step, ended)
        return
    capped = ", and the fixing agent filmed as many tries as it may" if ended.ended_by_cap else ""
    tried = len(_clip_tries(step))
    found = (
        f"None of the {tried} tries at scene {number}'s clip both passed the clip judge and "
        "made your change"
        if step.fixes_a_note
        else f"The clip judge found a problem in all {tried} tries at scene {number}'s clip"
    )
    post_notice(
        step.scene.job,
        f"{found}{capped}, so {_in_the_ad_meanwhile(step, ended)}. Watch them, and tap "
        "one to use it instead.",
        Message.Level.INFO,
        step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE),
        tries_of=step.pk,
    )


def _in_the_ad_meanwhile(step: SceneStep, ended: AgentFix) -> str:
    """What a give-up, or a fix parked on a question, left in the ad for the scene, as a
    clause for the chat: "the last try is in the ad", or the like."""
    number = step.scene.number
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
        f"Scene {number}'s fix stopped between tries for your pause, so "
        f"{_in_the_ad_meanwhile(step, ended)} meanwhile.",
        Message.Level.INFO,
        step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE),
    )


def _say_the_agent_asked(step: SceneStep, ended: AgentFix) -> None:
    """The one red line for a fix parked on the agent's question (the wording is Claude's,
    not approved yet): the scene has a question for the shop owner, what is in the ad until
    they answer, and where the question is."""
    number = step.scene.number
    post_notice(
        step.scene.job,
        f"The fixing agent needs something from you for scene {number}'s clip, so "
        f"{_in_the_ad_meanwhile(step, ended)} until it has it. Its question is on scene "
        f"{number}'s card.",
        Message.Level.PROBLEM,
        step=messages.StepSignal(number, Message.StepKind.CLIP, Message.StepState.MADE),
        owner_question=ended.owner_question.pk,
    )


# What a tap of "Use this one" is told when the tries it was under aren't on offer any more:
# the scene has a newer clip, or its line changed since.
TRIES_NOT_ON_OFFER = "Those tries aren't on offer any more."
TRY_IN_THE_AD = "That try is already in the ad."


class TryNotOnOffer(Exception):
    """A tap of "Use this one" on a try that can't be swapped in, saying why."""


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
    if not shown.on_offer:
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    if shown.in_ad == clip:
        raise TryNotOnOffer(TRY_IN_THE_AD)
    if scene.steps.filter(
        kind__in=[SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP],
        status=SceneStep.Status.RUNNING,
    ).exists():
        raise TryNotOnOffer(
            f"Scene {scene.number}'s picture or clip is being made right now. Tap again once "
            "it's ready."
        )
    number = next(version.number for version in versions(scene) if version.clip == clip)
    # A scene said to camera shows what its tries were made for once one is swapped in.
    said_to_camera = shown.on_offer and shown.in_ad is None and not scene.shows
    if not said_to_camera and not go_back_is_free(scene, number):
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    return number


def check_try_to_use(clip: ProducedItem) -> None:
    """Refuse a tap of "Use this one" on `clip`, saying why, unless it can be swapped in."""
    _try_to_use(clip)


def use_try(clip: ProducedItem) -> GoneBack:
    """Swap a try of a clip the judge failed every try of into the ad, as the shop owner
    tapped: its version is gone back to, for nothing, with nothing made, judged or paid for,
    and the chat is shown it, a scene said to camera when the fixing agent gave up showing
    what the try was made for again. The tap is their yes. Refuses as `check_try_to_use` does."""
    assert clip.scene is not None, "a try is a scene's clip"
    return go_back(clip.scene, _try_to_use(clip))


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
            kind__in=[SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP],
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
    if is_parked(ended):
        asked = (
            f"{failed}, and {tried} the fixing agent asked the shop owner a question about it, "
            "shown on the scene's card"
        )
        if _kept_a_try(step, ended):
            asked = f"{asked}, so {_what_is_kept(step)} is in the ad until they answer it."
        else:
            asked = f"{asked}."
        return f"{asked} {_ask_and_hand_back(step, ended)}"
    gave_up = (
        f"{failed}, and the fixing agent filmed as many tries as it may ({filmed}), none passing"
        if ended.ended_by_cap
        else f"{failed}, and the fixing agent gave up after {filmed} tries"
    )
    if _kept_a_try(step, ended):
        return (
            f"{gave_up}, so {_what_is_kept(step)} is in the ad. The chat showed the shop owner "
            f"every try, so they can tap one to use it instead, for nothing.{_assemble_again(step)}"
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


# What the fixing agent asks the shop owner for, as the producer is told it.
WANTS = {
    OwnerQuestion.Wants.VIDEO: "a video",
    OwnerQuestion.Wants.PHOTO: "a photo",
    OwnerQuestion.Wants.WORDS: "an answer in words",
}


def _ask_and_hand_back(step: SceneStep, ended: AgentFix) -> str:
    """What the producer is told of a fix parked on the fixing agent's question (spec #247
    point 11, one asker): the question, and to ask it in the chat and hand the answer back."""
    question = ended.owner_question
    number = step.scene.number
    return (
        f"Parked: the fix needs {WANTS[OwnerQuestion.Wants(question.wants)]} from the shop "
        f'owner. The fixing agent asks: "{question.question}" Ask the shop owner with '
        f"ask_owner, then, once they answer, call fix_scene for scene {number} to hand their "
        "answer back to the fix."
    )


def _carry_it_on(step: SceneStep) -> str:
    """What the producer is told to do with a fix the shop owner's pause parked."""
    return (
        f"After the shop owner's message, call fix_scene for scene {step.scene.number} to carry "
        "it on, or leave it."
    )


def what_the_fix_did(step: SceneStep, clip: ProducedItem) -> str:
    """How the producer's fix (fix_scene, door 2) of `step`'s scene ended, as the producer is
    told it with the step's result: the try that passed; parked on the fixing agent's question
    or on the shop owner's pause; or no try passing, the last try in the ad (Julian's Q19).
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
    if is_parked(ended):
        return (
            f"Scene {number}'s fix waits {after}, so {meanwhile} meanwhile. "
            f"{_ask_and_hand_back(step, ended)}"
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
    """make_clip's clip, made and kept: its first, or its `remake`th remake, moving as `motion`
    says; or, given the `sent` list cut-and-send or the fixing agent built, made from what it
    lists. A B-roll clip is sent what cut-and-send builds for it (`broll_sent_list`) unless
    given one, never a starting picture, and keeps that sent list before it is shown
    anywhere."""
    scene = step.scene
    job = scene.job
    picture, audio = step.picture, step.made_from
    assert audio is not None, "a clip step starts with its audio"
    assert picture is not None or step.shows, "a talking clip is made from its picture"
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    if step.note and not step.motion_prompt and not remake and sent is None:
        _rewrite_motion_prompt(step)
    handoff: ClipHandoff | BrollClipHandoff
    if step.shows:
        _too_long_for_a_broll_clip(audio.seconds)
        if sent is None:
            sent = broll_sent_list(step, motion=motion)
        handoff = sent_handoff(step, audio, sent)
    else:
        handoff = _clip_handoff(step, picture, audio)
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
        video_id = _clip_asked_for(
            step, handoff, making, collecting, remake=remake
        ) or _ask_for_clip(job, making, handoff)
        file = collect_clip(job=job, purpose=collecting, video_id=video_id, when_slow=tell_its_slow)
    # The talking video model makes a clip as long as the audio it speaks. A B-roll clip is
    # kept whole, as long as it was made, with the audio laid over its start.
    seconds = audio.seconds
    if isinstance(handoff, BrollClipHandoff):
        file, seconds = _with_the_voice(file, audio)
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


def broll_sent_list(step: SceneStep, *, motion: str = "") -> library.SentList:
    """What a B-roll clip of `step` is sent (spec #210 points 13-17), built by cut-and-send,
    never with a starting picture: the scene's planned pictures and piece, with the first-send
    writer's prompt; with `motion`, the words of the fixing agent's try, labelled by code; for
    a clip made from a note, the references of the clip it improves on, with the prompt
    written again from the note; for a new line, what the scene's clip was sent
    (`_sent_for_a_new_line`). The ad's presenter in the room and its room alone are
    drawn first, once per ad, as the clip is sent one of them.

    Run again, as after a worker stopped, it pays for nothing already paid for."""
    job = step.scene.job
    try:
        person_and_room(step)
    except PlaceNotDrawn as not_drawn:
        # Sent no picture of its place, as cut-and-send says for the scene.
        say_the_place_wasnt_drawn(job, not_drawn)
    if step.note:
        return _sent_to_improve(step)
    kept = None if motion else _sent_for_a_new_line(step)
    if kept is not None:
        return kept
    sending = _planned_sending(step)
    _keep_the_cut(step, sending)
    if motion:
        return cut_and_send.labelled_sent_list(job, sending, motion)
    return cut_and_send.first_sent_list(step, sending)


def _keep_the_cut(step: SceneStep, sending: cut_and_send.Sending) -> None:
    """Keep on the clip step, until it finishes, what the producer is told of the cut of its
    clip to the video API's limits (agents.hard_list.cut_answer): its finished answer says it.
    Blank when nothing was cut."""
    from agents import hard_list

    step.result = hard_list.cut_answer(step.scene.number, sending)
    step.save(update_fields=["result"])


def _planned_sending(step: SceneStep) -> cut_and_send.Sending:
    """What cut-and-send sends a clip of `step` from its plan: the scene's planned pictures and
    piece, then the shop owner's things it shows (`owners_things_picks`)."""
    return cut_and_send.planned_sending(step, also=owners_things_picks(step))


def owners_things_picks(step: SceneStep) -> list[ScenePicture]:
    """The tier 2 picks of the clean pictures of the shop owner's things a clip of `step`
    shows (_owners_things_sent), from the library, picked for what the clip acts on, sent
    after the planner's or the fixing agent's picks. Not approved yet: the owner's things go
    as "content" picks (their `for`)."""
    job = step.scene.job
    part = owner_parts.owner_part(step.scene)
    things = []
    for row in _owners_things_sent(step):
        if part is not None and reference_photos.same_thing(row.thing, part.what):
            # The owner part's photo enters the library as the part's picture (#234).
            continue
        drawn = clean_picture(step.scene, row)
        item = library_items.add_owner_photo(job, row.file, drawn, f"the shop owner's {row.thing}")
        if item is not None:
            things.append(ScenePicture(number=item.number, for_=OWNER_THINGS_FOR))
    return things


def _owners_things_sent(step: SceneStep) -> list[ReferencePhoto]:
    """The shop owner's photos a B-roll clip of the step is sent, as the step started: those
    of things in the picture and those of things that only show during the clip, alike, as
    the clip has no starting picture to draw any into (spec #210 point 19)."""
    return list(step.reference_photos.all())


# What the shop owner's things that show in a B-roll clip are sent for, as tier-2 picks.
# Not approved yet: one value for every such thing.
OWNER_THINGS_FOR: library.For = "content"


def _sent_to_improve(step: SceneStep) -> library.SentList:
    """What a note's B-roll clip is sent: what the clip it improves on was sent (`_sent_as`),
    with the prompt's words written again from the note, labelled by code; for a scene with
    no clip yet, the scene's planned references labelled with them."""
    made = versions(step.scene)
    improves = made[-1].clip if made else None
    return _sent_as(step, improves, step.motion_prompt) or cut_and_send.labelled_sent_list(
        step.scene.job, _planned_sending(step), step.motion_prompt
    )


def _sent_for_a_new_line(step: SceneStep) -> library.SentList | None:
    """What a B-roll clip of a new line is sent: what the scene's newest clip was (`_sent_as`),
    for the line's new seconds (spec #247 point 9: a new line keeps the pictures), when that
    clip was made for the scene as it stands but for its line. None for any other clip, such
    as the scene's first or one for a scene that shows something else now: it is sent a first
    send."""
    made = versions(step.scene)
    newest = made[-1].clip if made else None
    if newest is None or newest.step is None or newest.step.line == step.line:
        return None
    but_the_line = {
        key: value
        for key, value in SceneStep.made_for(step.scene, "step__").items()
        if key != "step__line"
    }
    if not ProducedItem.objects.filter(pk=newest.pk, **but_the_line).exists():
        return None
    return _sent_as(step, newest)


def _sent_as(
    step: SceneStep, clip: ProducedItem | None, words: str = "", *, as_it_was: bool = False
) -> library.SentList | None:
    """A B-roll clip of `step` sent as `clip` was, with `words` in place of its prompt's when
    given: `clip`'s sent list, while the scene would still send it (`_still_sent`) or
    `as_it_was`; else the scene's planned references, cut again, labelled by code with the
    words, or with those `clip` was sent. None with neither a list nor words to go on."""
    job = step.scene.job
    sent = library.sent_list_of(clip) if clip is not None else None
    if sent is not None and clip is not None and (as_it_was or _still_sent(step, clip)):
        return cut_and_send.relabelled(job, sent, words) if words else sent
    words = (
        words
        or (cut_and_send.words_of(sent.prompt) if sent is not None else "")
        or (motion_of(clip) if clip is not None else "")
    )
    if not words:
        return None
    return cut_and_send.labelled_sent_list(job, _planned_sending(step), words)


def _still_sent(step: SceneStep, clip: ProducedItem) -> bool:
    """Whether the scene would still send what `clip` was sent: its step was made for the
    scene's references as they stand (what it excludes and its place among them) and with the
    shop owner's photos `step` shows."""
    made = clip.step
    if made is None:
        return False
    if {row.pk for row in made.reference_photos.all()} != {
        row.pk for row in _owners_things_sent(step)
    }:
        return False
    return ProducedItem.objects.filter(
        pk=clip.pk, **SceneStep.references_made_for(step.scene, "step__")
    ).exists()


def sent_handoff(step: SceneStep, audio: ProducedItem, sent: library.SentList) -> BrollClipHandoff:
    """The video model's handoff for a B-roll clip of `step` sent `sent`: its pictures in
    order, its pieces as reference videos, its prompt, and the fewest whole seconds that cover
    the line's `audio`."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    _too_long_for_a_broll_clip(audio.seconds)
    videos = dict(step.scene.job.videos.values_list("position", "file"))
    return BrollClipHandoff(
        example_pictures=[picture.file for picture in sent.pictures],
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
        seconds=_broll_clip_seconds(audio.seconds),
        prompt=sent.prompt,
    )


def _too_long_for_a_broll_clip(audio_seconds: float) -> None:
    """Raise ClipFailed for a line longer than the longest B-roll clip, before anything is
    paid for."""
    if audio_seconds > MOST_BROLL_SECONDS:
        raise ClipFailed(
            f"its line takes {round(audio_seconds, 1):g} seconds to say, and the B-roll video "
            f"model makes clips of at most {MOST_BROLL_SECONDS} seconds"
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
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
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


def go_back(scene: Scene, number: int) -> GoneBack:
    """Make the scene's version `number` its newest again, as a new top version, the
    original too (decision 8, Julian 2026-10-16 22:40): its starting picture brought back,
    free, and its clip too when it still says the scene's line as it stands, so nothing is
    made, checked or paid for. When the line changed since, only the picture is brought back
    and the scene waits for a clip made from it; a scene showing the product, which has no
    picture, waits for a clip made from its sent list. The scene's photos in use become those the
    version was made with, so a photo dropped is a version gone back to. The chat is shown
    what was brought back. A try of a scene the fixing agent gave up on, said to camera since,
    has the scene show what the try was made for again. Refuses a version the scene doesn't
    have, a talking one with no picture whose clip no longer says the line, and while the scene's
    picture or clip is being made."""
    with transaction.atomic():
        Scene.objects.select_for_update().get(pk=scene.pk)
        # Whether the clip still says the line is read from the scene as it now stands.
        scene.refresh_from_db()
        if scene.steps.filter(
            kind__in=[SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP],
            status=SceneStep.Status.RUNNING,
        ).exists():
            raise SceneBusy(f"scene {scene.number}'s picture or clip is still being made")
        return _bring_back(scene, number)


def _bring_back(scene: Scene, number: int) -> GoneBack:
    """go_back's work, the scene locked and read as it stands, in its transaction."""
    with transaction.atomic():
        shown = versions(scene)
        if not 1 <= number <= len(shown):
            raise NoSuchVersion(
                f"scene {scene.number} has versions 1 to {len(shown)}, not {number}"
            )
        earlier = shown[number - 1]
        if earlier.clip is not None:
            _shown_again_for(scene, earlier.clip)
        free = earlier.clip is not None and _says_the_line_as_it_stands(scene, earlier.clip)
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
        if scene.job.session is not None:
            messages.add(
                scene.job.session,
                role=Message.Role.AGENT,
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
        return GoneBack(top, clip_to_make=not free)


def go_back_is_free(scene: Scene, number: int) -> bool:
    """Whether going back to the scene's version `number` would cost nothing: its clip still
    says the scene's line as it stands, so it is brought back with its picture."""
    made = versions(scene)
    if not 1 <= number <= len(made):
        raise NoSuchVersion(f"scene {scene.number} has versions 1 to {len(made)}, not {number}")
    clip = made[number - 1].clip
    return clip is not None and _says_the_line_as_it_stands(scene, clip)


def _says_the_line_as_it_stands(scene: Scene, clip: ProducedItem) -> bool:
    """Whether `clip` still says the scene's line, in the person's voice, and was made for
    what the scene shows: the clip the ad is assembled from, if it were the newest."""
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


def _rewrite_motion_prompt(step: SceneStep) -> None:
    """Write a B-roll clip step's video prompt again from its note, keeping it on the step: a
    model told the same rules as the one that wrote the scene's prompts, and that the
    references of the clip it improves on stay (spec #210 point 21), is handed the prompt that
    clip was sent, the scene and the note, and shown the pictures it was sent, with their
    labels. The producer never writes a prompt (Julian 2026-10-11 21:00).

    Run again, as after a worker stopped, it pays for nothing already paid for: the answer for
    the same handoff is answered from its record."""
    job = step.scene.job
    during = _during_the_clip(step)
    # A B-roll clip has no starting picture (spec #210 point 19): it is sent what the clip it
    # improves on was, cut again when the scene would no longer send that, which it is then
    # shown as its own pictures.
    sent = _sent_to_improve(step)
    instructions = clip_from_references_instructions(step.broll_kind)
    handoff = ClipFromReferencesHandoff(
        **_note_scene(step),
        motion_prompt=cut_and_send.words_of(sent.prompt),
        conversation=_conversation(job, until=step.started_at),
    )
    shown = [
        Image(label=f"Image {picture.image}: {picture.label}", key=picture.file)
        for picture in sent.pictures
    ]
    if during:
        rules = reference_photos.during_clip_rules([row.thing for row in during])
        instructions = f"{instructions}\n{rules}"
    rewrite = call_model(
        job=job,
        purpose="rewrite_clip_prompt",
        instructions=instructions,
        handoff=handoff,
        output=ClipFromNoteChoice,
        images=shown,
        pay_once=True,
    )
    step.motion_prompt = rewrite.motion_prompt
    step.save(update_fields=["motion_prompt"])


def _note_scene(step: SceneStep) -> dict[str, Any]:
    """The scene and the note, as a rewrite of its clip's video prompt is handed them."""
    return {
        "scene": step.scene.number,
        "line": step.line,
        "shows": step.shows,
        "broll_kind": step.broll_kind,
        "person_shown": step.person_shown,
        "usage": step.usage,
        "result": step.broll_result,
        "note": step.note,
    }


def _ask_for_clip(job: Job, making: str, handoff: ClipHandoff | BrollClipHandoff) -> str:
    """Ask the video model for the clip `handoff` describes, and pay for it. Gives its id."""
    if isinstance(handoff, BrollClipHandoff):
        return submit_broll_clip(
            job=job,
            purpose=making,
            starting_picture_key=handoff.starting_picture,
            example_picture_keys=handoff.example_pictures,
            seconds=handoff.seconds,
            prompt=handoff.prompt,
            reference_videos=handoff.reference_videos,
        )
    return submit_clip(
        job=job,
        purpose=making,
        picture_key=handoff.picture,
        audio_key=handoff.audio,
        seconds=handoff.seconds,
        motion_prompt=handoff.motion_prompt,
    )


def _clip_purposes(step: SceneStep) -> tuple[str, str]:
    """What a scene's clip is asked for and collected as: talking clips and B-roll ones are
    each made by their own video model."""
    if step.shows:
        return "make_broll_clip", "collect_broll_clip"
    return "make_talking_clip", "collect_talking_clip"


def _clip_handoff(
    step: SceneStep, picture: ProducedItem | None, audio: ProducedItem
) -> ClipHandoff:
    """What the talking video model is asked for: a clip of the scene's starting picture
    speaking the audio. A B-roll clip's is `sent_handoff`'s."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    assert not step.shows, "a B-roll clip is sent its sent list (sent_handoff)"
    assert picture is not None, "a talking scene's clip is made from its picture"
    return ClipHandoff(
        picture=picture.file,
        audio=audio.file,
        # The talking video model makes the clip as long as the audio, whatever it is asked
        # for, so this is only the seconds it is billed for: at least the shortest clip a
        # handoff takes.
        seconds=max(audio.seconds, LEAST_CLIP_SECONDS),
        motion_prompt=talking_motion_prompt(step.scene.job.product_size),
    )


def _broll_clip_seconds(audio_seconds: float) -> int:
    """How many seconds to ask for a B-roll clip covering `audio_seconds` of its line: the
    video model makes only whole seconds, at least LEAST_BROLL_SECONDS. A hair over a whole
    second, as a measurement can be, isn't counted as another second."""
    return max(LEAST_BROLL_SECONDS, math.ceil(audio_seconds - 1e-6))


def clip_estimate_usd(audio_seconds: float, *, broll: bool, piece_seconds: float = 0.0) -> Decimal:
    """What a scene's clip covering `audio_seconds` of its line is billed, before it is made:
    a B-roll clip's whole seconds and the `piece_seconds` of video it is sent, each at the
    B-roll video model's price per second, as it is billed (gateway.submit_broll_clip); a
    talking clip's audio at the talking one's (see _clip_handoff)."""
    making = "make_broll_clip" if broll else "make_talking_clip"
    seconds = (
        _broll_clip_seconds(audio_seconds) + piece_seconds
        if broll
        else max(audio_seconds, LEAST_CLIP_SECONDS)
    )
    return catalog.video_cost_usd(catalog.MODEL_FOR_PURPOSE[making], seconds)


def _with_the_voice(file: str, audio: ProducedItem) -> tuple[str, float]:
    """A silent clip kept whole with the audio laid over its start, then silence to its
    end, kept in the file store, and how long it lasts. Raises ClipFailed if the clip is
    shorter than the audio: the line would run on past the picture."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    with tempfile.TemporaryDirectory() as folder:
        silent = Path(folder) / "silent.mp4"
        silent.write_bytes(file_store.read(file))
        voice = Path(folder) / "voice"
        voice.write_bytes(file_store.read(audio.file))
        seconds = assembly.seconds_of(silent)
        if seconds < audio.seconds - assembly.CLIP_SHORT_BY_AT_MOST_SECONDS:
            raise ClipFailed(
                f"it came back {round(seconds, 1):g} seconds long, shorter than the line's "
                f"{round(audio.seconds, 1):g} seconds of audio"
            )
        voiced = Path(folder) / "clip.mp4"
        seconds = assembly.lay_voice_over(silent, voice, voiced)
        return file_store.save("clip.mp4", voiced.read_bytes()), seconds


def _clip_asked_for(
    step: SceneStep,
    handoff: ClipHandoff | BrollClipHandoff,
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


def assemble_ad(
    job: Job, scenes: list[tuple[ProducedItem, ProducedItem]], music: ProducedItem
) -> ProducedItem:
    """Put the finished ad together from each scene's clip and the transcript of the audio
    it speaks, in the order the scenes play, and the music: each talking scene's clip cut to
    where its words are said and each one showing the product kept whole, the next line said
    over its end (the early cut), then joined, with the music under the voice, captions of
    each line as written, timed as it was heard, and each scene's overlay while its picture
    plays. Gives back the ad, kept as the job's next version. Costs nothing: no model is
    called."""
    measured = []
    timed = []
    for clip, transcript in scenes:
        assert clip.scene is not None and clip.seconds is not None, "a clip is a scene's, measured"
        audio = clip.made_from
        assert audio is not None and audio.seconds is not None, "a clip speaks measured audio"
        measured.append(
            (
                clip.scene.number,
                clip.pk,
                clip.seconds,
                transcript.words,
                clip.scene.overlay,
                bool(clip.scene.shows),
                audio.seconds,
            )
        )
        timed.append(assembly.timed_script(clip.scene.line, transcript.words))
    planned = assembly.cuts(measured)
    drawn = assembly.captions(planned, timed)
    # ffmpeg reads and writes files on this machine, and the clips are in the file store.
    with tempfile.TemporaryDirectory() as folder:
        parts = []
        for (clip, _), cut in zip(scenes, planned, strict=True):
            path = Path(folder) / f"scene-{cut.scene}.mp4"
            path.write_bytes(file_store.read(clip.file))
            parts.append((path, cut))
        under = Path(folder) / "music"
        under.write_bytes(file_store.read(music.file))
        ad = Path(folder) / "ad.mp4"
        assembly.join(parts, ad, music=under, drawn=drawn)
        file = file_store.save("ad.mp4", ad.read_bytes())
    last = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).aggregate(last=Max("version"))
    return ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.FINISHED_AD,
        version=(last["last"] or 0) + 1,
        file=file,
        made_from=music,
        seconds=planned[-1].end,
        cuts=[asdict(cut) for cut in planned],
        captions=[asdict(caption) for caption in drawn],
    )


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
