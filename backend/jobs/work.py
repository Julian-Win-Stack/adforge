"""The work the producer's tools do on a job: reading its page, planning it, making its
person, running the planning checks, making the music, making each scene and assembling the
finished ad."""

import contextvars
import io
import json
import logging
import math
import mimetypes
import re
import tempfile
import wave
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

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
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    BrollClipHandoff,
    ClipFailed,
    ClipHandoff,
    Handoff,
    Image,
    Judgement,
    MusicHandoff,
    UnusableReply,
)

from . import (
    assembly,
    auto_fix,
    clip_judge,
    firecrawl,
    fix_check,
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
from .models import (
    BROLL_FIELDS,
    Fix,
    FixCheck,
    Job,
    ProducedItem,
    ProductPhoto,
    ProductVideo,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from .notices import post_notice
from .picture_check import (
    ASKS,
    FAILS_TO_FAIL,
    MOST_REDRAWS,
    REDO_RULES,
    STARTING_PICTURE_CHECK,
    BrollRedoHandoff,
    PictureKeptFailing,
    Redo,
    StartingPictureCheckHandoff,
    StartingPictureVerdict,
)
from .planning import (
    PLAN_INSTRUCTIONS,
    ChatMessage,
    PlanHandoff,
    ProducerDecision,
    producer_decision_for,
)
from .reference_photos import ReferencePhotoUnusable
from .scenes import (
    BEFORE_PICTURE_INSTRUCTIONS,
    BEFORE_PICTURE_JOB,
    BROLL_PICTURE_INSTRUCTIONS,
    MAIN_PHOTO_JOB,
    MOST_NEEDS_PHOTOS,
    PORTRAIT_JOB,
    STARTING_PICTURE_INSTRUCTIONS,
    BeforePictureHandoff,
    BeforePicturePlan,
    BrollPictureChoice,
    BrollPictureHandoff,
    BrollPromptHandoff,
    BrollSceneToDraw,
    ClipFromJudgeHandoff,
    ClipFromNoteChoice,
    ClipFromNoteHandoff,
    JudgedTry,
    NeedPhoto,
    PictureJob,
    StartingPictureChoice,
    StartingPictureHandoff,
    broll_prompt_instructions,
    broll_video_prompt,
    clip_from_judge_instructions,
    clip_from_note_instructions,
    need_job,
    photos_for_needs,
    pose_for,
    starting_picture_choice_for,
    talking_motion_prompt,
    with_nothing_made_up,
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
is the page actually read.
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
    However little is kept is used. A failed copy call posts a notice and is raised: the
    page isn't read."""
    if download.markdown is not None:
        given = page_text.shorten_links(download.markdown)
        on_the_page = page_text.markdown_to_text(download.markdown)
    else:
        given = on_the_page = product_page.words
    try:
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
    except (UnusableReply, OutsideServiceDown) as error:
        post_notice(
            job,
            f"Picking this product's own text out of the page failed ({error}), so the page "
            "wasn't read and nothing was kept from it. Ask to read it again.",
            Message.Level.PROBLEM,
        )
        raise
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
    A failed copy call is raised, as by copy_page_text."""
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        picking = pool.submit(context.run, _closing_its_connection, pick_photos, job, read)
        text = copy_page_text(job, read.download, product_page, read.record)
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
        picked, left_out = _picked_past_the_safety_filter(pick, images, shown)
    except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
        notices.append(
            (
                f"Picking this product's photos off the page failed ({error}). {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, notices)
    if left_out:
        notices.append((_left_out_by_the_filter(left_out), Message.Level.INFO))
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
    return Picked(
        [link.url for link in links],
        notices,
        frozenset(link.url for link in links if link.has_face),
        unnoted=frozenset(link.url for link in links if link.has_face is None),
        videos=videos,
        as_given={link.url: link.as_given for link in links if link.as_given != link.url},
    )


def _picked_past_the_safety_filter(
    pick: Callable[[list[Image], photos.ShownLines], photos.PickedPhotos],
    images: list[Image],
    shown: photos.ShownLines,
) -> tuple[photos.PickedPhotos, list[str]]:
    """The picker's answer, and the tiles of the grids left out to get it. A model's safety
    filter refuses a whole call over one picture it objects to, often wrongly, so when it
    refuses, the grids beside the screenshot are left out one at a time, last first, and
    then all together. Raises BlockedBySafetyFilter when it refuses even the screenshot."""
    try:
        return pick(images, shown), []
    except BlockedBySafetyFilter:
        pass
    grids = [image for image in images if not SCREENSHOT_PART.match(image.label)]
    tries = [[grid] for grid in reversed(grids)]
    if len(grids) > 1:
        tries.append(grids)
    for left in tries:
        tiles = [tile for grid in left for tile in picker_grids.tiles_of(grid.label)]
        try:
            picked = pick(
                [image for image in images if image not in left],
                picker_grids.without_tiles(shown, tiles),
            )
        except BlockedBySafetyFilter:
            if left is tries[-1]:
                raise
            continue
        return picked, tiles
    raise AssertionError("unreachable: the last try raises")  # pragma: no cover


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
    job.status = Job.Status.PAGE_READ
    job.save(update_fields=["page_text", "page_text_full", "page_html_key", "status"])


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
    merge_copies: bool = False,
    faces: frozenset[str] | None = None,
    unnoted: frozenset[str] = frozenset(),
    as_given: dict[str, str] | None = None,
) -> list[SkippedPhoto]:
    """Download and keep each photo, after any the job already has, up to MAX_PHOTOS. With
    `merge_copies`, a photo that is a copy of one already kept, at another size or under
    another name, is left out, and the one kept has a face if either does. `faces` are the
    links the picker marked with a face; without them, or for the `unnoted` links it didn't
    note, each photo kept is given its Face note by a call of its own. A link that can't be
    fetched is fetched `as_given` instead, when that names another link for it. Gives back
    each one that was skipped, with why."""
    # A read run again after a crash starts the page's photos afresh, so each is kept once.
    # Photos the user attached are theirs, and stay.
    job.photos.exclude(source_url="").delete()
    saved = job.photos.aggregate(last=Max("position"))["last"] or 0
    copies = photos.Copies()
    kept: list[ProductPhoto] = []
    skipped = []
    for url in urls:
        if len(kept) >= page.MAX_PHOTOS:
            break
        if merge_copies and (copy := copies.kept_at_link(url)) is not None:
            _merge_face(kept[copy], url, faces)
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
        if merge_copies and (copy := copies.copy_of(url, photo.content)) is not None:
            _merge_face(kept[copy], url, faces)
            continue
        saved += 1
        has_face = url in faces if faces is not None and url not in unnoted else None
        kept.append(
            keep_photo(
                job, saved, photo.content, photo.content_type, source_url=url, has_face=has_face
            )
        )
    return skipped


# How far, in seconds, the reader's second may be from the picked frame it names: the
# frames are shown to it labelled with their seconds, rounded.
MOST_SECONDS_OFF = 0.5


def save_video_pictures(
    job: Job,
    video: ProductVideo,
    kept: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
) -> list[ProductPhoto]:
    """Keep each moment the reader `kept` from the video ({second, shows, why}) as a video
    picture: the picked frame at that second, of the video's picked `frames` ({key, second}),
    as a product photo after every photo the job has, noted with what it `shows`. A moment
    with no picked frame near its second, or whose frame is a copy of a photo from the page
    or of a picture already kept, is left out. Each is given its Face note by a call of its
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
) -> ProductPhoto:
    """Store a product photo with the job, with its Face note, or with None, noted by a call
    of its own. An uploaded photo has no source link. A video picture is kept with the video
    it was cut `from_video`, its second there and its one-line `note`."""
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
    )


def note_face(job: Job, source: str, key: str) -> bool:
    """Whether the photo in the file store at `key` shows a stranger's face, by a call of its
    own: for a photo the picker never saw. `source` names it by where it came from, so a page
    read again pays for nothing twice though its photos are stored again under new keys. A
    failed call counts as a face, the safe side: such a photo is only used when no other shows
    what's needed. Nothing is said about it."""
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
    except UnusableReply, OutsideServiceDown, UnreadableImage:
        return True
    return noted.has_face


def plan(job: Job) -> ProducerDecision:
    """Have the planner plan the ad from the page, the product photos and the conversation,
    and keep the plan. A decision to ask the user keeps nothing."""
    photos = list(job.photos.all())
    shown = _photos_to_show(job, photos)
    decision = call_model(
        job=job,
        purpose="plan_ad",
        instructions=PLAN_INSTRUCTIONS,
        handoff=plan_handoff(job, job.page_text, len(photos)),
        output=producer_decision_for(len(photos)),
        images=[Image(label=f"Photo {photo.position}", key=photo.file) for photo in shown],
    )
    planned = decision.plan
    if planned is None:
        return decision
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
                **scene.broll_details(),
            )
            for number, scene in enumerate(planned.scenes, start=1)
        )
        job.product_name = planned.product_name
        job.product_colour = planned.product_colour
        job.product_size = planned.product_size
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
                "person_gender",
                "person_looks",
                "person_voice",
                "script_format",
                "status",
            ]
        )
        job.photos.filter(position__in=planned.colour_photos).update(shows_product_colour=True)
    return decision


