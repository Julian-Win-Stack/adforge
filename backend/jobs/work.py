"""The work the producer's tools do on a job: reading its page, planning it, making its
person, running the planning checks, making the music, making each scene and assembling the
finished ad."""

import contextvars
import io
import json
import math
import mimetypes
import tempfile
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Q, QuerySet

from adforge import file_store
from adforge.retry import OutsideServiceDown, with_retries
from chat import messages
from chat.models import Message
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
    BrollClipHandoff,
    ClipFailed,
    ClipHandoff,
    Handoff,
    Image,
    Judgement,
    MusicHandoff,
    UnusableReply,
)

from . import assembly, firecrawl, page, page_text, photos
from .checks import (
    FACT_CHECK_INSTRUCTIONS,
    LENGTH_ALLOWANCE_SECONDS,
    LONGEST_BROLL_LINE_SECONDS,
    LONGEST_LINE_SECONDS,
    MOST_BROLL_SHORTENINGS,
    MOST_REWRITES,
    REWRITE_INSTRUCTIONS,
    SHORTEN_INSTRUCTIONS,
    SHORTEN_LINE_INSTRUCTIONS,
    FactCheck,
    FactCheckHandoff,
    LineToCheck,
    Problem,
    RewriteHandoff,
    RewrittenLine,
    ShortenHandoff,
    ShortenLineHandoff,
    count_words,
    fact_check_for,
    fits_target,
    line_seconds,
    most_words,
    most_words_in_a_line,
    rewritten_scene_for,
    script_seconds,
    shortened_script_for,
)
from .models import BROLL_FIELDS, Job, ProducedItem, ProductPhoto, Scene, SceneStep
from .notices import post_notice
from .planning import (
    PLAN_INSTRUCTIONS,
    ChatMessage,
    PlanHandoff,
    ProducerDecision,
    producer_decision_for,
)
from .scenes import (
    BROLL_PICTURE_INSTRUCTIONS,
    STARTING_PICTURE_INSTRUCTIONS,
    BrollPictureChoice,
    BrollPictureHandoff,
    StartingPictureChoice,
    StartingPictureHandoff,
    pose_for,
    starting_picture_choice_for,
    talking_motion_prompt,
    with_nothing_made_up,
)

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


def copy_page_text(job: Job, download: page.Download, product_page: page.ProductPage) -> str:
    """This product's own text: what a model copies out of the page about it, each sentence
    matched back to the page, then the product data the page declares. However little is
    kept is used. A failed copy call posts a notice and is raised: the page isn't read."""
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
    return "\n".join(page_text.match_back(copied.passages, on_the_page)) + product_page.declared


@dataclass(frozen=True)
class Picked:
    """The picker's photo links, gallery first, or None when the page's declared photos are
    used instead; and the notices to post about how they were picked."""

    urls: list[str] | None
    notices: list[tuple[str, Message.Level]]
    # The picked links the picker marked with a stranger's face.
    faces: frozenset[str] = frozenset()


DECLARED_INSTEAD = (
    "Used the photos the page declares for search engines instead, which may include other "
    "products' photos and miss some of this one's."
)


def copy_and_pick(job: Job, read: PageRead, product_page: page.ProductPage) -> tuple[str, Picked]:
    """This product's own text and photos, copied and picked off the page at the same time.
    A failed copy call is raised, as by copy_page_text."""
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        picking = pool.submit(context.run, _closing_its_connection, pick_photos, job, read)
        text = copy_page_text(job, read.download, product_page)
    return text, picking.result()


