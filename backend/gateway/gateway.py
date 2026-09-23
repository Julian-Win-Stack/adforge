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
from pydantic import BaseModel

from adforge import file_store
from adforge.retry import OutsideServiceDown, with_retries

from . import catalog
from .models import ModelCall
from .types import (
    AgentProvider,
    Handoff,
    Image,
    Judgement,
    LoadedImage,
    ModelProvider,
    ModelRequest,
    PictureProvider,
    PortraitHandoff,
    Said,
    SpeechHandoff,
    ToolSpec,
    ToolUse,
    Turn,
    TurnHandoff,
    TurnRequest,
    UnusableReply,
    VoiceDesignHandoff,
    VoiceProvider,
)

if TYPE_CHECKING:
    from agents.models import ToolCall
    from chat.models import Session
    from jobs.models import Job

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


def _provider() -> ModelProvider:
    return cast(ModelProvider, _override) if _override is not None else _openai()


def _pictures() -> PictureProvider:
    return cast(PictureProvider, _override) if _override is not None else _openai()


def _voices() -> VoiceProvider:
    return cast(VoiceProvider, _override) if _override is not None else _inworld()


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
    conversation: Sequence[Said | ToolUse],
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
    provider = _agents()

    def take() -> _Made[Turn]:
        reply = provider.take_turn(request)
        if not reply.turn.says and not reply.turn.calls:
            # A turn with neither is no reply at all: the user would wait for one forever.
            raise UnusableReply(
                "it said nothing and asked for no tool",
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            )
        return _Made(
            result=reply.turn,
            output={
                "says": reply.turn.says,
                "calls": [
                    {"call_id": call.call_id, "tool": call.tool, "arguments": call.arguments}
                    for call in reply.turn.calls
                ],
            },
            bill=_Bill(
                cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            ),
        )

    return _recorded(None, purpose, request.model, provider.name, handoff, take, session=session)


def call_model[Out: BaseModel](
    *,
    job: Job | None,
    purpose: str,
    instructions: str,
    handoff: Handoff,
    output: type[Out],
    images: Sequence[Image] = (),
) -> Out:
    """Ask a model for `output`. `images` are pictures shown alongside the handoff, read
    here from the file store so the record of which were shown can't disagree with what
    was sent."""
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
        picture = provider.draw(model=model, prompt=handoff.prompt)
        key = file_store.save(f"{purpose}.{_picture_extension(picture.data)}", picture.data)
        return _Made(
            result=key,
            output={"file": key},
            bill=_Bill(
                cost_usd=catalog.picture_cost_usd(
                    model, picture.input_tokens, picture.output_tokens
                ),
                input_tokens=picture.input_tokens,
                output_tokens=picture.output_tokens,
            ),
        )

    return _recorded(job, purpose, model, provider.name, handoff, draw)


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


@dataclass(frozen=True)
class _Bill:
    """What one call was billed for."""

    cost_usd: Decimal
    input_tokens: int | None = None
    output_tokens: int | None = None
    characters: int | None = None


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