def plan_handoff(job: Job, page_text: str, photo_count: int) -> PlanHandoff:
    """What the planner is handed about the ad, with the product's own text from its page:
    the planner's, and the video reader's, which thinks through the same plan."""
    return PlanHandoff(
        product_url=job.product_url,
        page_text=page.for_model(page_text),
        target_seconds=job.target_seconds,
        photo_count=photo_count,
        conversation=_conversation(job),
    )


def _photos_to_show(job: Job, photos: list[ProductPhoto]) -> list[ProductPhoto]:
    """The job's photos a model can be shown in one call: at most clip_judge.MOST_PICTURES,
    the page's first, then the shop owner's. When some are left out, the chat is told once
    which."""
    shown = sorted(photos, key=lambda photo: not photo.source_url)[: clip_judge.MOST_PICTURES]
    left_out = [f"{photo.position}" for photo in photos if photo not in shown]
    if not left_out:
        return shown
    listed = left_out[0] if len(left_out) == 1 else f"{', '.join(left_out[:-1])} and {left_out[-1]}"
    text = (
        f"The ad has {len(photos)} product photos, more than the {clip_judge.MOST_PICTURES} a "
        f"model can be shown at once, so photo{'s' if len(left_out) > 1 else ''} {listed} "
        f"{'are' if len(left_out) > 1 else 'is'} left out when it's planned or a line is "
        "rewritten."
    )
    if all(warning["text"] != text for warning in job.warnings):
        post_notice(job, text, Message.Level.INFO)
    return shown


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


def _measure_voice(job: Job, voice: ProducedItem) -> None:
    """Measure how fast the voice really speaks by having it read the whole script: no
    speaking speed is assumed.

    Speech paid for before a worker stopped is read back rather than spoken again. It was
    spoken from this same script: making the person is what runs again first after a
    restart, so nothing gets a turn in between to rewrite a line."""
    script = " ".join(job.scenes.values_list("line", flat=True))
    paid_for = _paid_for_before(job, "measure_voice")
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
    # the ad's colour, and those the scenes need. A script where the person talks
    # throughout is checked on text alone.
    needed = {
        number
        for scene in job.scenes.filter(number__in=showing)
        for need in scene.needs
        for number in need["photos"]
    }
    photos = (
        job.photos.filter(Q(shows_product_colour=True) | Q(position__in=needed)).order_by(
            "position"
        )
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
        ),
        output=fact_check_for([line.scene for line in lines], showing=showing),
        images=[Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos],
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
    scene that shows something is rewritten seeing the job's photos, to say what it needs."""
    photos = list(job.photos.all())
    colour_photos = [photo.position for photo in photos if photo.shows_product_colour]
    rewrite = call_model(
        job=job,
        purpose="rewrite_line",
        instructions=REWRITE_INSTRUCTIONS,
        handoff=RewriteHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            photo_count=len(photos),
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
            scene.number, shows_something=bool(scene.shows), photo_count=len(photos)
        ),
        images=[
            Image(label=f"Photo {photo.position}", key=photo.file)
            for photo in _photos_to_show(job, photos)
        ]
        if scene.shows
        else [],
    )
    scene.fact_problems[-1]["rewritten"] = True
    scene.change_line(rewrite.line, rewrite.shows or "", broll=rewrite.broll_details())
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
    """Make the scene's starting picture: a model picks the product photo that suits the
    line best and writes the prompt, then the picture is made from the portrait and that
    photo. For a scene that shows the product rather than the person talking, the picture
    shows what the scene shows, and the model also writes the clip's motion prompt. Gives
    back the picture, kept as the scene's next version.

    Run again, as after a worker stopped, it pays for nothing already paid for: the choice
    is made from the conversation as it was when the step started, so it is answered from
    its record, and a picture made but not kept is kept rather than made again."""
    made = step.produced.first()
    if made is not None:
        return made
    if step.broll_kind:
        return _make_broll_picture(step)
    # A talking scene, or a B-roll one planned before the plan gave B-roll scenes their
    # labels, as every scene was made before.
    scene = step.scene
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    assert portrait is not None, "the tool refuses a scene whose person isn't made"
    photos = list(job.photos.filter(shows_product_colour=True))
    numbers = [photo.position for photo in photos]
    note = step.note
    failed = the_try_before(step)
    if failed is not None:
        # Drawn again for its fix check: told what the check found still wrong.
        redraw = fix_check.REDRAW_NOTE.format(reason=failed.reason)
        note = f"{note}\n\n{redraw}" if note else redraw
    handoff = StartingPictureHandoff(
        scene=scene.number,
        line=step.line,
        script=list(job.scenes.values_list("line", flat=True)),
        product_colour=job.product_colour,
        colour_photos=numbers,
        person_looks=job.person_looks,
        pose=pose_for(job.product_size),
        note=note or None,
        conversation=_conversation(job, until=step.started_at),
    )
    images = [
        Image(label="The portrait", key=portrait.file),
        *(Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos),
    ]
    choice: StartingPictureChoice
    if step.shows:
        broll = call_model(
            job=job,
            purpose="choose_broll_picture",
            instructions=BROLL_PICTURE_INSTRUCTIONS,
            handoff=BrollPictureHandoff(**handoff.model_dump(), shows=step.shows),
            output=starting_picture_choice_for(numbers, BrollPictureChoice),
            images=images,
            pay_once=True,
        )
        step.motion_prompt = broll.motion_prompt
        choice, prompt = broll, with_nothing_made_up(broll.prompt)
    else:
        choice = call_model(
            job=job,
            purpose="choose_starting_picture",
            instructions=STARTING_PICTURE_INSTRUCTIONS,
            handoff=handoff,
            output=starting_picture_choice_for(numbers, StartingPictureChoice),
            images=images,
            pay_once=True,
        )
        prompt = choice.prompt
    step.photo = job.photos.get(position=choice.photo)
    step.photo_reason = choice.photo_reason
    step.prompt = choice.prompt
    step.prompt_reason = choice.prompt_reason
    step.save(update_fields=["photo", "photo_reason", "prompt", "prompt_reason", "motion_prompt"])
    return _keep_starting_picture(step, prompt, [portrait.file, step.photo.file])


