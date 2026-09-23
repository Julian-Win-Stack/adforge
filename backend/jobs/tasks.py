import io
import logging
import mimetypes
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from celery import shared_task
from django.db import transaction
from django.db.models import Max

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message
from gateway.gateway import (
    IMAGE_TYPE_NAMES,
    IMAGE_TYPES,
    UnreadableImage,
    call_model,
    design_voice,
    draw_picture,
    speak,
)
from gateway.models import ModelCall
from gateway.types import Handoff, Image, Judgement, UnusableReply

from . import page
from .activity import record
from .checks import (
    FACT_CHECK_INSTRUCTIONS,
    MOST_REWRITES,
    REWRITE_INSTRUCTIONS,
    SHORTEN_INSTRUCTIONS,
    FactCheckHandoff,
    LineToCheck,
    Problem,
    RewriteHandoff,
    RewrittenLine,
    ShortenedScript,
    ShortenHandoff,
    count_words,
    fact_check_for,
    fits_target,
    most_words,
    script_seconds,
)
from .models import Job, ProducedItem, ProductPhoto, Question, Scene
from .planning import (
    PLAN_INSTRUCTIONS,
    ChatMessage,
    PlanHandoff,
    ProducerDecision,
    producer_decision_for,
)

logger = logging.getLogger(__name__)

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
    # Run again after a restart, the task only carries on a read that hadn't finished. A page
    # already read goes on to planning, in case the restart came before it was queued.
    if job.status == Job.Status.PAGE_READ:
        return True
    if job.status not in (Job.Status.QUEUED, Job.Status.READING_PAGE):
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
    product_page = keep_page(job, download)
    record(
        job,
        f"Stored {len(product_page.text):,} characters of page text and the page's HTML",
        reason="The script's claims will be checked against this text, and the HTML shows "
        "exactly what the page said on the day it was read.",
    )

    check = check_page(job, download, product_page)
    if check.decision == "unreadable":
        _ask_for_working_link(job, reason=check.reason)
        return False
    record(job, "The page has what the ad needs", reason=check.reason)

    for skipped in save_photos(job, product_page.photo_urls):
        record(job, f"Skipped the photo at {skipped.url}", reason=skipped.reason)
    saved = job.photos.count()
    if saved == 0:
        _ask(
            job,
            Question.Kind.PRODUCT_PHOTOS,
            PRODUCT_PHOTOS_QUESTION,
            message="Waiting for product photos",
            reason="The ad has to show the real product, and the page gave no product photo "
            "we could use.",
        )
        return False
    record(
        job,
        f"Saved {saved} product photo{'s' if saved != 1 else ''}",
        reason="The ad has to show the real product, so its photos are kept with the job.",
        status=Job.Status.PAGE_READ,
    )
    return True


def keep_page(job: Job, download: page.Download) -> page.ProductPage:
    """Store the page's text and its original HTML with the job."""
    product_page = page.parse(download)
    job.page_text = product_page.text
    job.page_html_key = file_store.save(f"jobs/{job.pk}/page.html", download.content)
    job.save(update_fields=["page_text", "page_html_key"])
    return product_page


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
    )


def _ask_for_working_link(job: Job, *, reason: str) -> None:
    _ask(
        job,
        Question.Kind.WORKING_LINK,
        WORKING_LINK_QUESTION,
        message="Waiting for a working link to the product page",
        reason=reason,
    )


@dataclass(frozen=True)
class SkippedPhoto:
    """A photo on the page that wasn't kept, and one sentence saying why."""

    url: str
    reason: str


def save_photos(job: Job, urls: list[str]) -> list[SkippedPhoto]:
    """Download and keep each photo, after any the job already has. Gives back each one
    that was skipped, with why."""
    # A read run again after a crash starts the page's photos afresh, so each is kept once.
    # Photos the user attached are theirs, and stay.
    job.photos.exclude(source_url="").delete()
    saved = job.photos.aggregate(last=Max("position"))["last"] or 0
    skipped = []
    for url in urls:
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
        saved += 1
        keep_photo(job, saved, photo.content, photo.content_type, source_url=url)
    return skipped


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
    if _run_step(job, _plan_ad, "plan the ad", "planning the ad"):
        make_person.delay(job_id)


@shared_task
def make_person(job_id: str) -> None:
    job = Job.objects.get(pk=job_id)
    if _run_step(job, _make_person, "make the person", "making the person"):
        check_plan.delay(job_id)


@shared_task
def check_plan(job_id: str) -> None:
    job = Job.objects.get(pk=job_id)
    _run_step(job, _check_plan, "check the plan", "checking the plan")


def _run_step(job: Job, step: Callable[[Job], bool], could_not: str, while_doing: str) -> bool:
    """Run one step of the job, stopping the job with the reason if it fails. True when the
    job goes on to its next step."""
    try:
        return step(job)
    except UnusableReply as error:
        reason = f"The model's answer couldn't be used: {error}."
    except OutsideServiceDown as error:
        reason = f"An outside service stayed down after several tries: {error}."
    except UnreadableImage as error:
        # Only a job whose photos were kept before they had to be in a readable format.
        reason = f"{error}. Only {IMAGE_TYPE_NAMES} photos can be shown to the model."
    except Exception:
        logger.exception("%s failed for job %s", while_doing.capitalize(), job.pk)
        record(
            job,
            f"Something went wrong while {while_doing}",
            reason="An unexpected error stopped the job; the details are in the server log.",
            status=Job.Status.FAILED,
        )
        return False
    record(job, f"Could not {could_not}", reason=reason, status=Job.Status.FAILED)
    return False


def _plan_ad(job: Job) -> bool:
    # Run again after a restart, the task only carries on a plan that hadn't finished, so
    # nothing is asked or paid for twice. A plan already made goes on to the next step, in
    # case the restart came before that step was queued.
    if job.status == Job.Status.PLANNED:
        return True
    if job.status not in (Job.Status.PAGE_READ, Job.Status.PLANNING):
        return False
    record(
        job,
        "Planning the ad",
        reason="The plan sets the scenes, what the person says in each, and who says it.",
        status=Job.Status.PLANNING,
    )
    decision = plan(job)
    if decision.question is not None:
        _ask(
            job,
            Question.Kind.PRODUCER,
            decision.question,
            message=f"Asked: {decision.question}",
            reason=decision.reason,
        )
        return False
    count = job.scenes.count()
    record(job, f"Planned {count} scene{'s' if count != 1 else ''}", reason=decision.reason)
    return True


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
            Scene(job=job, number=number, line=scene.line)
            for number, scene in enumerate(planned.scenes, start=1)
        )
        job.product_colour = planned.product_colour
        job.person_looks = planned.person_looks
        job.person_voice = planned.person_voice
        job.status = Job.Status.PLANNED
        job.save(update_fields=["product_colour", "person_looks", "person_voice", "status"])
        job.photos.filter(position__in=planned.colour_photos).update(shows_product_colour=True)
    return decision


PORTRAIT_PROMPT = """\
A photorealistic vertical portrait of the person who presents a video ad, looking \
straight at the camera with a friendly expression, head and shoulders in frame, lit \
naturally. Not a real, famous person. No text, logos or products in the picture. The \
person: {looks}"""


def _make_person(job: Job) -> bool:
    # Run again after a restart, the task only makes what it hadn't made yet, so the
    # portrait and the voice are never paid for twice. A person already made goes on to the
    # checks, in case the restart came before they were queued.
    if job.status == Job.Status.CHECKING_PLAN:
        return True
    if job.status not in (Job.Status.PLANNED, Job.Status.MAKING_PERSON):
        return False
    record(
        job,
        "Making the person",
        reason="The person who presents the ad gets a portrait, and a voice made to match.",
        status=Job.Status.MAKING_PERSON,
    )
    _, voice = create_person(job)
    record(
        job,
        "Made the person",
        reason=(
            f"The voice speaks {voice.words_per_second:.1f} words a second, measured on the "
            "script itself, so the script's length is known before anything is rendered."
        ),
    )
    return True


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
                    prompt=PORTRAIT_PROMPT.format(looks=job.person_looks),
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
                description=job.person_voice,
                sample=job.scenes.values_list("line", flat=True)[0],
            )
        )
        voice = ProducedItem.objects.create(
            job=job, kind=ProducedItem.Kind.VOICE, voice_id=voice_id
        )
    if voice.words_per_second is None:
        _measure_voice(job, voice)
    job.status = Job.Status.CHECKING_PLAN
    job.save(update_fields=["status"])
    return portrait, voice


def _paid_for_before(job: Job, purpose: str) -> dict[str, Any] | None:
    """What a call for `purpose` made before the worker stopped, if it was paid for but not
    kept. Every call is recorded as soon as it succeeds, so a restart reuses what it made."""
    call = job.model_calls.filter(purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED).last()
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


