"""The work the producer's tools do on a job: reading its page, planning it, making its
person, running the planning checks, making each scene and assembling the finished ad."""

import io
import mimetypes
import tempfile
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from django.db import transaction
from django.db.models import Max

from adforge import file_store
from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Message
from gateway.gateway import (
    IMAGE_TYPE_NAMES,
    IMAGE_TYPES,
    call_model,
    collect_clip,
    design_voice,
    draw_picture,
    edit_picture,
    speak,
    submit_clip,
    transcribe,
    transcription_from,
    transcription_output,
)
from gateway.models import ModelCall
from gateway.types import ClipFailed, Handoff, Image, Judgement

from . import assembly, page
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
from .models import Job, ProducedItem, ProductPhoto, Scene, SceneStep
from .planning import (
    PLAN_INSTRUCTIONS,
    ChatMessage,
    PlanHandoff,
    ProducerDecision,
    producer_decision_for,
)
from .scenes import (
    CLIP_MOTION_PROMPT,
    STARTING_PICTURE_INSTRUCTIONS,
    StartingPictureHandoff,
    starting_picture_choice_for,
)

if TYPE_CHECKING:
    from agents.models import ToolCall

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


def keep_page(job: Job, download: page.Download, product_page: page.ProductPage) -> None:
    """Store the page's text and its original HTML with the job. Only for a page found to
    show its product, whose photos are kept: the job counts as having its page from then."""
    job.page_text = product_page.text
    job.page_html_key = file_store.save(f"jobs/{job.pk}/page.html", download.content)
    job.status = Job.Status.PAGE_READ
    job.save(update_fields=["page_text", "page_html_key", "status"])


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
    scene.fact_problems[-1]["rewritten"] = True
    scene.change_line(rewrite.line)
    scene.save(update_fields=["line", "status", "fact_problems"])


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
        _shorten(job, lines, words_per_second)
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
        # A job started before sessions existed has no conversation.
        return None
    spoke: datetime | None = job.session.messages.filter(role=Message.Role.USER).aggregate(
        last=Max("created_at")
    )["last"]
    return spoke