def _make_broll_picture(step: SceneStep) -> ProducedItem:
    """Make a B-roll scene's starting picture: the model writing the scene's prompts, told the
    shared rules and its kind's, picks the main photo from the photos in the ad's colour and
    writes the picture's prompt and the clip's. The picture is made from the main photo, then
    a photo for each of the scene's needs, then the presenter's portrait when the scene shows
    their face. It is checked before any clip is paid for; one that fails is drawn again from
    prompts written to fix what the check found, at most MOST_REDRAWS times. When the last
    one drawn fails too, the chat is shown each picture and why it failed, and
    PictureKeptFailing is raised to stop the run. A fix's picture drawn again because the one
    before it failed its fix check is written as a redo of that one from the start, told
    what the fix check found, and every redo of it after is told that too.

    Run again, as after a worker stopped, it pays for nothing already paid for: each model's
    answer for the same handoff is answered from its record, and each picture drawn is
    reused in turn."""
    scene = step.scene
    job = scene.job
    before = _before_picture(step)
    photos = list(job.photos.filter(shows_product_colour=True))
    numbers = [photo.position for photo in photos]
    images = [Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos]
    pictures = [PictureJob(image=1, job=MAIN_PHOTO_JOB)]
    # The shop owner's things drawn into the picture go first, as clean pictures of only each
    # thing, labelled to win wherever they disagree with another picture (option C, Julian
    # 2026-10-15 16:01): the main photo is then the picture after them.
    owners = [row for row in step.reference_photos.all() if row.use == ReferencePhoto.Use.PICTURE]
    cleans = [clean_picture(step, row) for row in owners]
    things = [row.thing for row in owners]
    if owners:
        images = [
            *(
                Image(label=f"The shop owner's own {thing}", key=clean)
                for thing, clean in zip(things, cleans, strict=True)
            ),
            *images,
        ]
        pictures = [
            *(
                PictureJob(image=number, job=reference_photos.picture_job(thing))
                for number, thing in enumerate(things, 1)
            ),
            PictureJob(image=len(owners) + 1, job=MAIN_PHOTO_JOB),
        ]
    during = [row.thing for row in _during_the_clip(step)]
    owner_rules = reference_photos.writer_rules(things, during, picks_photo=len(numbers) > 1)
    # What the scene needs that the main photo can't show comes from a photo of its own,
    # sent to the picture model after the main photo, never to the video model: sent to it,
    # a shop photo's own scene leaked into the clip (graded #12).
    faces = {photo.position: photo.has_face for photo in job.photos.all()}
    # Picked before the main photo is, so a need's photo the model then picks as the main
    # photo is sent twice, each time with its own job.
    needs = photos_for_needs(step.needs, faces, MOST_NEEDS_PHOTOS)
    for need in needs:
        need_photo = job.photos.get(position=need.photo)
        images.append(Image(label=f"Photo {need.photo}", key=need_photo.file))
        pictures.append(PictureJob(image=len(pictures) + 1, job=need_job(need)))
    portrait = None
    if step.person_shown == Scene.PersonShown.HAS_FACE:
        portrait = latest(job, ProducedItem.Kind.PORTRAIT)
        assert portrait is not None, "the tool refuses a scene whose person isn't made"
        images.append(Image(label="The portrait", key=portrait.file))
        pictures.append(PictureJob(image=len(pictures) + 1, job=PORTRAIT_JOB))
    # Last, so every other picture keeps its number: the place the scene is drawn in.
    images.append(Image(label="The before-picture", key=before.file))
    pictures.append(PictureJob(image=len(pictures) + 1, job=BEFORE_PICTURE_JOB))
    handoff = BrollPromptHandoff(
        scene=scene.number,
        line=step.line,
        script=list(job.scenes.values_list("line", flat=True)),
        shows=step.shows,
        broll_kind=step.broll_kind,
        person_shown=step.person_shown,
        usage=step.usage,
        result=step.broll_result,
        pictures=pictures,
        before_picture=before.text,
        product_colour=job.product_colour,
        colour_photos=numbers,
        person_looks=job.person_looks,
        note=step.note or None,
        conversation=_conversation(job, until=step.started_at),
    )
    # Pictures drawn for the step before the worker stopped, oldest first, so a step run
    # again pays for none of them twice.
    drawn = _pictures_drawn(step)
    redo: tuple[Redo, str] | None = None
    fixing: list[str] = []
    before_it = the_try_before(step)
    if before_it is not None:
        assert step.redraw_of is not None, "a try before is the step's redraw_of's"
        fixing = [before_it.reason]
        redo = (
            Redo(
                picture_prompt=step.redraw_of.prompt,
                video_prompt=step.redraw_of.motion_prompt,
                problems=fixing,
            ),
            before_it.new.file,
        )
    failed: list[tuple[str, list[str]]] = []
    for tries in range(MOST_REDRAWS + 1):
        instructions = broll_prompt_instructions(step.broll_kind)
        if owner_rules:
            instructions = f"{instructions}\n{owner_rules}"
        asked: BrollPromptHandoff = handoff
        shown = images
        if redo is not None:
            # Told what was wrong with the last picture, and shown it.
            instructions = f"{instructions}\n{REDO_RULES}"
            asked = BrollRedoHandoff(**dict(handoff), redo=redo[0])
            shown = [*images, Image(label="The last picture", key=redo[1])]
        choice = call_model(
            job=job,
            purpose="choose_broll_picture",
            instructions=instructions,
            handoff=asked,
            output=starting_picture_choice_for(numbers, BrollPictureChoice),
            images=shown,
            pay_once=True,
        )
        _plan_broll_picture(step, choice, pictures[len(owners) :], needs, portrait is not None)
        if owners:
            step.pictures_sent = [
                *(
                    {**picture.model_dump(), "reference_photo": row.pk}
                    for picture, row in zip(pictures, owners, strict=False)
                ),
                *step.pictures_sent,
            ]
            step.save(update_fields=["pictures_sent"])
        sent = [
            *cleans,
            job.photos.get(position=choice.photo).file,
            *(job.photos.get(position=need.photo).file for need in needs),
            *([portrait.file] if portrait else []),
            before.file,
        ]
        file = (
            drawn[tries]
            if tries < len(drawn)
            else edit_picture(
                job=job,
                purpose="make_starting_picture",
                prompt=(
                    f"{reference_photos.picture_label(things)} {choice.prompt}"
                    if owners
                    else choice.prompt
                ),
                pictures=sent,
            )
        )
        problems = _check_broll_picture(step, file)
        if not problems:
            return _keep_picture(step, file)
        failed.append((file, problems))
        redo = (
            Redo(
                picture_prompt=choice.prompt,
                video_prompt=choice.motion_prompt,
                problems=[*fixing, *problems],
            ),
            file,
        )
    raise PictureKeptFailing(scene.number, failed)


# Which Postgres advisory lock stands for "one clean picture of the shop owner's things drawn
# at a time per ad", with the job's.
CLEAN_PICTURE_LOCK = 3


def clean_picture(step: SceneStep, row: ReferencePhoto) -> str:
    """The clean picture of only `row`'s thing, drawn from the shop owner's photo (Julian
    2026-10-15 17:43) once per ad, by whichever step needs it first, and kept on every scene's
    row of the same photo and thing. It is checked as a starting picture is, beside the photo,
    and drawn again when the check fails, at most MOST_REDRAWS times (D8). Gives its key in the
    file store. Raises ReferencePhotoUnusable when it still fails, or the picture model
    refuses the photo: nothing more is made for the scene, and the shop owner is asked for
    another photo.

    Run again, as after a worker stopped, it pays for nothing already paid for: each picture
    drawn is reused in turn, and each check answered from its record."""
    if row.drawn:
        return row.drawn
    job = step.scene.job
    with _one_job_at_a_time(CLEAN_PICTURE_LOCK, job):
        same = ReferencePhoto.objects.filter(scene__job=job, file=row.file, thing__iexact=row.thing)
        drawn = same.exclude(drawn="").values_list("drawn", flat=True).first()
        if drawn:
            row.drawn = drawn
            return drawn
        prompt = reference_photos.clean_picture_prompt(row.thing)
        paid = [
            str(call.output["file"])
            for call in _paid_calls(job, "make_reference_photo_picture").filter(
                handoff={"prompt": prompt, "pictures": [row.file]}
            )
            if call.output is not None
        ]
        failed: list[list[str]] = []
        for tries in range(MOST_REDRAWS + 1):
            try:
                file = (
                    paid[tries]
                    if tries < len(paid)
                    else edit_picture(
                        job=job,
                        purpose="make_reference_photo_picture",
                        prompt=prompt,
                        pictures=[row.file],
                    )
                )
            except BlockedBySafetyFilter as error:
                raise ReferencePhotoUnusable(
                    f"the picture model refused to draw the {row.thing} from the shop owner's "
                    f"photo ({error}), so nothing more was made for scene {step.scene.number}. "
                    f"Ask them for another photo of the {row.thing}.",
                    thing=row.thing,
                ) from error
            problems = (
                judge_starting_picture(job, file, row.file, about=f"the shop owner's {row.thing}")
                if settings.QUALITY_CHECKS
                else []
            )
            if not problems:
                same.update(drawn=file)
                row.drawn = file
                return file
            failed.append(problems)
        found = " ".join(problems[0] for problems in failed)
        raise ReferencePhotoUnusable(
            f"the clean copy of the {row.thing} drawn from the shop owner's photo failed its "
            f"check all {len(failed)} times it was drawn ({found}), so nothing more was made "
            f"for scene {step.scene.number}. Ask them for another photo of the {row.thing}, "
            "showing it clearly.",
            thing=row.thing,
        )