def _check_plan(job: Job) -> bool:
    # Run again after a restart, or after an answer, the checks carry on from where they
    # stopped: lines already checked stay checked.
    if job.status != Job.Status.CHECKING_PLAN:
        return False
    asking = run_checks(job)
    if asking is None:
        record(job, "Checked the plan", reason=why_the_checks_passed(job))
    elif asking.about == "unclear_page":
        _ask(
            job,
            Question.Kind.UNCLEAR_PAGE,
            asking.question,
            message=f"Asked: {asking.question}",
            reason=asking.reason,
        )
    elif asking.about == "line":
        assert asking.scene is not None
        _ask(
            job,
            Question.Kind.FACT_CHECK,
            f"{asking.question} Keep this line, or give your own?",
            message=f"Asked about scene {asking.scene.number}'s line",
            reason=asking.reason,
            scene=asking.scene,
            options=[
                {"value": choice.value, "label": choice.label} for choice in Question.LineChoice
            ],
        )
    else:
        _ask(
            job,
            Question.Kind.LENGTH,
            f"{asking.question} Shorten it to fit, or keep it longer?",
            message="Asked whether to shorten the script",
            reason=asking.reason,
            options=[
                {"value": Job.LengthChoice.SHORTEN, "label": "Shorten it to fit"},
                {"value": Job.LengthChoice.KEEP_LONGER, "label": "Keep it longer"},
            ],
        )
    return False


@dataclass(frozen=True)
class Asking:
    """Something the planning checks can't settle without the user: what it is about, the
    question with the facts they need to answer it, and one sentence on why it is asked."""

    about: Literal["unclear_page", "line", "length"]
    question: str
    reason: str
    # The scene whose line is asked about, for a line.
    scene: Scene | None = None


def run_checks(job: Job) -> Asking | None:
    """Check the script before anything is rendered: every line against the page, then the
    whole script against the target length. Each problem is fixed, or asked about. None
    once every check has passed.

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
        _rewrite_line(job, scene, conversation)
    return None


def _needs_fixing(scene: Scene) -> bool:
    """Whether the scene's line failed its last fact check and hasn't been rewritten since."""
    return bool(scene.fact_problems) and not scene.fact_problems[-1]["rewritten"]


def _checked(job: Job, scenes: list[Scene], conversation: list[ChatMessage]) -> Asking | None:
    """Fact-check the scenes' lines, storing why each that failed did. When the page itself
    is unclear, gives back what to ask the user instead."""
    check = call_model(
        job=job,
        purpose="fact_check",
        instructions=FACT_CHECK_INSTRUCTIONS,
        handoff=FactCheckHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            lines=[LineToCheck(scene=scene.number, line=scene.line) for scene in scenes],
        ),
        output=fact_check_for([scene.number for scene in scenes]),
    )
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
        assert verdict.problem is not None and verdict.page_says is not None
        scene.fact_problems.append(
            {"problem": verdict.problem, "page_says": verdict.page_says, "rewritten": False}
        )
        scene.save(update_fields=["fact_problems"])
    return None


def _rewrite_line(job: Job, scene: Scene, conversation: list[ChatMessage]) -> None:
    rewrite = call_model(
        job=job,
        purpose="rewrite_line",
        instructions=REWRITE_INSTRUCTIONS,
        handoff=RewriteHandoff(
            page_text=page.for_model(job.page_text),
            conversation=conversation,
            product_colour=job.product_colour,
            script=[LineToCheck(scene=each.number, line=each.line) for each in job.scenes.all()],
            scene=scene.number,
            problems=[
                Problem(problem=problem["problem"], page_says=problem["page_says"])
                for problem in scene.fact_problems
            ],
        ),
        output=RewrittenLine,
    )
    last = scene.fact_problems[-1]
    last["rewritten"] = True
    with transaction.atomic():
        scene.line = rewrite.line
        scene.save(update_fields=["line", "fact_problems"])
        record(
            job,
            f"Rewrote scene {scene.number}'s line: {rewrite.line}",
            reason=f"The fact check failed it: {last['problem']}",
        )


def _about_line(scene: Scene) -> Asking:
    last = scene.fact_problems[-1]
    return Asking(
        about="line",
        question=(
            f"Scene {scene.number}'s line still fails the fact check after "
            f'{MOST_REWRITES} rewrites: "{scene.line}" {last["problem"]} The page says: '
            f"{last['page_says']}"
        ),
        reason=(
            f"The line was rewritten {MOST_REWRITES} times and still failed the fact check, "
            "so you decide: the check itself may be wrong."
        ),
        scene=scene,
    )


def _fit_length(job: Job) -> bool | Asking:
    """Whether the script can go on to be rendered at its length. If it can't, it is
    shortened, giving False so the checks go round again, or the user is asked."""
    target = job.target_seconds
    if target is None or job.length_choice == Job.LengthChoice.KEEP_LONGER:
        return True
    voice = latest(job, ProducedItem.Kind.VOICE)
    assert voice is not None and voice.words_per_second is not None
    words_per_second = voice.words_per_second
    lines = list(job.scenes.values_list("line", flat=True))
    seconds = script_seconds(lines, words_per_second)
    if fits_target(seconds, target):
        return True
    if (
        job.length_choice == Job.LengthChoice.SHORTEN
        and _shortened_since_the_user_spoke(job) < MOST_REWRITES
    ):
        _shorten(job, lines, seconds, words_per_second)
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
            f"than 1 second over the {target} seconds you asked for."
        ),
    )


def _shortened_since_the_user_spoke(job: Job) -> int:
    """Times the script was shortened since the user last said anything. Each time they
    choose to shorten it, it gets MOST_REWRITES more tries before they are asked again."""
    shortened = job.model_calls.filter(
        purpose="shorten_script", outcome=ModelCall.Outcome.SUCCEEDED
    )
    spoke = _last_heard_from_the_user(job)
    return (shortened.filter(created_at__gt=spoke) if spoke else shortened).count()


def _last_heard_from_the_user(job: Job) -> datetime | None:
    if job.session is None:
        # A job started without a session hears from the user only through its questions.
        answered: datetime | None = job.questions.aggregate(last=Max("answered_at"))["last"]
        return answered
    spoke: datetime | None = job.session.messages.filter(role=Message.Role.USER).aggregate(
        last=Max("created_at")
    )["last"]
    return spoke


def _shorten(job: Job, lines: list[str], seconds: float, words_per_second: float) -> None:
    assert job.target_seconds is not None
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
            script=lines,
        ),
        output=ShortenedScript,
    )
    with transaction.atomic():
        scenes = list(job.scenes.all())
        # A line that passed the fact check word for word still has; anything else is new.
        checked = {scene.line for scene in scenes if scene.fact_checked}
        for scene, line in zip(scenes, shortened.lines, strict=False):
            if scene.line != line:
                scene.line = line
                scene.fact_checked = line in checked
                scene.fact_problems = []
                scene.save(update_fields=["line", "fact_checked", "fact_problems"])
        for scene in scenes[len(shortened.lines) :]:
            scene.delete()
        Scene.objects.bulk_create(
            Scene(job=job, number=number, line=line)
            for number, line in enumerate(shortened.lines, start=1)
            if number > len(scenes)
        )
        count = len(shortened.lines)
        record(
            job,
            f"Shortened the script to {count} scene{'s' if count != 1 else ''}",
            reason=(
                f"It ran about {seconds:.1f} seconds, over your {job.target_seconds}-second target."
            ),
        )


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


def latest(job: Job, kind: ProducedItem.Kind) -> ProducedItem | None:
    """The job's latest version of `kind`, if it has one."""
    return job.produced.filter(kind=kind).order_by("version").last()