def _shorten(job: Job, lines: list[str], words_per_second: float) -> None:
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
                scene.change_line(line)
                scene.fact_checked = line in checked
                scene.fact_problems = []
                scene.save(update_fields=["line", "status", "fact_checked", "fact_problems"])
        for scene in scenes[len(shortened.lines) :]:
            scene.delete()
        Scene.objects.bulk_create(
            Scene(job=job, number=number, line=line)
            for number, line in enumerate(shortened.lines, start=1)
            if number > len(scenes)
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


def make_starting_picture(step: SceneStep) -> ProducedItem:
    """Make the scene's starting picture: a model picks the product photo that suits the
    line best and writes the prompt, then the picture is made from the portrait and that
    photo. Gives back the picture, kept as the scene's next version.

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
    choice = call_model(
        job=job,
        purpose="choose_starting_picture",
        instructions=STARTING_PICTURE_INSTRUCTIONS,
        handoff=StartingPictureHandoff(
            scene=scene.number,
            line=step.line,
            script=list(job.scenes.values_list("line", flat=True)),
            product_colour=job.product_colour,
            colour_photos=numbers,
            person_looks=job.person_looks,
            note=step.note or None,
            conversation=_conversation(job, until=step.started_at),
        ),
        output=starting_picture_choice_for(numbers),
        images=[
            Image(label="The portrait", key=portrait.file),
            *(Image(label=f"Photo {photo.position}", key=photo.file) for photo in photos),
        ],
        pay_once=True,
    )
    step.photo = job.photos.get(position=choice.photo)
    step.photo_reason = choice.photo_reason
    step.prompt = choice.prompt
    step.prompt_reason = choice.prompt_reason
    step.save(update_fields=["photo", "photo_reason", "prompt", "prompt_reason"])
    paid_for = _paid_for_before(job, "make_starting_picture", charged_to=step.tool_call)
    file = (
        paid_for["file"]
        if paid_for
        else edit_picture(
            job=job,
            purpose="make_starting_picture",
            prompt=choice.prompt,
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
    the step started. Gives back the audio, kept as the scene's next version.

    Run again, as after a worker stopped, it pays for nothing already paid for: audio made
    but not kept is kept rather than spoken again."""
    made = step.produced.first()
    if made is not None:
        return made
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
    finished.

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
    video_id = _clip_asked_for(step, picture, audio) or (
        submit_clip(
            job=job,
            purpose="make_clip",
            picture_key=picture.file,
            audio_key=audio.file,
            audio_seconds=audio.seconds,
            motion_prompt=CLIP_MOTION_PROMPT,
        )
    )

    def tell_its_slow() -> None:
        messages.add(
            step.tool_call.session,
            role=Message.Role.AGENT,
            text=(
                f"Scene {scene.number}'s clip is taking longer than usual. It's still being "
                "made, and I'll tell you when it's ready."
            ),
        )

    fetched = _paid_for_before(job, "collect_clip", charged_to=step.tool_call)
    file = (
        fetched["file"]
        if fetched
        else collect_clip(
            job=job, purpose="collect_clip", video_id=video_id, when_slow=tell_its_slow
        )
    )
    with transaction.atomic():
        clip = ProducedItem.objects.create(
            job=job,
            scene=scene,
            step=step,
            kind=ProducedItem.Kind.CLIP,
            version=_next_version(scene, ProducedItem.Kind.CLIP),
            file=file,
            # The video model makes a clip as long as the audio it speaks.
            seconds=audio.seconds,
            made_from=audio,
            picture=picture,
        )
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
    return clip


def _clip_asked_for(step: SceneStep, picture: ProducedItem, audio: ProducedItem) -> str | None:
    """The id of a clip already paid for from this picture and audio that may still be made,
    so it is waited for rather than paid for again: asked for by this step before the worker
    stopped, or by an earlier one that stopped or gave up while the video service was down.
    None if there is none, or the video model said it couldn't make it."""
    job = step.scene.job
    asked_by_this_step = _paid_for_before(job, "make_clip", charged_to=step.tool_call)
    if asked_by_this_step:
        return str(asked_by_this_step["video_id"])
    handoff = {"picture": picture.file, "audio": audio.file, "motion_prompt": CLIP_MOTION_PROMPT}
    asked = job.model_calls.filter(
        purpose="make_clip", outcome=ModelCall.Outcome.SUCCEEDED, handoff=handoff
    ).last()
    if asked is None or asked.output is None:
        return None
    video_id = str(asked.output["video_id"])
    # Given up on while the service was down, it may still be made. Failed (ClipFailed),
    # it never will be.
    failed = job.model_calls.filter(
        purpose="collect_clip",
        handoff={"video_id": video_id},
        error__startswith=f"{ClipFailed.__name__}:",
    ).exists()
    return None if failed else video_id


def assemble_ad(job: Job, scenes: list[tuple[ProducedItem, ProducedItem]]) -> ProducedItem:
    """Put the finished ad together from each scene's clip and the transcript of the audio
    it speaks, in the order the scenes play: each clip cut to where its words are said, then
    joined. Gives back the ad, kept as the job's next version. Costs nothing: no model is
    called."""
    measured = []
    for clip, transcript in scenes:
        assert clip.scene is not None and clip.seconds is not None, "a clip is a scene's, measured"
        measured.append((clip.scene.number, clip.pk, clip.seconds, transcript.words))
    planned = assembly.cuts(measured)
    # ffmpeg reads and writes files on this machine, and the clips are in the file store.
    with tempfile.TemporaryDirectory() as folder:
        parts = []
        for (clip, _), cut in zip(scenes, planned, strict=True):
            path = Path(folder) / f"scene-{cut.scene}.mp4"
            path.write_bytes(file_store.read(clip.file))
            parts.append((path, cut))
        ad = Path(folder) / "ad.mp4"
        assembly.join(parts, ad)
        file = file_store.save("ad.mp4", ad.read_bytes())
    last = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).aggregate(last=Max("version"))
    return ProducedItem.objects.create(
        job=job,
        kind=ProducedItem.Kind.FINISHED_AD,
        version=(last["last"] or 0) + 1,
        file=file,
        seconds=planned[-1].end,
        cuts=[asdict(cut) for cut in planned],
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
    user's words; the producer's show what the user was answering."""
    if job.session is None:
        # A job started before sessions existed has no conversation.
        return []
    said = job.session.messages.prefetch_related("attachments")
    if until is not None:
        said = said.filter(created_at__lte=until)
    return [
        ChatMessage(
            by="user" if message.role == Message.Role.USER else "producer",
            text=messages.as_read(message),
        )
        for message in said
    ]