def _during_the_clip(step: SceneStep) -> list[ReferencePhoto]:
    """The shop owner's photos of things that only show during the step's scene's clip, as
    the step started: a clip made with any is made in reference mode."""
    return [row for row in step.reference_photos.all() if row.use == ReferencePhoto.Use.CLIP]


def say_the_pictures_kept_failing(job: Job, failing: PictureKeptFailing) -> None:
    """Show the chat each starting picture drawn for the scene, and what its check found in
    it, as the run stops: so a judge too strict, or prompts it can't fix, are seen rather
    than kept quietly (Julian 2026-10-10 21:48)."""
    scene, failed = failing.scene, failing.failed
    found = "\n".join(
        f"Picture {number}: {' '.join(problems)}" for number, (_, problems) in enumerate(failed, 1)
    )
    logger.error("Scene %s's starting picture kept failing its check:\n%s", scene, found)
    post_notice(
        job,
        f"Scene {scene}'s starting picture failed its check all {len(failed)} times it was "
        "drawn, so I stopped. This is a problem in the app, not with your product. What the "
        f"check found in each picture:\n{found}",
        Message.Level.PROBLEM,
        carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, file) for file, _ in failed],
        # The chat can tell from it that the scene's picture is no longer being made.
        step=messages.StepSignal(
            scene, Message.StepKind.STARTING_PICTURE, Message.StepState.NOT_MADE
        ),
    )


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


