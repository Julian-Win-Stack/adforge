"""The one place every model call goes through.

It checks the handoff before anything is spent, retries when the provider is down, and
records every attempt: what was called, what it cost, how long it took, whether it worked,
and the one-sentence judgement."""

import io
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
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
    Handoff,
    Image,
    Judgement,
    LoadedImage,
    ModelProvider,
    ModelReply,
    ModelRequest,
    PictureProvider,
    UnusableReply,
    VoiceProvider,
)

if TYPE_CHECKING:
    from jobs.models import Job

    from .inworld_adapter import InworldProvider
    from .openai_adapter import OpenAIProvider

_override: object | None = None

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
    attempts = 0

    def attempt() -> Out:
        nonlocal attempts
        attempts += 1
        started = time.monotonic()
        try:
            reply = provider.complete(request)
        except Exception as error:
            _record_failure(job, request, provider, attempts, started, error)
            raise
        _record_success(job, request, provider, attempts, started, reply)
        return reply.output

    try:
        return with_retries(attempt)
    except OutsideServiceDown as error:
        raise OutsideServiceDown(
            f"The model provider was still down after {attempts} tries: {error}"
        ) from error


def draw_picture(*, job: Job | None, purpose: str, prompt: str) -> str:
    """Have a model draw a picture. Returns its key in the file store."""
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _pictures()

    def draw() -> tuple[str, dict[str, Any], _Bill]:
        picture = provider.draw(model=model, prompt=prompt)
        extension = _picture_extension(picture.data)
        key = file_store.save(f"{purpose}.{extension}", picture.data)
        return (
            key,
            {"file": key},
            _Bill(
                input_tokens=picture.input_tokens,
                output_tokens=picture.output_tokens,
                cost_usd=catalog.picture_cost_usd(
                    model, picture.input_tokens, picture.output_tokens
                ),
            ),
        )

    return _made(job, purpose, model, provider.name, {"prompt": prompt}, draw)


def design_voice(*, job: Job | None, purpose: str, description: str, sample: str) -> str:
    """Have a voice designed from a description, heard saying `sample`. Returns its id."""
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _voices()

    def design() -> tuple[str, dict[str, Any], _Bill]:
        voice_id = provider.design_voice(model=model, description=description, sample=sample)
        return voice_id, {"voice_id": voice_id}, _speech_bill(model, sample)

    handoff = {"description": description, "sample": sample}
    return _made(job, purpose, model, provider.name, handoff, design)


def speak(*, job: Job | None, purpose: str, voice_id: str, text: str) -> str:
    """Have the voice say `text`. Returns the WAV file's key in the file store."""
    model = catalog.MODEL_FOR_PURPOSE[purpose]
    provider = _voices()

    def say() -> tuple[str, dict[str, Any], _Bill]:
        audio = provider.speak(model=model, voice_id=voice_id, text=text)
        key = file_store.save(f"{purpose}.wav", audio)
        return key, {"file": key}, _speech_bill(model, text)

    handoff = {"voice_id": voice_id, "text": text}
    return _made(job, purpose, model, provider.name, handoff, say)


@dataclass(frozen=True)
class _Bill:
    """What one call was billed for."""

    cost_usd: Decimal
    input_tokens: int | None = None
    output_tokens: int | None = None
    characters: int | None = None


def _speech_bill(model: str, text: str) -> _Bill:
    return _Bill(characters=len(text), cost_usd=catalog.speech_cost_usd(model, len(text)))


def _made[Result](
    job: Job | None,
    purpose: str,
    model: str,
    provider: str,
    handoff: dict[str, Any],
    make: Callable[[], tuple[Result, dict[str, Any], _Bill]],
) -> Result:
    """Run `make` with retries, recording every attempt like `call_model` does."""
    attempts = 0

    def attempt() -> Result:
        nonlocal attempts
        attempts += 1
        started = time.monotonic()
        try:
            result, output, bill = make()
        except Exception as error:
            ModelCall.objects.create(
                job=job,
                purpose=purpose,
                provider=provider,
                model=model,
                attempt=attempts,
                handoff=handoff,
                outcome=ModelCall.Outcome.FAILED,
                error=f"{type(error).__name__}: {error}",
                duration_ms=_elapsed_ms(started),
            )
            raise
        ModelCall.objects.create(
            job=job,
            purpose=purpose,
            provider=provider,
            model=model,
            attempt=attempts,
            handoff=handoff,
            output=output,
            outcome=ModelCall.Outcome.SUCCEEDED,
            input_tokens=bill.input_tokens,
            output_tokens=bill.output_tokens,
            characters=bill.characters,
            cost_usd=bill.cost_usd,
            duration_ms=_elapsed_ms(started),
        )
        return result

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


def _record_success[Out: BaseModel](
    job: Job | None,
    request: ModelRequest[Out],
    provider: ModelProvider,
    attempt: int,
    started: float,
    reply: ModelReply[Out],
) -> None:
    judgement = reply.output if isinstance(reply.output, Judgement) else None
    ModelCall.objects.create(
        job=job,
        purpose=request.purpose,
        provider=provider.name,
        model=request.model,
        attempt=attempt,
        handoff=request.handoff.model_dump(mode="json"),
        images=_shown(request),
        output=reply.output.model_dump(mode="json"),
        outcome=ModelCall.Outcome.SUCCEEDED,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost_usd=catalog.cost_usd(request.model, reply.input_tokens, reply.output_tokens),
        duration_ms=_elapsed_ms(started),
        decision=judgement.decision if judgement else "",
        reason=judgement.reason if judgement else "",
    )


def _record_failure[Out: BaseModel](
    job: Job | None,
    request: ModelRequest[Out],
    provider: ModelProvider,
    attempt: int,
    started: float,
    error: Exception,
) -> None:
    # An unusable answer was still billed, so its cost is recorded like any other.
    billed = error if isinstance(error, UnusableReply) else None
    ModelCall.objects.create(
        job=job,
        purpose=request.purpose,
        provider=provider.name,
        model=request.model,
        attempt=attempt,
        handoff=request.handoff.model_dump(mode="json"),
        images=_shown(request),
        outcome=ModelCall.Outcome.FAILED,
        error=f"{type(error).__name__}: {error}",
        input_tokens=billed.input_tokens if billed else None,
        output_tokens=billed.output_tokens if billed else None,
        cost_usd=(
            catalog.cost_usd(request.model, billed.input_tokens, billed.output_tokens)
            if billed
            else None
        ),
        duration_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
