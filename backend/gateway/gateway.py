"""The one place every model call goes through.

It checks the handoff before anything is spent, retries when the provider is down, and
records every attempt: what was called, what it cost, how long it took, whether it worked,
and the one-sentence judgement."""

import hashlib
import io
import json
import logging
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import PIL.Image
import PIL.ImageOps
from django.conf import settings
from django.db import connection
from django.utils import timezone
from pydantic import BaseModel

from adforge import file_store, tracing
from adforge.retry import OutsideServiceDown, with_retries

from . import catalog
from .models import ModelCall, SystemPrompt
from .types import (
    AgentProvider,
    BlockedBySafetyFilter,
    BrollClipHandoff,
    BrollClipProvider,
    ClipCollectHandoff,
    ClipCollector,
    ClipFailed,
    ClipHandoff,
    ClipProvider,
    Handoff,
    Happened,
    Image,
    Judgement,
    LoadedImage,
    LoadedPiece,
    LoadedVideo,
    ModelProvider,
    ModelRequest,
    MusicHandoff,
    MusicProvider,
    Picture,
    PictureEditHandoff,
    PictureProvider,
    PortraitHandoff,
    ReferenceVideo,
    RefusedForMoney,
    SpeechHandoff,
    ToolRequest,
    ToolSpec,
    ToolUse,
    Transcription,
    TranscriptionHandoff,
    TranscriptionProvider,
    Turn,
    TurnHandoff,
    TurnRequest,
    UnusableReply,
    VideoProvider,
    VideoRequest,
    VoiceDesignHandoff,
    VoiceProvider,
    Word,
)

if TYPE_CHECKING:
    from agents.models import ToolCall
    from chat.models import Session
    from jobs.models import Job

    from .creatify_adapter import CreatifyProvider
    from .elevenlabs_adapter import ElevenLabsProvider
    from .fal_adapter import FalProvider
    from .gemini_adapter import GeminiProvider
    from .heygen_adapter import HeyGenProvider
    from .inworld_adapter import InworldProvider
    from .openai_adapter import OpenAIProvider

logger = logging.getLogger(__name__)

_override: object | None = None

# The tool call whose work is running now. Every model call made meanwhile is recorded
# against it, so a tool call's cost is the sum of its model calls.
_running_tool: ContextVar[ToolCall | None] = ContextVar("running_tool", default=None)


def running_tool() -> ToolCall | None:
    """The tool call whose work is running now, if any."""
    return _running_tool.get()


# The picture formats models can read. Anything else is refused before money is spent.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
# The same formats, as a person would name them.
IMAGE_TYPE_NAMES = "PNG, JPEG, WebP or GIF"
# Every image goes to every model sharp, at the "high" detail the adapter asks for, so small
# print on a label, a thumbnail in a page screenshot or a shade of colour can be read. A
# bigger image is shrunk to fit this first: the model looks at no more than this anyway, and
# the request stays small. Never 512 px, never low: decided in #1 step 1.
MAX_IMAGE_SIDE = 2048


class UnreadableImage(ValueError):
    """An image models can't read: in another format, or not a picture at all."""


def _wait(seconds: float) -> None:
    time.sleep(seconds)


@cache
def _openai() -> OpenAIProvider:
    from .openai_adapter import OpenAIProvider

    return OpenAIProvider()


@cache
def _inworld() -> InworldProvider:
    from .inworld_adapter import InworldProvider

    return InworldProvider()


@cache
def _elevenlabs() -> ElevenLabsProvider:
    from .elevenlabs_adapter import ElevenLabsProvider

    return ElevenLabsProvider()


@cache
def _creatify() -> CreatifyProvider:
    from .creatify_adapter import CreatifyProvider

    return CreatifyProvider()


@cache
def _heygen() -> HeyGenProvider:
    from .heygen_adapter import HeyGenProvider

    return HeyGenProvider()


@cache
def _fal() -> FalProvider:
    from .fal_adapter import FalProvider

    return FalProvider()


@cache
def _gemini() -> GeminiProvider:
    from .gemini_adapter import GeminiProvider

    return GeminiProvider()


def _provider() -> ModelProvider:
    return cast(ModelProvider, _override) if _override is not None else _openai()


def _pictures() -> PictureProvider:
    return cast(PictureProvider, _override) if _override is not None else _openai()


def _voices() -> VoiceProvider:
    return cast(VoiceProvider, _override) if _override is not None else _inworld()


def _transcribers() -> TranscriptionProvider:
    return cast(TranscriptionProvider, _override) if _override is not None else _elevenlabs()


def _clips(model: str) -> ClipCollector:
    """The service that makes clips with `model`: talking clips and B-roll ones each have
    their own, so a clip is always waited for at the service it was asked of. A model the
    catalog names that no service here makes is a mistake, not the service's."""
    if _override is not None:
        return cast(ClipCollector, _override)
    return _CLIP_SERVICES[model]()


# Which service makes clips with which model. Each is made once and kept.
_CLIP_SERVICES: dict[str, Callable[[], ClipCollector]] = {
    "heygen/avatar-iv": _heygen,
    "creatify/boreal-h3": _creatify,
}


def _music() -> MusicProvider:
    return cast(MusicProvider, _override) if _override is not None else _fal()