@contextmanager
def _one_job_at_a_time(lock: int, job: Job) -> Iterator[None]:
    """Wait for, then hold while the block runs, the Postgres advisory lock `lock` for `job`,
    so work done under it for the same job runs one at a time. It is held by the database
    connection, not a transaction, so what is done under it is kept as it happens."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", [lock, str(job.pk)])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", [lock, str(job.pk)])


def _plan_broll_picture(
    step: SceneStep,
    choice: BrollPictureChoice,
    pictures: list[PictureJob],
    needs: list[NeedPhoto],
    portrait: bool,
) -> None:
    """Keep what the model writing a B-roll scene's prompts chose with the step: the main
    photo, both prompts and each picture sent with its job."""
    step.photo = step.scene.job.photos.get(position=choice.photo)
    step.photo_reason = choice.photo_reason
    step.prompt = choice.prompt
    step.prompt_reason = choice.prompt_reason
    step.motion_prompt = choice.motion_prompt
    step.way = SceneStep.Way.FROM_PICTURE
    main_photo, *need_photos = (picture.model_dump() for picture in pictures)
    before_sent = need_photos.pop()
    portrait_sent = [need_photos.pop()] if portrait else []
    step.pictures_sent = [
        {**main_photo, "photo": choice.photo},
        *(
            {**picture, "photo": need.photo}
            for picture, need in zip(need_photos, needs, strict=True)
        ),
        *({**picture, "portrait": True} for picture in portrait_sent),
        {**before_sent, "before_picture": True},
    ]
    step.save(
        update_fields=[
            "photo",
            "photo_reason",
            "prompt",
            "prompt_reason",
            "motion_prompt",
            "way",
            "pictures_sent",
        ]
    )


def _pictures_drawn(step: SceneStep) -> list[str]:
    """Each starting picture paid for for `step`, oldest first."""
    calls = _paid_calls(step.scene.job, "make_starting_picture", charged_to=step.tool_call)
    return [call.output["file"] for call in calls if call.output is not None]


def _check_broll_picture(step: SceneStep, picture: str) -> list[str]:
    """What is wrong with a B-roll scene's starting picture: see `judge_starting_picture`.
    Always checked, whatever QUALITY_CHECKS says (Julian 2026-10-15 14:53: "hardcode the
    check in the app to always turn it on")."""
    assert step.photo is not None, "a B-roll picture is drawn from its main photo"
    return judge_starting_picture(
        step.scene.job, picture, step.photo.file, about=f"scene {step.scene.number}"
    )


def judge_starting_picture(job: Job | None, picture: str, photo: str, *, about: str) -> list[str]:
    """What is wrong with the starting picture at `picture`, from a judge shown the picture,
    then the shop photo at `photo` it was drawn from, for how the product is built: each
    problem its failing answers found, when FAILS_TO_FAIL of its ASKS answers
    fail it; nothing when it passes. An ask the safety filter blocks, or whose answer can't
    be had, counts as not checked: the picture is paid for, and failing it would pay for it
    again. `about` names the picture in the log."""
    passes = 0
    problems: list[str] = []
    # Asked until the answers settle it, so a picture two answers pass isn't asked a third.
    for ask in range(1, ASKS + 1):
        if passes > ASKS - FAILS_TO_FAIL or len(problems) >= FAILS_TO_FAIL:
            break
        try:
            verdict = call_model(
                job=job,
                purpose="check_starting_picture",
                instructions=STARTING_PICTURE_CHECK,
                handoff=StartingPictureCheckHandoff(ask=ask),
                output=StartingPictureVerdict,
                images=[
                    Image(label="The picture to judge:", key=picture),
                    Image(label="The shop photo:", key=photo),
                ],
                pay_once=True,
            )
        except (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter) as error:
            logger.warning(
                "Ask %s about %s's starting picture went unchecked: %s", ask, about, error
            )
            continue
        if verdict.verdict == "pass":
            passes += 1
        else:
            assert verdict.problem is not None, "a failing answer says why"
            problems.append(verdict.problem)
    return problems if len(problems) >= FAILS_TO_FAIL else []


def check_fix(step: SceneStep, made: ProducedItem | None) -> FixCheck | None:
    """Check a fix (#131): for a step that made a scene's new starting picture for a picture
    fix, or its new clip for a clip fix, keep whether a judge found the change in `made`,
    shown beside the scene's version before the step. A new starting picture is checked here,
    by asking a judge the complaint back. A new clip was already asked, by the clip judge
    itself, beside the clip the shop owner complained about (Julian 2026-10-14, one judge for
    both): its answers are read from the ruling of the clip its remakes kept (#155), which is
    the step's newest, and nothing is asked here. A new line isn't checked, nor a picture
    fix's clip, the complaint being about the picture, unless the fix has the shop owner's
    photo of a thing that only shows during the clip. Nothing is checked when quality checks
    are switched off. A picture drawn again because the one before it failed this check is
    checked beside the version its first try was (see `to_draw_again`).

    Run again, as after a worker stopped, it gives the verdict kept, or asks the judge
    again with each ask answered from its record, so nothing is paid for twice."""
    fix = step.fix
    if fix is None or made is None or not settings.QUALITY_CHECKS:
        return None
    picture = fix.redo == Fix.Redo.PICTURE and step.kind == SceneStep.Kind.STARTING_PICTURE
    # A picture fix's clip too, when the fix has the shop owner's photo of a thing that only
    # shows during the clip: the picture's check can't see it (design thread 22:42).
    clip = step.kind == SceneStep.Kind.CLIP and (
        fix.redo == Fix.Redo.CLIP or bool(clip_judge.during_the_clip(fix))
    )
    if not (picture or clip):
        return None
    kept = FixCheck.objects.filter(step=step).first()
    if kept is not None:
        return kept
    before_it = the_try_before(step)
    # A redraw is checked against what the shop owner complained about, as its first try was.
    old = before_it.old if before_it is not None else step.shown_before(made.kind)
    if old is None:
        return None
    if picture:
        job = fix.job
        settled = fix_check.settle(
            lambda ask: _ask_about_the_fix(job, fix, ask, old.file, made.file)
        )
    else:
        settled = _the_clip_judges_answer(made)
    return FixCheck.objects.create(
        fix=fix,
        step=step,
        old=old,
        new=made,
        verdict=settled.verdict,
        reason=settled.reason,
        answers=settled.answers,
    )


def the_try_before(step: SceneStep) -> FixCheck | None:
    """For a fix's starting picture drawn again for its fix check, the failed check of the
    picture before it. None for any other step."""
    if step.redraw_of is None:
        return None
    return FixCheck.objects.select_related("old", "new").get(step=step.redraw_of)


def to_draw_again(check: FixCheck | None) -> bool:
    """Whether the fix's new starting picture `check` failed is drawn again by itself, told
    why, rather than shown to the shop owner (Julian 2026-10-17 17:04): a fix plan's picture
    with redraws left, at most fix_check.MOST_REDRAWS after its first."""
    if check is None or check.verdict != FixCheck.Verdict.FAILED or check.what != "picture":
        return False
    step = check.step
    if step.plan_scene_id is None:
        return False
    redraws = 0
    while step.redraw_of is not None:
        step = step.redraw_of
        redraws += 1
    return redraws < fix_check.MOST_REDRAWS


def _the_clip_judges_answer(new: ProducedItem) -> fix_check.Settled:
    """What the clip judge's answers about `new` settled on the shop owner's change, asked
    beside the clip the step's fix was for (clip_judge.complained_about, the same `old`):
    unchecked when it was judged without it, as when its frames couldn't be cut, or not
    judged at all."""
    ruling = new.ruling or {}
    if "compared_with" not in ruling:
        return fix_check.Settled("unchecked", "", [])
    return fix_check.settle_all(clip_judge.owner_change_answers(ruling))


def _ask_about_the_fix(job: Job, fix: Fix, ask: int, old: str, new: str) -> dict[str, Any] | None:
    """Ask the judge once whether the new picture at `new` has the change `fix` asks for,
    shown the old picture at `old` first and, for a fix that takes things from the shop
    owner's photos, each photo after the new picture, named (Julian 2026-10-15 19:34). Gives
    its answer, or None when none could be had: the picture is paid for, and failing it on no
    answer would hold up its clip."""
    images = [
        Image(label="The old picture:", key=old),
        Image(label="The new picture:", key=new),
    ]
    # Only things drawn into the picture: one that shows during the clip is never in a still,
    # and its clip's judge looks for it.
    photos = [photo for photo in fix.photos if photo["use"] == ReferencePhoto.Use.PICTURE]
    during = [str(photo["thing"]) for photo in clip_judge.during_the_clip(fix)]
    if not photos and during:
        return _fix_answer(
            job,
            purpose="check_fix",
            instructions=f"{fix_check.FIX_CHECK}\n\n{fix_check.DURING_THE_ACTION}",
            handoff=fix_check.DuringTheActionHandoff(
                fix=fix.fix, ask=ask, during_the_action=during
            ),
            output=fix_check.FixVerdict,
            images=images,
        )
    if not photos:
        return _fix_answer(
            job,
            purpose="check_fix",
            instructions=fix_check.FIX_CHECK,
            handoff=fix_check.FixCheckHandoff(fix=fix.fix, ask=ask),
            output=fix_check.FixVerdict,
            images=images,
        )
    things = [str(photo["thing"]) for photo in photos]
    return _fix_answer(
        job,
        purpose="check_fix",
        instructions="\n\n".join(
            [
                fix_check.FIX_CHECK,
                fix_check.PHOTO_FIX_QUESTION,
                *([fix_check.DURING_THE_ACTION] if during else []),
            ]
        ),
        handoff=fix_check.PhotoFixCheckHandoff(
            fix=fix.fix, ask=ask, owner_photos=things, during_the_action=during
        ),
        output=fix_check.PhotoFixVerdict,
        images=[
            *images,
            *(
                Image(
                    label=f"The shop owner's photo of their {photo['thing']}:",
                    key=str(photo["file"]),
                )
                for photo in photos
            ),
        ],
    )


def _fix_answer(
    job: Job,
    *,
    purpose: str,
    instructions: str,
    handoff: fix_check.FixCheckHandoff,
    output: type[fix_check.FixVerdict] | type[fix_check.PhotoFixVerdict],
    images: list[Image],
) -> dict[str, Any] | None:
    """One answer of the judge checking a fix, or None when none could be had, whatever went
    wrong, as the clip judge's asks: what it checks is already paid for, and failing it on
    no answer would fail that too."""
    try:
        verdict = call_model(
            job=job,
            purpose=purpose,
            instructions=instructions,
            handoff=handoff,
            output=output,
            images=images,
            pay_once=True,
        )
    except Exception as error:
        logger.warning(
            "Ask %s about the fix %r went unchecked: %s",
            handoff.ask,
            handoff.fix,
            error,
            # Expected when a model or the service misbehaves; anything else is a bug to see.
            exc_info=not isinstance(
                error, (UnusableReply, OutsideServiceDown, BlockedBySafetyFilter, AnswerTookTooLong)
            ),
        )
        return None
    return verdict.model_dump()


def say_the_fix_check_failed(check: FixCheck) -> None:
    """Show the chat the old version and the new one a fix made, and why its check found the
    change missing, with the reason as the note the scene's card is filled with: the shop
    owner decides what happens next (#131). The notice is kept on the check, as what their
    answer comes after."""
    number = check.step.scene.number
    picture = check.what == "picture"
    then = "make its clip anyway" if picture else "use the new clip in the ad anyway"
    kind = Attachment.Kind.PICTURE if picture else Attachment.Kind.VIDEO
    assert check.old is not None, "a fix is checked against the version before it"
    check.notice = post_notice(
        check.fix.job,
        f"Scene {number}'s new {'starting picture' if picture else 'clip'} may not have your "
        f"change: {check.reason} Here are the old {check.what} and the new one. Say go ahead "
        f"to {then}, or tell me what to change.{judge_still_sees(check.new)}",
        Message.Level.PROBLEM,
        # Without their scene's number, so the scene's card keeps showing its newest.
        carrying=[
            messages.AttachedFile(kind, check.old.file),
            messages.AttachedFile(kind, check.new.file),
        ],
        suggested=messages.SuggestedNote(number, check.reason),
    )
    check.save(update_fields=["notice"])


def judge_still_sees(clip: ProducedItem) -> str:
    """For a clip the clip judge failed even after its remakes, what it still found, as a
    sentence to follow another, so the shop owner hears both in one notice. Nothing
    otherwise."""
    ruling = clip.ruling or {}
    if ruling.get("verdict") != "fail":
        return ""
    problems = clip_judge.still_failing(ruling)
    return f" The clip's own check also still sees: {problems[0]}" if problems else ""


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
    return _keep_picture(step, file)


def _keep_picture(step: SceneStep, file: str) -> ProducedItem:
    """Keep the picture in the file store at `file` as the step's scene's next version of
    its starting picture."""
    scene = step.scene
    return ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=_next_version(scene, ProducedItem.Kind.STARTING_PICTURE),
        file=file,
        change=change_made_for(step),
    )


