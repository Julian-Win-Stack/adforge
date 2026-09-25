"""The one place every model call goes through.

It checks the handoff before anything is spent, retries when the provider is down, and
records every attempt: what was called, what it cost, how long it took, whether it worked,
and the one-sentence judgement."""

import io
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import TYPE_CHECKING, Any, cast

import PIL.Image
import PIL.ImageOps
from django.conf import settings
from pydantic import BaseModel

from adforge import file_store
from adforge.retry import OutsideServiceDown, with_retries

from . import catalog
from .models import ModelCall
from .types import (
    AgentProvider,
    ClipCollectHandoff,
    ClipFailed,
    ClipHandoff,
    ClipProvider,
    ClipTimedOut,
    Handoff,
    Happened,
    Image,
    Judgement,
    LoadedImage,
    ModelProvider,
    ModelRequest,
    Picture,
    PictureEditHandoff,
    PictureProvider,
    PortraitHandoff,
    SpeechHandoff,
    ToolRequest,
    ToolSpec,
    Transcription,
    TranscriptionHandoff,
    TranscriptionProvider,
    Turn,
    TurnHandoff,
    TurnRequest,
    UnusableReply,
    VoiceDesignHandoff,
    VoiceProvider,
    Word,
)

if TYPE_CHECKING:
    from agents.models import ToolCall
    from chat.models import Session
    from jobs.models import Job

    from .elevenlabs_adapter import ElevenLabsProvider
    from .heygen_adapter import HeyGenProvider
    from .inworld_adapter import InworldProvider
    from .openai_adapter import OpenAIProvider

_override: object | None = None

# The tool call whose work is running now. Every model call made meanwhile is recorded
# against it, so a tool call's cost is the sum of its model calls.
_running_tool: ContextVar[ToolCall | None] = ContextVar("running_tool", default=None)

# The picture formats models can read. Anything else is refused before money is spent.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
# The same formats, as a person would name them.
IMAGE_TYPE_NAMES = "PNG, JPEG, WebP or GIF"
# The most of an image a model looks at in the "low" detail the adapter asks for. A bigger
# image is shrunk to fit first: the model sees the same picture, and the request stays small.
MAX_IMAGE_SIDE = 512


class UnreadableImage(ValueError):
    """An image models can't read: in another format, or not a picture at all."""


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
def _heygen() -> HeyGenProvider:
    from .heygen_adapter import HeyGenProvider

    return HeyGenProvider()


def _provider() -> ModelProvider:
    return cast(ModelProvider, _override) if _override is not None else _openai()


def _pictures() -> PictureProvider:
    return cast(PictureProvider, _override) if _override is not None else _openai()


def _voices() -> VoiceProvider:
    return cast(VoiceProvider, _override) if _override is not None else _inworld()


def _transcribers() -> TranscriptionProvider:
    return cast(TranscriptionProvider, _override) if _override is not None else _elevenlabs()


def _clips() -> ClipProvider:
    return cast(ClipProvider, _override) if _override is not None else _heygen()


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
) -> Turn:
    """Give an agent its conversation and the tools it may call, and have it take one turn:
    say something, ask for tools, or both."""
    handoff = TurnHandoff(conversation=list(conversation), tools=[tool.name for tool in tools])
    # Validate again here rather than trusting the caller built the handoff properly.
    handoff = TurnHandoff.model_validate(handoff.model_dump())
    request = TurnRequest(
        purpose=purpose,
        model=catalog.MODEL_FOR_PURPOSE[purpose],
        instructions=instructions,
        handoff=handoff,
        tools=tuple(tools),
    )
    # A turn paid for before the worker stopped, but not yet kept, is taken again for free.
    answered = _answered_before(handoff, purpose=purpose, session=session)
    if answered is not None:
        return _turn_from(answered)
    provider = _agents()

    def take() -> _Made[Turn]:
        reply = provider.take_turn(request)
        return _Made(
            result=reply.turn,
            output=_turn_output(reply.turn),
            bill=_Bill(
                cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            ),
        )

    return _recorded(None, purpose, request.model, provider.name, handoff, take, session=session)


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
) -> Out:
    """Ask a model for `output`. `images` are pictures shown alongside the handoff, read
    here from the file store so the record of which were shown can't disagree with what
    was sent. With `pay_once`, what the job's model already answered for this same handoff
    and images is handed back, and nothing is paid."""
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
        answered = _answered_before(handoff, purpose=purpose, job=job, images=_shown(request))
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

    return _recorded(job, purpose, request.model, provider.name, handoff, complete, _shown(request))


def draw_picture(*, job: Job | None, purpose: str, prompt: str) -> str:
    """Have a model draw a picture. Returns its key in the file store."""
    handoff = PortraitHandoff(prompt=prompt)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _pictures()

    def draw() -> _Made[str]:
        return _kept(purpose, model, provider.draw(model=model, prompt=handoff.prompt))

    return _recorded(job, purpose, model, provider.name, handoff, draw)