def _watchers() -> VideoProvider:
    return cast(VideoProvider, _override) if _override is not None else _gemini()


def _agents() -> AgentProvider:
    return cast(AgentProvider, _override) if _override is not None else _openai()


@contextmanager
def use_model(provider: object) -> Iterator[None]:
    """Send every model call to `provider` inside this block, whatever it is for, so a test
    never reaches a real service. Tests use it to swap in a fake."""
    global _override
    previous, _override = _override, provider
    try:
        yield
    finally:
        _override = previous


@contextmanager
def charged_to(tool_call: ToolCall) -> Iterator[None]:
    """Record every model call made inside this block against `tool_call`."""
    token = _running_tool.set(tool_call)
    try:
        yield
    finally:
        _running_tool.reset(token)


def take_turn(
    *,
    session: Session,
    purpose: str,
    instructions: str,
    conversation: Sequence[Happened],
    tools: Sequence[ToolSpec],
    since: datetime | None = None,
) -> Turn:
    """Give an agent its conversation and the tools it may call, and have it take one turn:
    say something, ask for tools, or both. The pictures its tools handed back are read here
    from the file store and shown with their labels, as `call_model`'s images are. An agent
    whose conversations start afresh, as the decider's each round, passes when this one
    started (`since`): a turn taken in an earlier one, handed the same words, isn't this
    one's to take again."""
    handoff = TurnHandoff(conversation=list(conversation), tools=[tool.name for tool in tools])
    # Validate again here rather than trusting the caller built the handoff properly.
    handoff = TurnHandoff.model_validate(handoff.model_dump())
    shown = [
        Image(label=picture.label, key=picture.key)
        for each in handoff.conversation
        if isinstance(each, ToolUse)
        for picture in each.pictures
    ]
    request = TurnRequest(
        purpose=purpose,
        model=catalog.MODEL_FOR_PURPOSE[purpose],
        instructions=instructions,
        handoff=handoff,
        tools=tuple(tools),
        images=tuple(_load(image) for image in shown),
    )
    images = _shown(shown)
    # A turn paid for before the worker stopped, but not yet kept, is taken again for free.
    answered = _answered_before(
        handoff, purpose=purpose, session=session, images=images, since=since
    )
    if answered is not None:
        return _turn_from(answered)
    provider = _agents()

    def take() -> _Made[Turn]:
        reply = provider.take_turn(request)
        # Kept with what the turn did, so its record and its trace show why it chose.
        reasoning = {"reasoning": reply.reasoning} if reply.reasoning else {}
        return _Made(
            result=reply.turn,
            output={**_turn_output(reply.turn), **reasoning},
            bill=_Bill(
                cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            ),
        )

    return _recorded(
        None,
        purpose,
        request.model,
        provider.name,
        handoff,
        take,
        images,
        session=session,
        told=_Told(instructions, tuple(tools)),
    )


def last_turn_given(session: Session, purpose: str) -> list[Happened] | None:
    """The conversation an agent was given for its last turn paid for in the session, or
    None if it hasn't taken one."""
    last = ModelCall.objects.filter(
        session=session, purpose=purpose, outcome=ModelCall.Outcome.SUCCEEDED
    ).last()
    return TurnHandoff.model_validate(last.handoff).conversation if last else None


def _turn_output(turn: Turn) -> dict[str, Any]:
    """A turn as its model call records it."""
    return {
        "says": turn.says,
        "calls": [
            {"call_id": call.call_id, "tool": call.tool, "arguments": call.arguments}
            for call in turn.calls
        ],
    }


def _turn_from(output: dict[str, Any]) -> Turn:
    """The turn a model call recorded."""
    return Turn(says=output["says"], calls=tuple(ToolRequest(**call) for call in output["calls"]))


def call_model[Out: BaseModel](
    *,
    job: Job | None,
    purpose: str,
    instructions: str,
    handoff: Handoff,
    output: type[Out],
    images: Sequence[Image] = (),
    pay_once: bool = False,
    images_may_move: bool = False,
) -> Out:
    """Ask a model for `output`. `images` are pictures shown alongside the handoff, read
    here from the file store so the record of which were shown can't disagree with what
    was sent. With `pay_once`, what the job's model already answered for this same handoff
    and images is handed back, and nothing is paid. With `images_may_move` too, the same
    handoff is enough: for pictures stored again under new keys, as a page's photos are when
    it is read again, and named in the handoff by where they came from."""
    # Validate again here rather than trusting the caller built the handoff properly.
    handoff = type(handoff).model_validate(handoff.model_dump())
    request = ModelRequest(
        purpose=purpose,
        model=catalog.MODEL_FOR_PURPOSE[purpose],
        instructions=instructions,
        handoff=handoff,
        output=output,
        images=tuple(_load(image) for image in images),
    )
    if pay_once and job is not None:
        answered = _answered_before(
            handoff,
            purpose=purpose,
            job=job,
            images=_shown(request.images),
            images_may_move=images_may_move,
        )
        if answered is not None:
            return output.model_validate(answered)
    provider = _provider()

    def complete() -> _Made[Out]:
        reply = provider.complete(request)
        judgement = reply.output if isinstance(reply.output, Judgement) else None
        return _Made(
            result=reply.output,
            output=reply.output.model_dump(mode="json"),
            bill=_Bill(
                cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            ),
            decision=judgement.decision if judgement else "",
            reason=judgement.reason if judgement else "",
        )

    return _recorded(
        job,
        purpose,
        request.model,
        provider.name,
        handoff,
        complete,
        _shown(request.images),
        told=_Told(instructions),
    )