def _closing_its_connection[Out](work: Callable[..., Out], *args: Any) -> Out:
    """Run `work` in a thread of its own, then close the database connection the thread
    opened."""
    try:
        return work(*args)
    finally:
        connection.close()


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
    try:
        picked = call_model(
            job=job,
            purpose="pick_photos",
            instructions=photos.PICK_INSTRUCTIONS,
            handoff=photos.handoff(record, read.marked.marks, read.marked.title),
            output=photos.PickedPhotos,
            images=_picker_images(job, record, read.marked.screenshot),
            # A page read again, as after a worker stopped, isn't picked and paid for twice.
            pay_once=True,
        )
    except (UnusableReply, OutsideServiceDown) as error:
        notices.append(
            (
                f"Picking this product's photos off the page failed ({error}). {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, notices)
    links = photos.picked_links(picked, read.marked.marks)
    if not links:
        notices.append(
            (
                f"The photo picker found no photo of this product on the page. {DECLARED_INSTEAD}",
                Message.Level.PROBLEM,
            )
        )
        return Picked(None, notices)
    return Picked(
        [url for url, _ in links],
        notices,
        frozenset(url for url, has_face in links if has_face),
    )


def _picker_images(job: Job, record: dict[str, Any] | None, screenshot: bytes) -> list[Image]:
    """The pictures the picker is shown: the record's official photos, R1, R2, ..., then the
    screenshot's parts, top to bottom. Kept with the job's Firecrawl answers, so a read run
    again shows the same files and pays for nothing twice."""
    shown = job.firecrawl.get("picker_images")
    if shown is not None:
        return [Image(label, key) for label, key in shown]
    images: list[Image] = []
    for url in photos.reference_urls(record) if record else []:
        try:
            official = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
        except page.PageUnreadable, OutsideServiceDown:
            continue  # A guide only: the picker does without it.
        if official.content_type not in IMAGE_TYPES:
            continue
        number = len(images) + 1
        extension = mimetypes.guess_extension(official.content_type) or ""
        key = file_store.save(
            f"jobs/{job.pk}/firecrawl/official-{number}{extension}", official.content
        )
        images.append(Image(f"R{number}", key))
    parts = photos.parts(screenshot)
    for number, (top, part) in enumerate(parts, 1):
        key = file_store.save(f"jobs/{job.pk}/firecrawl/part-{number}.jpg", part)
        images.append(Image(f"Part {number} of {len(parts)} (from {top} px down the page):", key))
    job.firecrawl["picker_images"] = [[image.label, image.key] for image in images]
    job.save(update_fields=["firecrawl"])
    return images


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
) -> list[SkippedPhoto]:
    """Download and keep each photo, after any the job already has, up to MAX_PHOTOS. With
    `merge_copies`, a photo that is a copy of one already kept, at another size or under
    another name, is left out, and the one kept has a face if either does. `faces` are the
    links the picker marked with a face; without them, each photo kept is given its Face note
    by a call of its own. Gives back each one that was skipped, with why."""
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
            photo = page.download(url, max_bytes=page.MAX_PHOTO_BYTES, what="product photo")
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
        if merge_copies and (copy := copies.copy_of(url, photo.content)) is not None:
            _merge_face(kept[copy], url, faces)
            continue
        saved += 1
        has_face = url in faces if faces is not None else None
        kept.append(
            keep_photo(
                job, saved, photo.content, photo.content_type, source_url=url, has_face=has_face
            )
        )
    return skipped


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
) -> ProductPhoto:
    """Store a product photo with the job, with its Face note, or with None, noted by a call
    of its own. An uploaded photo has no source link."""
    extension = mimetypes.guess_extension(content_type) or ""
    key = file_store.save(f"jobs/{job.pk}/photos/{position}{extension}", content)
    if has_face is None:
        has_face = note_face(job, source_url, key)
    return ProductPhoto.objects.create(
        job=job, position=position, source_url=source_url, file=key, has_face=has_face
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
    decision = call_model(
        job=job,
        purpose="plan_ad",
        instructions=PLAN_INSTRUCTIONS,
        handoff=PlanHandoff(
            product_url=job.product_url,
            page_text=page.for_model(job.page_text),
            target_seconds=job.target_seconds,
            photo_count=len(photos),
            conversation=_conversation(job),
        ),
        output=producer_decision_for(len(photos)),
        images=[Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos],
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
        job.status = Job.Status.PLANNED
        job.save(
            update_fields=[
                "product_name",
                "product_colour",
                "product_size",
                "person_gender",
                "person_looks",
                "person_voice",
                "status",
            ]
        )
        job.photos.filter(position__in=planned.colour_photos).update(shows_product_colour=True)
    return decision


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
    calls = job.model_calls.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED)
    if charged_to is not None:
        calls = calls.filter(tool_call=charged_to)
    call = calls.last()
    return call.output if call else None


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
    against the page; then each line against the longest a clip can last, and the whole
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
        images=[Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos]
        if scene.shows
        else [],
    )
    scene.fact_problems[-1]["rewritten"] = True
    scene.change_line(rewrite.line, rewrite.shows or "", broll=rewrite.broll_details())
    scene.save(
        update_fields=["line", "shortened_from", "shows", *BROLL_FIELDS, "status", "fact_problems"]
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
    """Whether the script can go on to be rendered at its length: every line short enough
    for its clip, and the whole script within its target. If it can't, a line or the script
    is shortened, giving False so the checks go round again, or the user is asked."""
    voice = latest(job, ProducedItem.Kind.VOICE)
    assert voice is not None and voice.words_per_second is not None
    words_per_second = voice.words_per_second
    too_long = _a_line_too_long(job, words_per_second)
    if too_long is not None:
        return _fit_line(job, too_long, words_per_second)
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


def _a_line_too_long(job: Job, words_per_second: float) -> Scene | None:
    """The first scene whose line takes the voice longer to say than a clip can last."""
    return next(
        (
            scene
            for scene in job.scenes.all()
            if line_seconds(scene.line, words_per_second) > LONGEST_LINE_SECONDS
        ),
        None,
    )


def _fit_line(job: Job, scene: Scene, words_per_second: float) -> Literal[False] | Asking:
    """Shorten a line too long for its clip, giving False so the checks go round again and
    fact-check it, or ask the user for a shorter one once it was shortened MOST_REWRITES
    times since they last spoke."""
    shortened = job.model_calls.filter(
        purpose="shorten_line", outcome=ModelCall.Outcome.SUCCEEDED, handoff__scene=scene.number
    )
    if _since_the_user_spoke(job, shortened) < MOST_REWRITES:
        _shorten_line(job, scene, words_per_second)
        return False
    seconds = line_seconds(scene.line, words_per_second)
    return Asking(
        about="line_length",
        question=(
            f"Scene {scene.number}'s line still takes about {seconds:.1f} seconds to say after "
            f"{MOST_REWRITES} shortenings, and a scene can last at most {LONGEST_LINE_SECONDS} "
            f'seconds: "{scene.line}"{_while_its_said(scene)}'
        ),
        reason=(
            f"The line was shortened {MOST_REWRITES} times and is still too long for one "
            "scene, so you choose a shorter line."
        ),
        scene=scene,
    )


def _shorten_line(job: Job, scene: Scene, words_per_second: float) -> None:
    shortened = _shorter_line(
        job, scene, most_words_in_a_line(words_per_second), _conversation(job)
    )
    # A new line is fact checked again.
    scene.change_line(shortened)
    scene.fact_checked = False
    scene.fact_problems = []
    scene.save(update_fields=["line", "shortened_from", "status", "fact_checked", "fact_problems"])


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
    comes from, and takes that scene's "shows", overlay and B-roll fields with it, so a
    dropped scene takes them away and a line never moves under another scene's picture."""
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
        was = {scene.number: (scene.shows, scene.overlay, _broll_fields(scene)) for scene in scenes}
        # A line that passed the fact check word for word, showing the same, still has;
        # anything else is new.
        checked = {(scene.line, scene.shows) for scene in scenes if scene.fact_checked}
        for scene, kept in zip(scenes, shortened.lines, strict=False):
            shows, overlay, broll = was[kept.scene]
            if (scene.line, scene.shows, scene.overlay, _broll_fields(scene)) != (
                kept.line,
                shows,
                overlay,
                broll,
            ):
                scene.change_line(kept.line, shows=shows)
                scene.overlay = overlay
                for field, value in broll.items():
                    setattr(scene, field, value)
                scene.fact_checked = (kept.line, shows) in checked
                scene.fact_problems = []
                scene.save(
                    update_fields=[
                        "line",
                        "shortened_from",
                        "shows",
                        "overlay",
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
    passed: every line has passed the fact check, every line fits in a scene, and the script
    fits its target length or the user chose to keep it longer."""
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
    too_long = _a_line_too_long(job, voice.words_per_second)
    if too_long is not None:
        return (
            f"scene {too_long.number}'s line takes longer to say than a scene can last. Run "
            "the planning checks first."
        )
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
    scene = step.scene
    job = scene.job
    portrait = latest(job, ProducedItem.Kind.PORTRAIT)
    assert portrait is not None, "the tool refuses a scene whose person isn't made"
    photos = list(job.photos.filter(shows_product_colour=True))
    numbers = [photo.position for photo in photos]
    handoff = StartingPictureHandoff(
        scene=scene.number,
        line=step.line,
        script=list(job.scenes.values_list("line", flat=True)),
        product_colour=job.product_colour,
        colour_photos=numbers,
        person_looks=job.person_looks,
        pose=pose_for(job.product_size),
        note=step.note or None,
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
    paid_for = _paid_for_before(job, "make_starting_picture", charged_to=step.tool_call)
    file = (
        paid_for["file"]
        if paid_for
        else edit_picture(
            job=job,
            purpose="make_starting_picture",
            prompt=prompt,
            pictures=[portrait.file, step.photo.file],
        )
    )
    return ProducedItem.objects.create(
        job=job,
        scene=scene,
        step=step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=_next_version(scene, ProducedItem.Kind.STARTING_PICTURE),
        file=file,
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
    shortened MOST_BROLL_SHORTENINGS times, or if the shorter line fails the fact check,
    the scene is said to camera instead, and the chat says why. Nobody is asked.

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
    why: its line is too long for any B-roll clip."""
    with transaction.atomic():
        scene.change_line(scene.line, shows="")
        scene.shortened_from = []
        scene.save(update_fields=["shows", *BROLL_FIELDS, "shortened_from", "status"])
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
    made = step.produced.first()
    if made is not None:
        return made
    scene = step.scene
    job = scene.job
    picture, audio = step.picture, step.made_from
    assert picture is not None and audio is not None, "a clip step starts with both"
    assert audio.seconds is not None, "a line's audio is measured when it's made"
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
    fetched = _paid_for_before(job, collecting, charged_to=step.tool_call)
    if fetched:
        file = fetched["file"]
    else:
        video_id = _clip_asked_for(step, handoff, making, collecting) or _ask_for_clip(
            job, making, handoff
        )
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
        )
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
    return clip


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


def _clip_handoff(
    step: SceneStep, picture: ProducedItem, audio: ProducedItem
) -> ClipHandoff | BrollClipHandoff:
    """What the video model is asked for: a clip speaking the audio, or, for a scene that
    shows the product, a silent one from its starting picture that moves as the picture's
    step planned, in the fewest whole seconds that cover the audio. Raises ClipFailed for a
    line longer than the longest B-roll clip, before anything is paid for."""
    assert audio.seconds is not None, "a line's audio is measured when it's made"
    if not step.shows:
        return ClipHandoff(
            picture=picture.file,
            audio=audio.file,
            # The talking video model makes the clip as long as the audio, whatever it is
            # asked for, so this is only the seconds it is billed for: at least the shortest
            # clip a handoff takes.
            seconds=max(audio.seconds, LEAST_CLIP_SECONDS),
            motion_prompt=talking_motion_prompt(step.scene.job.product_size),
        )
    assert picture.step is not None, "a starting picture is made by a scene step"
    if audio.seconds > MOST_BROLL_SECONDS:
        raise ClipFailed(
            f"its line takes {round(audio.seconds, 1):g} seconds to say, and the B-roll video "
            f"model makes clips of at most {MOST_BROLL_SECONDS} seconds"
        )
    return BrollClipHandoff(
        starting_picture=picture.file,
        seconds=_broll_clip_seconds(audio.seconds),
        prompt=with_nothing_made_up(picture.step.motion_prompt),
    )


def _broll_clip_seconds(audio_seconds: float) -> int:
    """How many seconds to ask for a B-roll clip covering `audio_seconds` of its line: the
    video model makes only whole seconds, at least LEAST_BROLL_SECONDS. A hair over a whole
    second, as a measurement can be, isn't counted as another second."""
    return max(LEAST_BROLL_SECONDS, math.ceil(audio_seconds - 1e-6))


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
    step: SceneStep, handoff: ClipHandoff | BrollClipHandoff, making: str, collecting: str
) -> str | None:
    """The id of a clip already paid for with this same handoff that may still be made, so
    it is waited for rather than paid for again: asked for by this step before the worker
    stopped, or by an earlier one that stopped or gave up while the video service was down.
    None if there is none, or the video model said it couldn't make it, or it was fetched:
    then it was kept, or it was refused. `making` and `collecting` are the purposes this kind
    of clip is asked for and collected as."""
    job = step.scene.job
    asked_by_this_step = job.model_calls.filter(
        purpose=making, outcome=ModelCall.Outcome.SUCCEEDED, tool_call=step.tool_call
    ).last()
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