def edit_picture(*, job: Job | None, purpose: str, prompt: str, pictures: Sequence[str]) -> str:
    """Have a model make a picture from `pictures`, given by their keys in the file store,
    as `prompt` says. Each is sent whole, not shrunk: they are what the new picture is made
    of. Returns the new picture's key in the file store."""
    handoff = PictureEditHandoff(prompt=prompt, pictures=list(pictures))
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _pictures()

    def edit() -> _Made[str]:
        given = [file_store.read(key) for key in handoff.pictures]
        return _kept(
            purpose, model, provider.edit(model=model, prompt=handoff.prompt, pictures=given)
        )

    return _recorded(job, purpose, model, provider.name, handoff, edit)


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
    audio_key: str,
    audio_seconds: float,
    motion_prompt: str,
) -> str:
    """Ask for a clip of the picture speaking the audio, both given by their keys in the file
    store. This is what is paid for: a clip as long as the audio. Returns the clip's id, to
    collect it with once it's made."""
    handoff = ClipHandoff(picture=picture_key, audio=audio_key, motion_prompt=motion_prompt)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _clips()

    def submit() -> _Made[str]:
        video_id = provider.submit(
            picture=file_store.read(handoff.picture),
            audio=file_store.read(handoff.audio),
            motion_prompt=handoff.motion_prompt,
        )
        return _Made(
            result=video_id,
            output={"video_id": video_id},
            bill=_Bill(
                video_seconds=audio_seconds,
                cost_usd=catalog.video_cost_usd(model, audio_seconds),
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, submit)


def collect_clip(*, job: Job | None, purpose: str, video_id: str) -> str:
    """Wait for the clip asked for as `video_id` to be made, then keep it. Costs nothing: the
    clip was paid for when it was asked for. Raises ClipFailed if it can't be made, and
    ClipTimedOut if it isn't made within CLIP_MAX_WAIT_SECONDS. Neither is asked again,
    which would pay for the clip twice. Returns the clip's key in the file store."""
    handoff = ClipCollectHandoff(video_id=video_id)
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _clips()

    def collect() -> _Made[str]:
        waited_since = time.monotonic()
        while (made := provider.status(video_id=handoff.video_id)).state == "working":
            if time.monotonic() - waited_since >= settings.CLIP_MAX_WAIT_SECONDS:
                raise ClipTimedOut(
                    f"the clip wasn't made within {settings.CLIP_MAX_WAIT_SECONDS:g} seconds"
                )
            time.sleep(settings.CLIP_POLL_SECONDS)
        if made.state == "failed" or made.video_url is None:
            raise ClipFailed(made.error or "the video service gave no reason")
        # Fetched and kept at once: the link to it only works for a while.
        key = file_store.save("clip.mp4", provider.download(url=made.video_url))
        return _Made(result=key, output={"file": key}, bill=_Bill(cost_usd=Decimal(0)))

    return _recorded(job, purpose, model, provider.name, handoff, collect)


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


def _recorded[Result](
    job: Job | None,
    purpose: str,
    model: str,
    provider: str,
    handoff: Handoff,
    make: Callable[[], _Made[Result]],
    images: list[dict[str, str]] | None = None,
    session: Session | None = None,
) -> Result:
    """Run `make` with retries, recording every attempt: what was called, what it cost, how
    long it took, whether it worked, and any judgement. Each is recorded against the session
    it was for: the one given, or else the tool call's or the job's."""
    tool_call = _running_tool.get()
    if session is None and tool_call is not None:
        session = tool_call.session
    recorded: dict[str, Any] = {
        "session_id": session.pk if session else job.session_id if job else None,
        "job": job,
        "tool_call": tool_call,
        "purpose": purpose,
        "provider": provider,
        "model": model,
        "handoff": handoff.model_dump(mode="json"),
        "images": images or [],
    }
    attempts = 0

    def attempt() -> Result:
        nonlocal attempts
        attempts += 1
        started = time.monotonic()
        try:
            made = make()
        except Exception as error:
            # An unusable answer was still billed, so its cost is recorded like any other.
            billed = error if isinstance(error, UnusableReply) else None
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
            )
            raise
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
        )
        return made.result

    try:
        return with_retries(attempt)
    except OutsideServiceDown as error:
        raise OutsideServiceDown(
            f"The model provider was still down after {attempts} tries: {error}"
        ) from error


def _answered_before(
    handoff: Handoff,
    *,
    purpose: str,
    session: Session | None = None,
    job: Job | None = None,
    images: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """What a call for `purpose` answered when handed exactly `handoff` and shown exactly
    `images`, if one was paid for. Every call is recorded as soon as it succeeds, so an
    answer a worker stopped before it could keep is handed back rather than paid for again."""
    calls = ModelCall.objects.filter(
        purpose=purpose,
        outcome=ModelCall.Outcome.SUCCEEDED,
        handoff=handoff.model_dump(mode="json"),
        images=images or [],
    )
    if session is not None:
        calls = calls.filter(session=session)
    if job is not None:
        calls = calls.filter(job=job)
    answered = calls.last()
    return answered.output if answered else None


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


def _load(image: Image) -> LoadedImage:
    """Read an image, shrunk to fit what the model looks at. The stored file is unchanged."""
    content = file_store.read(image.key)
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


def _shown[Out: BaseModel](request: ModelRequest[Out]) -> list[dict[str, str]]:
    """Which images the call showed, by their keys: the bytes are already in the file store."""
    return [{"label": image.label, "key": image.key} for image in request.images]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