def change_made_for(step: SceneStep) -> str:
    """The change a starting picture or clip `step` makes is made for, in the shop owner's
    words, as its version's label: its fix's. Blank for the ad's first making."""
    return step.fix.fix if step.fix is not None else ""


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
    why: its line is too long for a B-roll clip. A talking scene shows no second state, and
    the scene after it no longer follows the first state it was made from."""
    with transaction.atomic():
        scene.change_line(scene.line, shows="")
        scene.shortened_from = []
        scene.second_state = False
        scene.save(
            update_fields=["shows", *BROLL_FIELDS, "shortened_from", "second_state", "status"]
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
    made = step.produced.order_by("id").first()
    if made is None:
        made = _make_clip(step)
    if step.shows and settings.QUALITY_CHECKS:
        return _auto_fixed(step, made)
    return made


def _auto_fixed(step: SceneStep, first: ProducedItem) -> ProducedItem:
    """The clip kept of a B-roll clip step's `first` clip and its remakes: each is judged,
    and one the judge fails is made again from the same starting picture, up to
    auto_fix.MOST_REMAKES times, its video prompt written again from what the judge saw
    wrong. The one with the fewest failed answers is kept, the newest of those tied; one kept
    from before the last is the scene's clip again, as its next version (the auto-fix, #155).

    Run again, as after a worker stopped, it pays for nothing already paid for: a clip made or
    judged is kept, and the remake under way is waited for."""
    made = _clip_tries(step)
    tries: list[ProducedItem] = []
    for remake in range(auto_fix.MOST_REMAKES + 1):
        if remake < len(made):
            clip = made[remake]
        else:
            # A remake that can't be made doesn't fail the step: its clips are made and paid
            # for, and the best of them is kept.
            try:
                clip = _remake_clip(step, tries)
            except Exception:
                logger.exception("Making scene %s's clip again failed", step.scene.number)
                break
        if "verdict" not in (clip.ruling or {}):
            _judge_clip(step, clip)
        tries.append(clip)
        if _ruling(clip)["verdict"] != "fail":
            break
    kept = tries[auto_fix.clip_to_keep([auto_fix.failed_answers(_ruling(clip)) for clip in tries])]
    if kept == tries[-1]:
        return kept
    brought_back = step.produced.filter(remake__isnull=True).exclude(pk=first.pk).first()
    if brought_back is not None:
        return brought_back
    # Every try failed, so each is a version the shop owner sees, and the one kept is gone
    # back to (#180).
    back_to = next(
        version.number for version in versions(step.scene, landing=tries) if version.clip == kept
    )
    return bring_back_clip(kept, back_to=back_to)


def kept_clip(step: SceneStep) -> ProducedItem:
    """The clip a finished B-roll clip step kept: the scene's newest clip from the step, as a
    remake kept from before the last is brought back as."""
    kept = step.scene.produced.filter(kind=ProducedItem.Kind.CLIP, step=step).order_by("id").last()
    assert kept is not None, "a finished clip step kept a clip"
    return kept


def clip_tries(step: SceneStep) -> list[ProducedItem]:
    """A B-roll clip step's clips made, oldest first: its first, then each remake."""
    return _clip_tries(step)


def _clip_tries(step: SceneStep) -> list[ProducedItem]:
    """A B-roll clip step's clips made so far, oldest first: its first, then each remake. A
    clip brought back as the one kept isn't another."""
    first = step.produced.order_by("id").first()
    assert first is not None, "the step made its clip"
    return [first, *step.produced.filter(remake__isnull=False).order_by("remake")]


def every_try_failed(step: SceneStep) -> bool:
    """Whether the clip judge failed a B-roll clip step's first clip and every remake of it
    made: the shop owner is then shown them all, to pick from (#180). A step with one clip
    has nothing to pick from."""
    tries = _clip_tries(step)
    return len(tries) > 1 and all((clip.ruling or {}).get("verdict") == "fail" for clip in tries)


def try_name(clip: ProducedItem) -> str:
    """Which try of its step a clip is, as the shop owner is shown it: "first try", or
    "remake 1"."""
    return f"remake {clip.remake}" if clip.remake else "first try"


@dataclass(frozen=True)
class TryShown:
    """One try of a clip the judge failed every try of, as the shop owner is shown it."""

    clip: ProducedItem
    # "First try", "Remake 1".
    name: str
    # The label of the scene version it is.
    version: str
    # What the judge still sees wrong in it, in its words: its first answer's.
    problem: str


@dataclass(frozen=True)
class TriesShown:
    """Every try of a B-roll clip step the judge failed every try of (#180), and the one in
    the ad now: the scene's current clip, when it is one of them, else None."""

    scene: int
    tries: list[TryShown]
    in_ad: ProducedItem | None


def tries_shown(step: SceneStep) -> TriesShown:
    """What the chat shows of a clip step the judge failed every try of, as it stands now."""
    made = versions(step.scene)
    labels = {version.clip.pk: version.label for version in made if version.clip is not None}
    tries = _clip_tries(step)
    current = made[-1].clip if made else None
    in_ad = next(
        (
            clip
            for clip in tries
            if current is not None and current.step_id == step.pk and current.file == clip.file
        ),
        None,
    )
    return TriesShown(
        scene=step.scene.number,
        tries=[
            TryShown(
                clip=clip,
                name=try_name(clip).capitalize(),
                version=labels.get(clip.pk, ""),
                # The first only: the judge's asks word the same fault again and again.
                problem=next(iter(clip_judge.still_failing(_ruling(clip))), ""),
            )
            for clip in tries
        ],
        in_ad=in_ad,
    )


def say_every_try_failed(step: SceneStep) -> None:
    """Show the shop owner every try of a B-roll clip the judge failed every try of, to pick
    from, the one kept already in the ad (#180): by code, never silent."""
    # The pick isn't named: a tap swaps another in, and the notice is read again then.
    post_notice(
        step.scene.job,
        f"The clip judge found a problem in all {len(_clip_tries(step))} tries at scene "
        f"{step.scene.number}'s clip. The app put the one with the fewest problems in the ad. "
        "Watch them, and tap another to use it instead.",
        Message.Level.INFO,
        tries_of=step.pk,
    )


# What a tap of "Use this one" is told when the tries it was under aren't on offer any more:
# the scene has a newer clip, or its line changed since.
TRIES_NOT_ON_OFFER = "Those tries aren't on offer any more."
TRY_IN_THE_AD = "That try is already in the ad."


class TryNotOnOffer(Exception):
    """A tap of "Use this one" on a try that can't be swapped in, saying why."""


def _try_to_use(clip: ProducedItem, waiting: ProducedItem | None = None) -> int:
    """The number of the scene version a try of a clip the judge failed every try of is, to
    swap it in for nothing: it must be one of the tries the chat showed, those tries still
    hold the clip in the ad, and it isn't that one, or `waiting`, the try a tap not yet acted
    on chose, which is in the ad by then. Refuses, saying why, otherwise, and while the
    scene's picture or clip is being made."""
    step = clip.step
    scene = clip.scene
    if (
        step is None
        or scene is None
        or step.kind != SceneStep.Kind.CLIP
        or not step.tries_shown.exists()
        or clip not in _clip_tries(step)
    ):
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    in_ad = tries_shown(step).in_ad
    if in_ad is None:
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    if waiting is not None:
        in_ad = waiting
    if in_ad == clip:
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
    if not go_back_is_free(scene, number):
        raise TryNotOnOffer(TRIES_NOT_ON_OFFER)
    return number


def check_try_to_use(clip: ProducedItem, *, waiting: ProducedItem | None = None) -> None:
    """Refuse a tap of "Use this one" on `clip`, saying why, unless it can be swapped in once
    `waiting`, the try an earlier tap not yet acted on chose, if any, is."""
    _try_to_use(clip, waiting)


def use_try(clip: ProducedItem) -> GoneBack:
    """Swap a try of a clip the judge failed every try of into the ad, as the shop owner
    tapped (#180): its version is gone back to, for nothing, with nothing made, judged or paid
    for, and the chat is shown it. The tap is their yes. Refuses as `check_try_to_use` does."""
    assert clip.scene is not None, "a try is a scene's clip"
    return go_back(clip.scene, _try_to_use(clip))


