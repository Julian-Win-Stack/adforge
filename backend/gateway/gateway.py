"""The one place every model call goes through.

It checks the handoff before anything is spent, retries when the provider is down, and
records every attempt: what was called, what it cost, how long it took, whether it worked,
and the one-sentence judgement."""

import mimetypes
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import cache
from typing import TYPE_CHECKING

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
    UnusableReply,
)

if TYPE_CHECKING:
    from jobs.models import Job

_override: ModelProvider | None = None

# The picture formats models can read. Anything else is refused before money is spent.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
# The same formats, as a person would name them.
IMAGE_TYPE_NAMES = "PNG, JPEG, WebP or GIF"


class UnreadableImage(ValueError):
    """An image in a format models can't read."""


@cache
def _openai() -> ModelProvider:
    from .openai_adapter import OpenAIProvider

    return OpenAIProvider()


def _provider() -> ModelProvider:
    return _override or _openai()


@contextmanager
def use_model(provider: ModelProvider) -> Iterator[None]:
    """Send every model call to `provider` inside this block. Tests use it to swap in a fake."""
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


def _load(image: Image) -> LoadedImage:
    media_type, _ = mimetypes.guess_type(image.key)
    if media_type not in IMAGE_TYPES:
        raise UnreadableImage(
            f"{image.label} ({image.key}) is {media_type or 'not a known picture format'}, "
            "which models can't read"
        )
    return LoadedImage(
        label=image.label, key=image.key, media_type=media_type, data=file_store.read(image.key)
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