def past_the_safety_filter[Answer](
    ask: Callable[[list[Image]], Answer],
    images: Sequence[Image],
    *,
    may_leave_out: Callable[[Image], bool] = lambda _: True,
    fewest_kept: int = 0,
) -> tuple[Answer, list[Image]]:
    """`ask`'s answer shown `images`, and the pictures left out to get it (requirement 22). A
    model's safety filter refuses a whole call over one picture it objects to, often wrongly,
    so when it refuses, the pictures `may_leave_out` allows are left out one at a time, last
    first, then all together, and the first answer had is given back. A try that would keep
    fewer than `fewest_kept` of those pictures isn't made. `ask` is handed the pictures to
    show, in their order, and builds the rest of the call from them. Raises the filter's
    BlockedBySafetyFilter when every try is refused."""
    images = list(images)
    try:
        return ask(images), []
    except BlockedBySafetyFilter as error:
        refused = error
    # A picture shown twice is one picture: left out, every copy of it goes.
    droppable = list(dict.fromkeys(image for image in images if may_leave_out(image)))
    tries = [[image] for image in reversed(droppable)]
    if len(droppable) > 1:
        tries.append(droppable)
    for left in tries:
        if len(droppable) - len(left) < fewest_kept:
            continue
        try:
            return ask([image for image in images if image not in left]), left
        except BlockedBySafetyFilter as error:
            refused = error
    raise refused


def watch_video[Out: BaseModel](
    *,
    job: Job | None,
    purpose: str,
    instructions: str,
    handoff: Handoff,
    video_key: str,
    output: type[Out],
) -> Out:
    """Ask a model for `output` from the handoff and from what it sees and hears in the video
    given by its key in the file store. The video is sent cut to WATCH_VIDEO_SHORT_SIDE px on
    its short side, with its sound, looked at WATCH_VIDEO_FRAMES_PER_SECOND frames a second;
    the cut is kept in the file store, and the call's record names it, so the record of what
    was watched can't disagree with what was sent."""
    # Validate again here rather than trusting the caller built the handoff properly.
    handoff = type(handoff).model_validate(handoff.model_dump())
    sent = _watchable(video_key)
    request = VideoRequest(
        purpose=purpose,
        model=catalog.MODEL_FOR_PURPOSE[purpose],
        instructions=instructions,
        handoff=handoff,
        output=output,
        video=LoadedVideo(key=sent, media_type="video/mp4", data=file_store.read(sent)),
        frames_per_second=settings.WATCH_VIDEO_FRAMES_PER_SECOND,
    )
    provider = _watchers()

    def watch() -> _Made[Out]:
        reply = provider.watch(request)
        return _Made(
            result=reply.output,
            output=reply.output.model_dump(mode="json"),
            bill=_Bill(
                cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            ),
        )

    return _recorded(
        job,
        purpose,
        request.model,
        provider.name,
        handoff,
        watch,
        video=sent,
        told=_Told(instructions),
    )


def _watchable(video_key: str) -> str:
    """The video, given by its key in the file store, as it is sent to be watched: an MP4
    cut to WATCH_VIDEO_SHORT_SIDE px on its short side, or as it is when smaller, with its
    sound. Made once and kept beside it; returns its key."""
    side = settings.WATCH_VIDEO_SHORT_SIDE
    original = PurePosixPath(video_key)
    name = str(original.with_name(f"{original.stem}.watched-{side}px.mp4"))
    # The short side at most `side`, and even, as H.264 needs; the long one in proportion.
    width = f"trunc(min({side},iw)/2)*2"
    height = f"trunc(min({side},ih)/2)*2"
    scale = f"scale='if(lt(iw,ih),{width},-2)':'if(lt(iw,ih),-2,{height})'"

    def cut() -> bytes:
        with tempfile.TemporaryDirectory() as folder:
            given = Path(folder) / "given"
            made = Path(folder) / "watched.mp4"
            given.write_bytes(file_store.read(video_key))
            try:
                subprocess.run(
                    [settings.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error"]
                    + ["-i", str(given), "-map", "0:v:0", "-map", "0:a:0?", "-vf", scale]
                    + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
                    + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"]
                    + ["-movflags", "+faststart", str(made)],
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
            except subprocess.CalledProcessError as error:
                said = error.stderr.decode(errors="replace").strip()[-2000:]
                raise VideoNotWatchable(
                    f"ffmpeg couldn't cut the video {video_key} to be watched: {said}"
                ) from error
            except subprocess.TimeoutExpired as error:
                raise VideoNotWatchable(
                    f"ffmpeg took over {error.timeout:g} s to cut the video {video_key}"
                ) from error
            return made.read_bytes()

    return file_store.save_once(name, cut, whole=_whole_mp4)


class VideoNotWatchable(Exception):
    """A video ffmpeg can't cut to be watched (no picture in it, a broken file, or too long a
    cut), with what ffmpeg said."""


def _whole_mp4(data: bytes) -> bool:
    """Whether `data` is an MP4 written to its end: its top-level boxes run exactly to the
    last byte. A cut a stopped worker left half-saved ends inside a box."""
    at = 0
    while at < len(data):
        if at + 8 > len(data):
            return False
        size = int.from_bytes(data[at : at + 4], "big")
        if size == 1:  # The size is the 64-bit one after the box's type.
            if at + 16 > len(data):
                return False
            size = int.from_bytes(data[at + 8 : at + 16], "big")
        elif size == 0:  # The last box, running to the end.
            return True
        if size < 8:
            return False
        at += size
    return at == len(data) > 0


def draw_picture(*, job: Job | None, purpose: str, prompt: str) -> str:
    """Have a model draw a picture. Returns its key in the file store."""
    handoff = PortraitHandoff(prompt=prompt)
    model = catalog.MODEL_FOR_PURPOSE[purpose]

    def draw(provider: PictureProvider, on_azure: bool) -> Picture:
        return provider.draw(model=model, prompt=handoff.prompt, on_azure=on_azure)

    return _picture_made(job, purpose, model, handoff, draw)


def edit_picture(*, job: Job | None, purpose: str, prompt: str, pictures: Sequence[str]) -> str:
    """Have a model make a picture from `pictures`, given by their keys in the file store,
    as `prompt` says. Each is sent whole, not shrunk: they are what the new picture is made
    of. Returns the new picture's key in the file store."""
    handoff = PictureEditHandoff(prompt=prompt, pictures=list(pictures))
    model = catalog.MODEL_FOR_PURPOSE[purpose]

    def edit(provider: PictureProvider, on_azure: bool) -> Picture:
        given = [file_store.read(key) for key in handoff.pictures]
        return provider.edit(model=model, prompt=handoff.prompt, pictures=given, on_azure=on_azure)

    return _picture_made(job, purpose, model, handoff, edit)


# The provider recorded for a picture drawn on our Azure copy of the picture model.
AZURE_PICTURES = "azure"


def _picture_made(
    job: Job | None,
    purpose: str,
    model: str,
    handoff: Handoff,
    make: Callable[[PictureProvider, bool], Picture],
) -> str:
    """A picture `make` made, kept in the file store, its key handed back. It is drawn on
    OpenAI's own servers until OpenAI refuses one of the session's pictures for money; that
    picture, and every later one of the session, is drawn on our Azure copy of the same
    picture model at Azure's pace, and the shop owner is told once (Julian 2026-10-29). Any
    other refusal, such as the safety filter's, a timeout or an outage, never moves them."""
    provider = _pictures()
    tool_call = _running_tool.get()
    session = tool_call.session if tool_call is not None else job.session if job else None
    azure_set_up = bool(settings.AZURE_PICTURES_KEY)
    if not (azure_set_up and _refused_for_money_before(session)):
        try:
            return _recorded(
                job,
                purpose,
                model,
                provider.name,
                handoff,
                lambda: _kept(purpose, model, make(provider, False)),
                session=session,
            )
        except RefusedForMoney as refused:
            if not azure_set_up:
                raise
            _say_pictures_moved_to_azure(job, session, refused)

    # One picture at a time across every worker, each after Azure's pace allows: pictures
    # made at once would otherwise all see the same last call and go together. The wait is
    # outside the call's record, so its time isn't counted as the call's.
    with _one_azure_picture_at_a_time():
        _wait_for_azures_pace()
        return _recorded(
            job,
            purpose,
            model,
            AZURE_PICTURES,
            handoff,
            lambda: _kept(purpose, model, make(provider, True)),
            session=session,
        )


# The Postgres advisory lock held while a picture is drawn on Azure: a number none of the
# app's other advisory locks use (agents/tasks.py, jobs/work.py).
AZURE_PICTURES_LOCK = 5


@contextmanager
def _one_azure_picture_at_a_time() -> Iterator[None]:
    """Hold the lock that lets one picture at a time be drawn on Azure, in every worker. It
    is held by the database connection, so a worker that dies lets it go."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [AZURE_PICTURES_LOCK])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [AZURE_PICTURES_LOCK])


def _refused_for_money_before(session: Session | None) -> bool:
    """Whether OpenAI has refused one of `session`'s calls for money. Read from the calls'
    records, so every worker sees it."""
    if session is None:
        return False
    return ModelCall.objects.filter(
        session=session, error__startswith=f"{RefusedForMoney.__name__}:"
    ).exists()


def _wait_for_azures_pace() -> None:
    """Wait until Azure's picture deployment may be asked again: it draws at most
    AZURE_PICTURES_PER_MINUTE pictures a minute, for every session alike. Timed from when
    the last call there was recorded, which is when it ended, so never sooner than allowed."""
    last = (
        ModelCall.objects.filter(provider=AZURE_PICTURES)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last is None:
        return
    gap = 60 / settings.AZURE_PICTURES_PER_MINUTE - (timezone.now() - last).total_seconds()
    if gap > 0:
        _wait(gap)


def _say_pictures_moved_to_azure(
    job: Job | None, session: Session | None, refused: RefusedForMoney
) -> None:
    """Tell the shop owner in red, with the ad's warning line, that the session's pictures
    are drawn on Azure from now on, and say it in the server log."""
    from jobs.notices import post_pictures_moved_to_azure

    logger.warning(
        "OpenAI refused a picture for money (%s); session %s's pictures are drawn on Azure "
        "from now on",
        refused,
        session.pk if session else None,
    )
    told = job if job is not None else session.jobs.first() if session else None
    if told is not None:
        post_pictures_moved_to_azure(told)


def _kept(purpose: str, model: str, picture: Picture) -> _Made[str]:
    """A picture a model made, kept in the file store, with what it was billed for."""
    key = file_store.save(f"{purpose}.{_picture_extension(picture.data)}", picture.data)
    return _Made(
        result=key,
        output={"file": key},
        bill=_Bill(
            cost_usd=catalog.picture_cost_usd(
                model, picture.input_tokens, picture.output_tokens, picture.picture_input_tokens
            ),
            input_tokens=picture.input_tokens,
            output_tokens=picture.output_tokens,
        ),
    )


def design_voice(*, job: Job | None, purpose: str, description: str, sample: str) -> str:
    """Have a voice designed from a description, heard saying `sample`. Returns its id."""
    handoff = VoiceDesignHandoff(description=description, sample=sample)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _voices()

    def design() -> _Made[str]:
        voice_id = provider.design_voice(
            model=model, description=handoff.description, sample=handoff.sample
        )
        bill = _speech_bill(model, handoff.sample)
        return _Made(result=voice_id, output={"voice_id": voice_id}, bill=bill)

    return _recorded(job, purpose, model, provider.name, handoff, design)


def speak(*, job: Job | None, purpose: str, voice_id: str, text: str) -> str:
    """Have the voice say `text`. Returns the WAV file's key in the file store."""
    handoff = SpeechHandoff(voice_id=voice_id, text=text)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _voices()

    def say() -> _Made[str]:
        audio = provider.speak(model=model, voice_id=handoff.voice_id, text=handoff.text)
        key = file_store.save(f"{purpose}.wav", audio)
        return _Made(result=key, output={"file": key}, bill=_speech_bill(model, handoff.text))

    return _recorded(job, purpose, model, provider.name, handoff, say)


def transcribe(*, job: Job | None, purpose: str, audio_key: str) -> Transcription:
    """Have the audio, given by its key in the file store, written down word by word,
    exactly as it was said."""
    handoff = TranscriptionHandoff(audio=audio_key)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _transcribers()

    def hear() -> _Made[Transcription]:
        heard = provider.transcribe(model=model, audio=file_store.read(handoff.audio))
        if not heard.words:
            raise ValueError(f"{provider.name} heard no words in the audio")
        return _Made(
            result=heard,
            output=transcription_output(heard),
            bill=_Bill(
                audio_seconds=heard.audio_seconds,
                cost_usd=catalog.transcription_cost_usd(model, heard.audio_seconds),
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, hear)


def transcription_output(heard: Transcription) -> dict[str, Any]:
    """A transcription as its model call records it."""
    return {
        "text": heard.text,
        "words": [{"text": w.text, "start": w.start, "end": w.end} for w in heard.words],
        "audio_seconds": heard.audio_seconds,
    }


def transcription_from(output: dict[str, Any]) -> Transcription:
    """The transcription a model call recorded, so one paid for isn't paid for again."""
    return Transcription(
        text=output["text"],
        words=tuple(Word(**word) for word in output["words"]),
        audio_seconds=output["audio_seconds"],
    )


def submit_clip(
    *,
    job: Job | None,
    purpose: str,
    picture_key: str,
    audio_key: str | None,
    seconds: float,
    motion_prompt: str,
) -> str:
    """Ask for a `seconds`-long clip of the picture, given by its key in the file store,
    speaking the audio, also by its key, or with no sound when there is none. This is what
    is paid for: every second of it. Returns the clip's id, to collect it with once it's
    made."""
    handoff = ClipHandoff(
        picture=picture_key, audio=audio_key, seconds=seconds, motion_prompt=motion_prompt
    )
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = cast(ClipProvider, _clips(model))

    def submit() -> _Made[str]:
        video_id = provider.submit(
            picture=file_store.read(handoff.picture),
            audio=None if handoff.audio is None else file_store.read(handoff.audio),
            seconds=handoff.seconds,
            motion_prompt=handoff.motion_prompt,
        )
        return _Made(
            result=video_id,
            output={"video_id": video_id},
            bill=_Bill(
                video_seconds=handoff.seconds,
                cost_usd=catalog.video_cost_usd(model, handoff.seconds),
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, submit)


def submit_broll_clip(
    *,
    job: Job | None,
    purpose: str,
    starting_picture_key: str | None,
    example_picture_keys: Sequence[str],
    seconds: int,
    prompt: str,
    reference_videos: Sequence[ReferenceVideo] = (),
) -> str:
    """Ask for a B-roll clip, with no sound, of `seconds` whole seconds as `prompt` says:
    from a starting picture, its first frame, or from example pictures, never both, each given
    by its key in the file store. This is what is paid for: every second of it. Returns the
    clip's id, to collect it with once it's made."""
    handoff = BrollClipHandoff(
        starting_picture=starting_picture_key,
        example_pictures=list(example_picture_keys),
        reference_videos=list(reference_videos),
        seconds=seconds,
        prompt=prompt,
    )
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = cast(BrollClipProvider, _clips(model))

    def submit() -> _Made[str]:
        video_id = provider.submit_broll(
            starting_picture=None
            if handoff.starting_picture is None
            else file_store.read(handoff.starting_picture),
            example_pictures=[file_store.read(key) for key in handoff.example_pictures],
            seconds=handoff.seconds,
            prompt=handoff.prompt,
            reference_videos=[
                LoadedPiece(data=file_store.read(piece.file), media_type="video/mp4", piece=piece)
                for piece in handoff.reference_videos
            ],
        )
        # Each second of reference video sent costs what a second made does (spec #210,
        # Further Notes: 0.4 credits a second).
        sent = sum(piece.seconds for piece in handoff.reference_videos)
        return _Made(
            result=video_id,
            output={"video_id": video_id},
            bill=_Bill(
                video_seconds=handoff.seconds,
                cost_usd=catalog.video_cost_usd(model, handoff.seconds + sent),
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, submit)


def collect_clip(
    *,
    job: Job | None,
    purpose: str,
    video_id: str,
    when_slow: Callable[[], None] = lambda: None,
) -> str:
    """Wait for the clip asked for as `video_id` to be made, then keep it. Costs nothing: the
    clip was paid for when it was asked for, so it is waited for however long it takes, and
    `when_slow` is called once it has taken CLIP_SLOW_AFTER_SECONDS. Raises ClipFailed if it
    can't be made, and never asks for it again, which would pay for it twice. Returns the
    clip's key in the file store."""
    handoff = ClipCollectHandoff(video_id=video_id)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _clips(model)

    # Counted from the first look, not from each retry, and told of once across retries.
    waited_since = time.monotonic()
    told_slow = False

    def collect() -> _Made[str]:
        nonlocal told_slow
        while (made := provider.status(video_id=handoff.video_id)).state == "working":
            if (
                not told_slow
                and time.monotonic() - waited_since >= settings.CLIP_SLOW_AFTER_SECONDS
            ):
                when_slow()
                told_slow = True
            time.sleep(settings.CLIP_POLL_SECONDS)
        if made.state == "failed" or made.video_url is None:
            raise ClipFailed(made.error or "the video service gave no reason")
        # Fetched and kept at once: the link to it only works for a while.
        key = file_store.save("clip.mp4", provider.download(url=made.video_url))
        return _Made(result=key, output={"file": key}, bill=_Bill(cost_usd=Decimal(0)))

    return _recorded(job, purpose, model, provider.name, handoff, collect)


def make_music(*, job: Job | None, purpose: str, prompt: str, seconds: int) -> str:
    """Have `seconds` of background music made as `prompt` describes. Returns the audio
    file's key in the file store."""
    handoff = MusicHandoff(prompt=prompt, seconds=seconds)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _music()

    def compose() -> _Made[str]:
        music = provider.compose(model=model, prompt=handoff.prompt, seconds=handoff.seconds)
        # Sonilo always sends AAC in an MP4 file.
        key = file_store.save(f"{purpose}.m4a", music)
        return _Made(
            result=key,
            output={"file": key},
            bill=_Bill(
                audio_seconds=handoff.seconds,
                cost_usd=catalog.music_cost_usd(model, handoff.seconds),
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, compose)


@dataclass(frozen=True)
class _Bill:
    """What one call was billed for."""

    cost_usd: Decimal
    input_tokens: int | None = None
    output_tokens: int | None = None
    characters: int | None = None
    audio_seconds: float | None = None
    video_seconds: float | None = None


def _speech_bill(model: str, text: str) -> _Bill:
    return _Bill(characters=len(text), cost_usd=catalog.speech_cost_usd(model, len(text)))


@dataclass(frozen=True)
class _Made[Result]:
    """What one call made, what to record as its output, and what it was billed for."""

    result: Result
    output: dict[str, Any]
    bill: _Bill
    decision: str = ""
    reason: str = ""


@dataclass(frozen=True)
class _Told:
    """What a model is told beside its handoff: its instructions, and any tools offered."""

    instructions: str
    tools: tuple[ToolSpec, ...] = ()

    def listed(self) -> dict[str, Any]:
        """As it is stored and traced: the instructions, and each tool's name, description
        and arguments' JSON schema."""
        return {
            "instructions": self.instructions,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.arguments.model_json_schema(),
                }
                for tool in self.tools
            ],
        }


def _system_prompt(session_id: Any, purpose: str, told: _Told | None) -> SystemPrompt | None:
    """The session's stored system prompt for `purpose` with exactly what `told` holds: the
    one kept already, or else a new one. None for a call told nothing beside its handoff."""
    if told is None or not (told.instructions or told.tools):
        return None
    listed = told.listed()
    digest = hashlib.sha256(json.dumps(listed, sort_keys=True).encode()).hexdigest()
    # Calls made at once, as a judge's asks, find the one row the first of them made.
    kept, _ = SystemPrompt.objects.get_or_create(
        session_id=session_id, purpose=purpose, digest=digest, defaults=listed
    )
    return kept


def _recorded[Result](
    job: Job | None,
    purpose: str,
    model: str,
    provider: str,
    handoff: Handoff,
    make: Callable[[], _Made[Result]],
    images: list[dict[str, str]] | None = None,
    session: Session | None = None,
    video: str = "",
    told: _Told | None = None,
) -> Result:
    """Run `make` with retries, recording every attempt: what was called, what it cost, how
    long it took, whether it worked, and any judgement, and what the model was `told` beside
    its handoff, by the session's stored system prompt. Each is recorded against the session
    it was for: the one given, or else the tool call's or the job's."""
    tool_call = _running_tool.get()
    if session is None and tool_call is not None:
        session = tool_call.session
    session_id = session.pk if session else job.session_id if job else None
    system_prompt = _system_prompt(session_id, purpose, told)
    recorded: dict[str, Any] = {
        "session_id": session_id,
        "job": job,
        "tool_call": tool_call,
        "purpose": purpose,
        "system_prompt": system_prompt,
        "provider": provider,
        "model": model,
        "handoff": handoff.model_dump(mode="json"),
        "images": images or [],
        "video": video,
    }
    attempts = 0

    shown = _files_given(handoff, images) + ([video] if video else [])

    def attempt() -> Result:
        nonlocal attempts
        attempts += 1
        with tracing.model_call(
            purpose,
            model=model,
            told=_told_in_trace(system_prompt),
            handoff=recorded["handoff"],
            shown=shown,
        ) as traced:
            started = time.monotonic()
            try:
                made = make()
            except Exception as error:
                # An unusable answer was still billed, so its cost is recorded like any other.
                billed = error if isinstance(error, UnusableReply) else None
                _send_to_trace(
                    traced,
                    ModelCall.objects.create(
                        **recorded,
                        attempt=attempts,
                        outcome=ModelCall.Outcome.FAILED,
                        error=f"{type(error).__name__}: {error}",
                        input_tokens=billed.input_tokens if billed else None,
                        output_tokens=billed.output_tokens if billed else None,
                        cost_usd=(
                            catalog.cost_usd(model, billed.input_tokens, billed.output_tokens)
                            if billed
                            else None
                        ),
                        duration_ms=_elapsed_ms(started),
                    ),
                )
                raise
            _send_to_trace(
                traced,
                ModelCall.objects.create(
                    **recorded,
                    attempt=attempts,
                    output=made.output,
                    outcome=ModelCall.Outcome.SUCCEEDED,
                    input_tokens=made.bill.input_tokens,
                    output_tokens=made.bill.output_tokens,
                    characters=made.bill.characters,
                    audio_seconds=made.bill.audio_seconds,
                    video_seconds=made.bill.video_seconds,
                    cost_usd=made.bill.cost_usd,
                    duration_ms=_elapsed_ms(started),
                    decision=made.decision,
                    reason=made.reason,
                ),
            )
            return made.result

    try:
        return with_retries(attempt)
    except OutsideServiceDown as error:
        raise OutsideServiceDown(
            f"The model provider was still down after {attempts} tries: {error}"
        ) from error


def _files_given(handoff: Handoff, images: list[dict[str, str]] | None) -> list[str]:
    """The files in the file store a call was given, by their keys: the pictures shown with
    its handoff, and any the handoff names."""
    given = [image["key"] for image in images or []]
    if isinstance(handoff, PictureEditHandoff):
        given += handoff.pictures
    if isinstance(handoff, TranscriptionHandoff):
        given.append(handoff.audio)
    if isinstance(handoff, ClipHandoff):
        given += [key for key in (handoff.picture, handoff.audio) if key is not None]
    if isinstance(handoff, BrollClipHandoff):
        given += (
            [handoff.starting_picture] if handoff.starting_picture else handoff.example_pictures
        )
        given += [piece.file for piece in handoff.reference_videos]
    return given


def _told_in_trace(system_prompt: SystemPrompt | None) -> dict[str, Any]:
    """What a call was told beside its handoff, as its trace shows it: the instructions, and
    the tools when it was offered any."""
    if system_prompt is None:
        return {}
    tools = {"tools": system_prompt.tools} if system_prompt.tools else {}
    return {"instructions": system_prompt.instructions, **tools}


# Each judge's verdicts, sent to Langfuse as scores on its call (spec #247 point 16), by its
# purpose: each score's name, after what it measures, and where its verdict is in the judge's
# answer. A verdict an answer doesn't hold, as a fix's question on any other clip, is no score.
VERDICT_SCORES: dict[str, dict[str, tuple[str, ...]]] = {
    "check_starting_picture": {"starting_picture_real": ()},
    "check_fix": {"fix_made": ()},
    "check_clip_sides": {"clip_sides_real": ()},
    "check_clip_product": {"clip_matches_product": ()},
    "check_clip_references": {"references_enough": ()},
    "check_clip_motion": {
        "clip_real_motion": ("real_motion",),
        "clip_no_sudden_change": ("sudden_change",),
        "clip_does_what_was_ordered": ("does_what_was_ordered",),
        "clip_owner_change_made": ("owner_change",),
    },
}
# Where a judge's answer says what is wrong, beside its verdict: the score's comment.
_WHAT_IS_WRONG = ("problem", "fault", "still_wrong", "references_never_show")


def _scores(purpose: str, output: Any) -> list[tuple[str, str, str | None]]:
    """The scores a judge's answer gives, each its name, its verdict and what is wrong."""
    scores = []
    for name, path in VERDICT_SCORES.get(purpose, {}).items():
        said = output
        for key in path:
            said = said.get(key) if isinstance(said, dict) else None
        if not isinstance(said, dict) or not isinstance(said.get("verdict"), str):
            continue
        wrong = next((said[key] for key in _WHAT_IS_WRONG if said.get(key)), None)
        scores.append((name, said["verdict"], wrong))
    return scores


def _send_to_trace(traced: tracing.Traced, call: ModelCall) -> None:
    """Send what a model call's record holds to its trace, and any verdicts in it as scores
    on it."""
    made = call.output.get("file") if isinstance(call.output, dict) else None
    measured = {
        "characters": call.characters,
        "audio_seconds": call.audio_seconds,
        "video_seconds": call.video_seconds,
    }
    traced.made(
        output=call.output,
        files=[made] if isinstance(made, str) else [],
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cost_usd=call.cost_usd,
        error=call.error,
        metadata={
            "provider": call.provider,
            "attempt": call.attempt,
            "outcome": call.outcome,
            "decision": call.decision,
            "reason": call.reason,
            "duration_ms": call.duration_ms,
            **{name: value for name, value in measured.items() if value is not None},
        },
    )
    for name, verdict, wrong in _scores(call.purpose, call.output):
        traced.score(name, verdict, comment=wrong)


def _answered_before(
    handoff: Handoff,
    *,
    purpose: str,
    session: Session | None = None,
    job: Job | None = None,
    images: list[dict[str, str]] | None = None,
    images_may_move: bool = False,
    since: datetime | None = None,
) -> dict[str, Any] | None:
    """What a call for `purpose` answered when handed exactly `handoff` and shown exactly
    `images`, or any images with `images_may_move`, if one was paid for. Every call is recorded
    as soon as it succeeds, so an answer a worker stopped before it could keep is handed back
    rather than paid for again."""
    calls = ModelCall.objects.filter(
        purpose=purpose,
        outcome=ModelCall.Outcome.SUCCEEDED,
        handoff=handoff.model_dump(mode="json"),
    )
    if not images_may_move:
        calls = calls.filter(images=images or [])
    if session is not None:
        calls = calls.filter(session=session)
    if since is not None:
        calls = calls.filter(created_at__gte=since)
    if job is not None:
        calls = calls.filter(job=job)
    answered = calls.last()
    if answered is None:
        return None
    tracing.answered_from_record(
        purpose,
        model=answered.model,
        told=_told_in_trace(answered.system_prompt),
        handoff=answered.handoff,
        shown=_files_given(handoff, images),
        output=answered.output,
    )
    return answered.output


def _picture_extension(data: bytes) -> str:
    """The file extension for a drawn picture, judged by what the bytes hold."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            file_format = picture.format or ""
    except (OSError, PIL.Image.DecompressionBombError) as error:
        raise UnreadableImage("The drawn picture can't be opened as a picture") from error
    if PIL.Image.MIME.get(file_format, "") not in IMAGE_TYPES:
        raise UnreadableImage(f"The drawn picture is {file_format or 'an unknown format'}")
    return file_format.lower()


def check_readable(image: Image) -> None:
    """Raise UnreadableImage if `image` can't be shown to a model."""
    _load(image)


def _load(image: Image) -> LoadedImage:
    """Read an image, shrunk to fit what the model looks at. The stored file is unchanged."""
    return shrunk_image(image, file_store.read(image.key))


def shrunk_image(image: Image, content: bytes) -> LoadedImage:
    """`content`, the bytes of `image`, shrunk to fit what the model looks at."""
    try:
        with PIL.Image.open(io.BytesIO(content)) as picture:
            # Judged by what the file holds, not by its name.
            file_format = picture.format or ""
            media_type = PIL.Image.MIME.get(file_format, "")
            if media_type not in IMAGE_TYPES:
                raise UnreadableImage(
                    f"{image.label} ({image.key}) is {media_type or 'an unknown format'}, "
                    "which models can't read"
                )
            picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            # The copy drops the note a phone leaves saying which way is up, so turn it first.
            upright = PIL.ImageOps.exif_transpose(picture)
            shrunk = io.BytesIO()
            upright.save(shrunk, format=file_format)
    except (OSError, PIL.Image.DecompressionBombError) as error:
        raise UnreadableImage(
            f"{image.label} ({image.key}) can't be opened as a picture"
        ) from error
    return LoadedImage(
        label=image.label, key=image.key, media_type=media_type, data=shrunk.getvalue()
    )


def _shown(images: Sequence[Image | LoadedImage]) -> list[dict[str, str]]:
    """Which images a call showed, by their keys: the bytes are already in the file store."""
    return [{"label": image.label, "key": image.key} for image in images]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