def what_the_judge_did(step: SceneStep, clip: ProducedItem) -> str:
    """What the auto-fix did before `clip` was kept as a B-roll clip step's clip, as a sentence
    to follow another, for the producer to tell the shop owner: nothing when the judge passed
    its first clip or didn't judge it. A first clip the judge couldn't check is kept as it is,
    and the shop owner is told (Julian, 2026-10-14: "treat that as pass")."""
    tries = _clip_tries(step)
    first = tries[0]
    verdict = (first.ruling or {}).get("verdict")
    if verdict == "not_checked":
        return " The clip judge couldn't check this clip, so it is kept unchecked."
    if verdict != "fail":
        return ""
    kept = next(made for made in tries if made.file == clip.file)
    remade = len(tries) - 1
    faults = " ".join(dict.fromkeys(clip_judge.still_failing(_ruling(first))))
    failed = f' The clip judge failed the first clip it made ("{faults}")'
    if not remade:
        return f"{failed}, and it couldn't be made again, so it is kept as it is."
    times = "once" if remade == 1 else f"{remade} times"
    said = f"{failed}, so it was made again {times}"
    if _ruling(kept)["verdict"] == "pass":
        return f"{said}, and the judge passed the new one."
    if _ruling(kept)["verdict"] == "not_checked":
        return f"{said}, and the judge couldn't check the new one, so it is kept unchecked."
    which = "the first clip" if kept.remake is None else f"remake {kept.remake}"
    still = " ".join(dict.fromkeys(clip_judge.still_failing(_ruling(kept))))
    shown = (
        f" The chat showed the shop owner all {len(tries)} tries, each with what the judge still "
        "sees, so they can tap another to use it instead, for nothing."
        if every_try_failed(step)
        else ""
    )
    return (
        f"{said}, and it failed {'the remake' if remade == 1 else 'each remake'} too. The clip "
        f'kept is {which}, the one with the fewest faults, and the judge still sees: "{still}"'
        f"{shown}"
    )


def _ruling(clip: ProducedItem) -> dict[str, Any]:
    """The clip judge's ruling on `clip`, judged."""
    ruling: dict[str, Any] | None = clip.ruling
    assert ruling is not None, "the clip was judged"
    return ruling


def _remake_clip(step: SceneStep, tries: list[ProducedItem]) -> ProducedItem:
    """Make a B-roll clip step's clip again after the judge failed each of `tries`, its clips
    so far, oldest first: a model told the same rules as the one that wrote the scene's
    prompts writes the video prompt again, handed what the judge saw wrong in the first clip
    and, for a second remake, the first remake's prompt and what it saw wrong in that, and
    shown the starting picture. The shop owner's fix the clip was made for, if any, is kept.
    No conversation is handed over, and no "already made" or yes guard is asked: the judge,
    not the shop owner, asked for it (change 9, Julian 2026-10-13)."""
    job = step.scene.job
    planned = step.picture_step
    assert step.picture is not None and planned is not None, (
        "a B-roll clip is made again only from its starting picture's step"
    )
    first, *remade = tries
    earlier = remade[-1] if remade else None
    during = _during_the_clip(step)
    instructions = clip_from_judge_instructions(step.broll_kind, again=earlier is not None)
    if during:
        rules = reference_photos.during_clip_rules([row.thing for row in during])
        instructions = f"{instructions}\n{rules}"
    written = call_model(
        job=job,
        purpose="remake_clip_prompt",
        instructions=instructions,
        handoff=ClipFromJudgeHandoff(
            scene=step.scene.number,
            line=step.line,
            shows=step.shows,
            broll_kind=step.broll_kind,
            person_shown=step.person_shown,
            usage=step.usage,
            result=step.broll_result,
            picture_prompt=planned.prompt,
            motion_prompt=step.motion_prompt or planned.motion_prompt,
            faults=auto_fix.faults(_ruling(first)),
            judge_saw=auto_fix.judge_saw(_ruling(first)),
            owner_fix=step.note or None,
            earlier_try=(
                JudgedTry(
                    motion_prompt=earlier.motion_prompt,
                    faults=auto_fix.faults(_ruling(earlier)),
                    judge_saw=auto_fix.judge_saw(_ruling(earlier)),
                )
                if earlier is not None
                else None
            ),
        ),
        output=ClipFromNoteChoice,
        images=[
            Image(label="The starting picture", key=step.picture.file),
            *_the_owners_things(step, during),
        ],
        pay_once=True,
    )
    return _make_clip(step, remake=len(tries), motion=written.motion_prompt)


def _the_owners_things(step: SceneStep, during: list[ReferencePhoto]) -> list[Image]:
    """The clean pictures of the shop owner's things that only show during the clip, as a
    model writing its video prompt is shown them."""
    return [
        Image(label=f"The shop owner's own {row.thing}", key=clean_picture(step, row))
        for row in during
    ]


def _judge_clip(step: SceneStep, clip: ProducedItem) -> None:
    """Have the clip judge rule on a B-roll clip, keeping its ruling on the clip, with what was
    said with the shop owner when the step started."""
    clip_judge.judge_clip(
        step, clip, conversation=_conversation(step.scene.job, until=step.started_at)
    )


def _make_clip(step: SceneStep, *, remake: int = 0, motion: str = "") -> ProducedItem:
    """make_clip's clip, made and kept: its first, or its `remake`th remake, moving as `motion`
    says."""
    scene = step.scene
    job = scene.job
    picture, audio = step.picture, step.made_from
    assert audio is not None, "a clip step starts with its audio"
    assert picture is not None or step.picture_step is not None, "and its picture, or its plan"
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    if step.note and not step.motion_prompt and not remake:
        _rewrite_motion_prompt(step)
    handoff = _clip_handoff(step, picture, audio, motion=motion)
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
            change=change_made_for(step),
        )
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
    return clip


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
    come; a new clip of the same picture is one of its own. A clip the judge failed and code
    made again isn't one, unless it was kept, or the judge failed every try of it: then each
    try is one (#180). The line lives on its own: a version keeps the
    line its clip says, or its picture was made for (Julian 2026-10-16 22:39, "the picture is
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
    # For a try of a clip whose every try the judge failed, which try it is: "remake 1".
    tried: str = ""

    @property
    def label(self) -> str:
        """How the shop owner and the agent deciding fixes see it named: "v1 first version",
        then the change it was made for, as "v2 black toilet" (decision 17), and which try it
        is of a clip the judge failed every try of, as "v3 black toilet (remake 1)"."""
        tried = f" ({self.tried})" if self.tried else ""
        return f"v{self.number} {self.change or 'first version'}{tried}"

    @property
    def line(self) -> str:
        """The line its clip says, or its picture was made for while it has no clip."""
        made = self.clip or self.picture
        return made.step.line if made is not None and made.step is not None else ""


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
    """Which try a clip is, when its step's judge failed every try: blank otherwise."""
    step = made.step
    if made.kind != ProducedItem.Kind.CLIP or step is None or step.kind != SceneStep.Kind.CLIP:
        return ""
    if made not in _clip_tries(step) or not every_try_failed(step):
        return ""
    return try_name(made)


def _tries_were_shown(step: SceneStep) -> bool:
    """Whether the chat showed the shop owner every try of a clip step the judge failed every
    try of. A step finished before the chat showed them (#180) never did: its tries aren't
    versions, so its versions keep their numbers."""
    return every_try_failed(step) and step.tries_shown.exists()


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
    finished with. Of a clip step's clip and the remakes the judge failed it into, only the
    one kept was, unless an earlier one kept was brought back on top (`_auto_fixed`), or the
    judge failed them all: then each was, to pick from (#180)."""
    step = made.step
    if step is None:
        return True
    if step.status != SceneStep.Status.FINISHED:
        return False
    if made.kind != ProducedItem.Kind.CLIP:
        return True
    tries = _clip_tries(step)
    if made not in tries:
        # Brought back: by the auto-fix, or as an earlier note's clip again.
        return True
    if len(tries) == 1 or _tries_were_shown(step):
        return True
    failed = [auto_fix.failed_answers(clip.ruling or {"verdict": "pass"}) for clip in tries]
    return made == tries[-1] and auto_fix.clip_to_keep(failed) == len(tries) - 1


def version_label(made: ProducedItem) -> str:
    """The label of the scene version a starting picture or clip about to be shown is in."""
    assert made.scene is not None, "a starting picture or clip belongs to a scene"
    landing = [made]
    step = made.step
    if (
        made.kind == ProducedItem.Kind.CLIP
        and step is not None
        and step.kind == SceneStep.Kind.CLIP
    ):
        # Every try the judge failed lands with the one kept, each its own version.
        if every_try_failed(step):
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
    and the scene waits for a clip made from it. The scene's photos in use become those the
    version was made with, so a photo dropped is a version gone back to. The chat is shown
    what was brought back. Refuses a version the scene doesn't have, one with no picture whose
    clip no longer says the line, and while the scene's picture or clip is being made."""
    with transaction.atomic():
        Scene.objects.select_for_update().get(pk=scene.pk)
        # Whether the clip still says the line is read from the scene as it now stands.
        scene.refresh_from_db()
        if scene.steps.filter(
            kind__in=[SceneStep.Kind.STARTING_PICTURE, SceneStep.Kind.CLIP],
            status=SceneStep.Status.RUNNING,
        ).exists():
            raise SceneBusy(f"scene {scene.number}'s picture or clip is still being made")
        shown = versions(scene)
        if not 1 <= number <= len(shown):
            raise NoSuchVersion(
                f"scene {scene.number} has versions 1 to {len(shown)}, not {number}"
            )
        earlier = shown[number - 1]
        free = earlier.clip is not None and _says_the_line_as_it_stands(scene, earlier.clip)
        if earlier.picture is None and not free:
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
                    messages.AttachedFile(kind, made.file, scene.number, top.label)
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
    scene.reference_photos.filter(pk__in=rows).update(set_aside_at=None, dropped_by=None)


def _rewrite_motion_prompt(step: SceneStep) -> None:
    """Write a B-roll clip step's video prompt again from its note, keeping it on the step: a
    model told the same rules as the one that wrote the scene's prompts, and that the starting
    picture stays, is handed the picture's prompt, the movement of the clip it improves on,
    the scene and the note, and shown the picture. The producer never writes a prompt (Julian
    2026-10-11 21:00).

    Run again, as after a worker stopped, it pays for nothing already paid for: the answer for
    the same handoff is answered from its record."""
    job = step.scene.job
    planned = step.picture_step
    assert step.picture is not None and planned is not None, (
        "the tool makes a B-roll clip from a note only from its starting picture's step"
    )
    during = _during_the_clip(step)
    instructions = clip_from_note_instructions(step.broll_kind)
    if during:
        rules = reference_photos.during_clip_rules([row.thing for row in during])
        instructions = f"{instructions}\n{rules}"
    rewrite = call_model(
        job=job,
        purpose="rewrite_clip_prompt",
        instructions=instructions,
        handoff=ClipFromNoteHandoff(
            scene=step.scene.number,
            line=step.line,
            shows=step.shows,
            broll_kind=step.broll_kind,
            person_shown=step.person_shown,
            usage=step.usage,
            result=step.broll_result,
            picture_prompt=planned.prompt,
            motion_prompt=_movement_to_improve(step, planned),
            note=step.note,
            conversation=_conversation(job, until=step.started_at),
        ),
        output=ClipFromNoteChoice,
        images=[
            Image(label="The starting picture", key=step.picture.file),
            *_the_owners_things(step, during),
        ],
        pay_once=True,
    )
    step.motion_prompt = rewrite.motion_prompt
    step.save(update_fields=["motion_prompt"])


def _movement_to_improve(step: SceneStep, planned: SceneStep) -> str:
    """How the clip a note's clip improves on moved: the scene's clip as the shop owner sees
    it now, when it is of the same starting picture, so a try they swapped in, or an earlier
    note's clip, is built on (#180); else as the picture's step planned."""
    made = versions(step.scene)
    current = made[-1].clip if made else None
    if (
        current is not None
        and current.picture is not None
        and step.picture is not None
        and current.picture.file == step.picture.file
    ):
        return motion_of(current)
    return planned.motion_prompt


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


# Every B-roll clip is made in reference mode, its drawn starting picture sent as Image 1
# (Julian 2026-10-16 19:14). False goes back to before: a clip is made from its starting
# picture as its first frame, and in reference mode only with the shop owner's things that
# only show during it.
EVERY_BROLL_CLIP_IN_REFERENCE_MODE = True


def _clip_handoff(
    step: SceneStep, picture: ProducedItem | None, audio: ProducedItem, *, motion: str = ""
) -> ClipHandoff | BrollClipHandoff:
    """What the video model is asked for: a clip speaking the audio, or, for a scene that
    shows the product, a silent one that moves as the picture's step planned, in the fewest
    whole seconds that cover the audio, starting like its starting picture. Raises ClipFailed
    for a line longer than the longest B-roll clip, before anything is paid for."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    if not step.shows:
        assert picture is not None, "a talking scene's clip is made from its picture"
        return ClipHandoff(
            picture=picture.file,
            audio=audio.file,
            # The talking video model makes the clip as long as the audio, whatever it is
            # asked for, so this is only the seconds it is billed for: at least the shortest
            # clip a handoff takes.
            seconds=max(audio.seconds, LEAST_CLIP_SECONDS),
            motion_prompt=talking_motion_prompt(step.scene.job.product_size),
        )
    # A clip step started before clip steps kept their picture step has only its picture.
    planned = step.picture_step or (picture.step if picture else None)
    assert planned is not None, "a starting picture is made by a scene step"
    if audio.seconds > MOST_BROLL_SECONDS:
        raise ClipFailed(
            f"its line takes {round(audio.seconds, 1):g} seconds to say, and the B-roll video "
            f"model makes clips of at most {MOST_BROLL_SECONDS} seconds"
        )
    seconds = _broll_clip_seconds(audio.seconds)
    assert picture is not None, "a B-roll scene's clip is made from its starting picture"
    # A clip made from a note has its own prompt, written again from the picture's.
    # A remade clip has its own, written again from what the judge saw wrong.
    motion = motion or step.motion_prompt or planned.motion_prompt
    during = _during_the_clip(step)
    # A scene planned before it had its B-roll labels is moved as before. A labelled scene's
    # prompt is sent as written, after its look and length: the real photo of the product
    # keeps it true. Only a labelled scene takes the shop owner's photos.
    prompt = broll_video_prompt(motion, seconds) if planned.way else with_nothing_made_up(motion)
    if during or EVERY_BROLL_CLIP_IN_REFERENCE_MODE:
        # Reference mode (Julian 2026-10-15 17:21, 19:38; every B-roll clip since 2026-10-16
        # 19:14): the drawn starting picture goes first, as Image 1, told the clip starts like
        # it, then the clean pictures of the shop owner's things that only show during the
        # clip. Their raw photos never go.
        return BrollClipHandoff(
            example_pictures=[picture.file, *(clean_picture(step, row) for row in during)],
            seconds=seconds,
            prompt=(
                f"{reference_photos.reference_mode_label([row.thing for row in during])} {prompt}"
            ),
        )
    return BrollClipHandoff(starting_picture=picture.file, seconds=seconds, prompt=prompt)


def _broll_clip_seconds(audio_seconds: float) -> int:
    """How many seconds to ask for a B-roll clip covering `audio_seconds` of its line: the
    video model makes only whole seconds, at least LEAST_BROLL_SECONDS. A hair over a whole
    second, as a measurement can be, isn't counted as another second."""
    return max(LEAST_BROLL_SECONDS, math.ceil(audio_seconds - 1e-6))


def clip_estimate_usd(audio_seconds: float, *, broll: bool) -> Decimal:
    """What a scene's clip covering `audio_seconds` of its line is billed, before it is made:
    a B-roll clip's whole seconds at the B-roll video model's price, a talking clip's audio
    at the talking one's, as the clip's handoff asks for them (see _clip_handoff)."""
    making = "make_broll_clip" if broll else "make_talking_clip"
    seconds = (
        _broll_clip_seconds(audio_seconds) if broll else max(audio_seconds, LEAST_CLIP_SECONDS)
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
    words to the user, not the producer's, and are left out."""
    if job.session is None:
        # A job started before sessions existed has no conversation.
        return []
    said = job.session.messages.exclude(role=Message.Role.NOTICE).prefetch_related("attachments")
    if until is not None:
        said = said.filter(created_at__lte=until)
    return [
        ChatMessage(
            by="user" if message.role == Message.Role.USER else "producer",
            text=messages.as_read(message),
        )
        for message in said
    ]