def _conversation(job: Job) -> list[ChatMessage]:
    """What the user and the producer have said, for the models that plan and check the ad.
    Facts may come from the user's words; the producer's show what the user was answering."""
    if job.session is None:
        # A job started without a session talks to the user only through its questions.
        said = []
        for asked in job.questions.filter(
            kind__in=[Question.Kind.PRODUCER, Question.Kind.UNCLEAR_PAGE],
            answered_at__isnull=False,
        ):
            said += [
                ChatMessage(by="producer", text=asked.question),
                ChatMessage(by="user", text=asked.answer),
            ]
        return said
    return [
        ChatMessage(
            by="user" if message.role == Message.Role.USER else "producer",
            text=messages.as_read(message),
        )
        for message in job.session.messages.prefetch_related("attachments")
    ]


# What the job waits in while each kind of question is open.
WAITING_STATUS = {
    Question.Kind.WORKING_LINK: Job.Status.NEEDS_WORKING_LINK,
    Question.Kind.PRODUCT_PHOTOS: Job.Status.NEEDS_PRODUCT_PHOTOS,
    Question.Kind.PRODUCER: Job.Status.NEEDS_ANSWER,
    Question.Kind.UNCLEAR_PAGE: Job.Status.NEEDS_ANSWER,
    Question.Kind.FACT_CHECK: Job.Status.NEEDS_ANSWER,
    Question.Kind.LENGTH: Job.Status.NEEDS_ANSWER,
}


def _ask(
    job: Job,
    kind: Question.Kind,
    question: str,
    *,
    message: str,
    reason: str,
    scene: Scene | None = None,
    options: list[dict[str, Any]] | None = None,
) -> None:
    """Put a question to the user and leave the job waiting for the answer. With `options`
    the user picks one instead of typing an answer."""
    with transaction.atomic():
        Question.objects.create(
            job=job,
            kind=kind,
            question=question,
            reason=reason,
            scene=scene,
            options=options or [],
        )
        record(job, message, reason=reason, status=WAITING_STATUS[kind])
