import base64
import json
from typing import Any

import openai
from django.conf import settings
from openai.lib._pydantic import to_strict_json_schema
from openai.types.responses import (
    FunctionToolParam,
    ResponseInputImageParam,
    ResponseInputMessageContentListParam,
    ResponseInputParam,
)
from pydantic import BaseModel

from adforge.retry import OutsideServiceDown

from .types import (
    LoadedImage,
    ModelReply,
    ModelRequest,
    Picture,
    Said,
    ToolRequest,
    ToolSpec,
    Turn,
    TurnReply,
    TurnRequest,
    UnusableReply,
)

# Errors that may pass if we try again. Anything else (bad request, bad key) will not.
_WORTH_RETRYING = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
# Upright, the shape of the clips the portrait becomes.
_PORTRAIT_SIZE: Any = "720x1280"


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

    def take_turn(self, request: TurnRequest) -> TurnReply:
        try:
            raw = self._client.responses.with_raw_response.create(
                model=request.model,
                instructions=request.instructions,
                input=_conversation(request),
                tools=[_tool(spec) for spec in request.tools],
            )
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error)) from error
        # Read what we were billed before reading the answer, which can fail after billing.
        usage = json.loads(raw.text).get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)

        def unusable(why: str) -> UnusableReply:
            return UnusableReply(
                f"{request.model}'s turn for {request.purpose} can't be used: {why}",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        response = raw.parse()
        if response.status != "completed":
            details = response.incomplete_details
            raise unusable(f"it stopped before the end ({details.reason if details else '?'})")
        says = []
        calls = []
        for item in response.output:
            if item.type == "message":
                for part in item.content:
                    if part.type == "refusal":
                        raise unusable(f"it refused: {part.refusal}")
                    says.append(part.text)
            elif item.type == "function_call":
                try:
                    arguments = json.loads(item.arguments)
                except ValueError as error:
                    raise unusable(f"its arguments for {item.name} aren't JSON") from error
                if not isinstance(arguments, dict):
                    raise unusable(f"its arguments for {item.name} aren't a JSON object")
                calls.append(ToolRequest(call_id=item.call_id, tool=item.name, arguments=arguments))
        return TurnReply(
            turn=Turn(says="\n\n".join(says), calls=tuple(calls)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def draw(self, *, model: str, prompt: str) -> Picture:
        try:
            reply = self._client.images.generate(
                model=model, prompt=prompt, size=_PORTRAIT_SIZE, quality="high"
            )
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error)) from error
        if not reply.data or not reply.data[0].b64_json:
            raise ValueError(f"{model} sent back no picture")
        usage = reply.usage
        return Picture(
            data=base64.b64decode(reply.data[0].b64_json),
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )


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


def _conversation(request: TurnRequest) -> ResponseInputParam:
    """The agent's conversation as OpenAI takes it: each tool it called is the call, then
    what the tool handed back, paired by the call's id."""
    items: ResponseInputParam = []
    for each in request.handoff.conversation:
        if isinstance(each, Said):
            items.append(
                {"role": "user" if each.by == "user" else "assistant", "content": each.text}
            )
            continue
        items.append(
            {
                "type": "function_call",
                "call_id": each.call_id,
                "name": each.tool,
                "arguments": json.dumps(each.arguments),
            }
        )
        items.append(
            {"type": "function_call_output", "call_id": each.call_id, "output": each.result}
        )
    return items


def _tool(spec: ToolSpec) -> FunctionToolParam:
    # Strict, so the arguments the model sends always have the tool's shape.
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": to_strict_json_schema(spec.arguments),
        "strict": True,
    }
