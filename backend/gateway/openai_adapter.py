import openai
from django.conf import settings
from pydantic import BaseModel

from adforge.retry import OutsideServiceDown

from .types import ModelReply, ModelRequest

# Errors that may pass if we try again. Anything else (bad request, bad key) will not.
_WORTH_RETRYING = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        # The gateway does the retrying, so each attempt gets its own record.
        self._client = openai.OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=0, timeout=120)

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        try:
            response = self._client.responses.parse(
                model=request.model,
                instructions=request.instructions,
                input=request.handoff.model_dump_json(),
                text_format=request.output,
            )
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error)) from error
        if response.output_parsed is None:
            raise ValueError(f"{request.model} returned no usable output for {request.purpose}")
        usage = response.usage
        return ModelReply(
            output=response.output_parsed,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )
