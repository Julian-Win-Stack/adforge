import base64
import json

import openai
from django.conf import settings
from openai.types.responses import (
    ResponseInputImageParam,
    ResponseInputMessageContentListParam,
    ResponseInputParam,
)
from pydantic import BaseModel

from adforge.retry import OutsideServiceDown

from .types import LoadedImage, ModelReply, ModelRequest, UnusableReply

# Errors that may pass if we try again. Anything else (bad request, bad key) will not.
_WORTH_RETRYING = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = openai.OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
            max_retries=0,
            timeout=120,
        )

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        try:
            raw = self._client.responses.with_raw_response.parse(
                model=request.model,
                instructions=request.instructions,
                input=_input(request),
                text_format=request.output,
            )
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error)) from error
        # Read what we were billed before reading the answer, which can fail after billing.
        usage = json.loads(raw.text).get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        try:
            output = raw.parse().output_parsed
        except ValueError as error:  # Includes pydantic's ValidationError for cut-off JSON.
            raise UnusableReply(
                f"{request.model} gave an answer for {request.purpose} that could not be "
                f"read: {error}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ) from error
        if output is None:
            raise UnusableReply(
                f"{request.model} gave no usable answer for {request.purpose}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return ModelReply(output=output, input_tokens=input_tokens, output_tokens=output_tokens)


def _input[Out: BaseModel](request: ModelRequest[Out]) -> str | ResponseInputParam:
    handoff = request.handoff.model_dump_json()
    if not request.images:
        return handoff
    content: ResponseInputMessageContentListParam = [{"type": "input_text", "text": handoff}]
    for image in request.images:
        content += [{"type": "input_text", "text": image.label}, _image_part(image)]
    return [{"role": "user", "content": content}]


def _image_part(image: LoadedImage) -> ResponseInputImageParam:
    # "low" detail shows the model a 512 x 512 version: enough to judge colour, at a
    # fraction of the cost of full detail.
    data = base64.b64encode(image.data).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{image.media_type};base64,{data}",
        "detail": "low",
    }
