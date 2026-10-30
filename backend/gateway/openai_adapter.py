import base64
import io
import json
from collections.abc import Callable, Sequence
from typing import Any

import openai
import PIL.Image
from django.conf import settings
from openai.lib._pydantic import to_strict_json_schema
from openai.types import ImagesResponse
from openai.types.responses import (
    FunctionToolParam,
    ResponseFunctionCallOutputItemListParam,
    ResponseInputImageContentParam,
    ResponseInputImageParam,
    ResponseInputMessageContentListParam,
    ResponseInputParam,
)
from pydantic import BaseModel

from adforge.retry import OutsideServiceDown, retry_after

from . import catalog
from .types import (
    AnswerBrokeItsRules,
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    LoadedImage,
    ModelReply,
    ModelRequest,
    NotSetUp,
    Picture,
    RefusedForMoney,
    Said,
    StepFinished,
    ToolRequest,
    ToolSpec,
    Turn,
    TurnReply,
    TurnRequest,
    UnusableReply,
    broke_its_rules,
)

# Errors that may pass if we try again. Anything else (bad request, bad key) will not.
_WORTH_RETRYING = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
# What OpenAI answers with once the account's spend limit or quota is reached. It comes as a
# rate limit (429), but trying again in a few seconds gets the same answer.
_OUT_OF_MONEY_CODES = {"insufficient_quota", "billing_hard_limit_reached"}
# What the safety filter answers with: Azure's, for a picture and for text, and OpenAI's own
# for a picture (docs/real-api-replies.md).
_BLOCKED_CODES = {"content_policy_violation", "content_filter", "moderation_blocked"}
# Why an answer stopped before the end when the safety filter stopped what the model wrote.
_FILTERED = "content_filter"
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
            timeout=catalog.ANSWER_SECONDS,
        )
        # Pictures are drawn on OpenAI's own servers, whichever answers the other calls.
        self._pictures_client = (
            openai.OpenAI(
                api_key=settings.OPENAI_PICTURES_KEY,
                base_url=settings.OPENAI_PICTURES_BASE_URL or None,
                max_retries=0,
                timeout=catalog.ANSWER_SECONDS,
            )
            if settings.OPENAI_PICTURES_KEY
            else None
        )
        # Our Azure copy of the picture model, which draws once OpenAI refuses for money.
        self._azure_pictures_client = (
            openai.OpenAI(
                api_key=settings.AZURE_PICTURES_KEY,
                base_url=settings.AZURE_PICTURES_BASE_URL,
                max_retries=0,
                timeout=catalog.ANSWER_SECONDS,
            )
            if settings.AZURE_PICTURES_KEY
            else None
        )

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        try:
            raw = self._client.with_options(
                timeout=catalog.SLOW_ANSWER_SECONDS.get(request.purpose, catalog.ANSWER_SECONDS)
            ).responses.with_raw_response.parse(
                model=request.model,
                instructions=request.instructions,
                input=_input(request),
                text_format=request.output,
            )
        except openai.APITimeoutError as error:
            if request.purpose in catalog.SLOW_ANSWER_SECONDS:
                raise AnswerTookTooLong(str(error)) from error
            raise OutsideServiceDown(str(error)) from error
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error), retry_after=_retry_after(error)) from error
        except openai.BadRequestError as error:
            raise _blocked_or(error) from error
        # Read what we were billed before reading the answer, which can fail after billing.
        answer = json.loads(raw.text)
        usage = answer.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        _raise_if_filtered(answer, request.model, request.purpose, input_tokens, output_tokens)
        try:
            output = raw.parse().output_parsed
        except ValueError as error:  # Includes pydantic's ValidationError for cut-off JSON.
            # Whole JSON that broke a rule of the output may be asked again; cut-off JSON isn't.
            unusable = AnswerBrokeItsRules if broke_its_rules(error) else UnusableReply
            raise unusable(
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
                **_reasoning(),
            )
        except _WORTH_RETRYING as error:
            raise OutsideServiceDown(str(error), retry_after=_retry_after(error)) from error
        except openai.BadRequestError as error:
            raise _blocked_or(error) from error
        # Read what we were billed before reading the answer, which can fail after billing.
        answer = json.loads(raw.text)
        usage = answer.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        _raise_if_filtered(answer, request.model, request.purpose, input_tokens, output_tokens)

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
        reasoning = []
        for item in response.output:
            if item.type == "reasoning":
                reasoning += [part.text for part in item.summary]
            elif item.type == "message":
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
            reasoning="\n\n".join(reasoning),
        )

    def draw(self, *, model: str, prompt: str, on_azure: bool = False) -> Picture:
        client, asked_for = self._picture_client(model, on_azure)
        return _picture(
            model,
            _asked(
                lambda: client.images.generate(
                    model=asked_for, prompt=prompt, size=_PORTRAIT_SIZE, quality="high"
                )
            ),
        )

    def edit(
        self,
        *,
        model: str,
        prompt: str,
        pictures: Sequence[bytes],
        size: str,
        on_azure: bool = False,
    ) -> Picture:
        client, asked_for = self._picture_client(model, on_azure)
        image = [
            (f"picture-{number}", data, _picture_type(data))
            for number, data in enumerate(map(_editable, pictures), 1)
        ]
        return _picture(
            model,
            _asked(
                lambda: client.images.edit(
                    model=asked_for,
                    image=image,
                    prompt=prompt,
                    size=size,
                    quality="high",
                )
            ),
        )

    def _picture_client(self, model: str, on_azure: bool) -> tuple[openai.OpenAI, str]:
        """The client to draw with, and the name `model` goes by there: on Azure, its
        deployment's."""
        if on_azure:
            if self._azure_pictures_client is None:
                raise NotSetUp(
                    "Pictures are drawn on Azure once OpenAI refuses for money, and "
                    "AZURE_OPENAI_KEY or AZURE_OPENAI_BASE_URL is not set"
                )
            return self._azure_pictures_client, settings.AZURE_PICTURES_DEPLOYMENT or model
        if self._pictures_client is None:
            raise NotSetUp("Pictures are drawn on OpenAI's own API, and OPENAI_API_KEY is not set")
        return self._pictures_client, model


def _asked(ask: Callable[[], ImagesResponse]) -> ImagesResponse:
    """A picture model's reply to `ask`, its errors raised as the gateway tells them apart:
    out of money, worth trying again, blocked by the safety filter, or the request's own."""
    try:
        return ask()
    except openai.APIStatusError as error:
        if _out_of_money(error):
            raise RefusedForMoney(str(error)) from error
        if isinstance(error, _WORTH_RETRYING):
            raise OutsideServiceDown(str(error), retry_after=_retry_after(error)) from error
        if isinstance(error, openai.BadRequestError):
            raise _blocked_or(error) from error
        raise
    except _WORTH_RETRYING as error:
        raise OutsideServiceDown(str(error)) from error


def _retry_after(error: openai.APIError) -> float | None:
    """How many seconds the service said to wait before asking again, if it said: Azure
    paces its picture deployment this way. An answer never had says nothing."""
    if isinstance(error, openai.APIStatusError):
        return retry_after(error.response.headers)
    return None


def _out_of_money(error: openai.APIStatusError) -> bool:
    """Whether OpenAI refused because the account's spend limit or quota is reached."""
    return error.code in _OUT_OF_MONEY_CODES or error.type in _OUT_OF_MONEY_CODES


def _editable(data: bytes) -> bytes:
    """A picture as the picture model takes it: a GIF, which it refuses, as a PNG of its
    first frame; any other picture as it is."""
    with PIL.Image.open(io.BytesIO(data)) as picture:
        if picture.format != "GIF":
            return data
        png = io.BytesIO()
        picture.convert("RGBA").save(png, format="PNG")
        return png.getvalue()


def _picture_type(data: bytes) -> str:
    """The media type of a picture, judged by what the bytes hold. OpenAI refuses a picture
    sent without one."""
    with PIL.Image.open(io.BytesIO(data)) as picture:
        return PIL.Image.MIME.get(picture.format or "", "application/octet-stream")


def _picture(model: str, reply: ImagesResponse) -> Picture:
    if not reply.data or not reply.data[0].b64_json:
        raise ValueError(f"{model} sent back no picture")
    usage = reply.usage
    return Picture(
        data=base64.b64decode(reply.data[0].b64_json),
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        picture_input_tokens=usage.input_tokens_details.image_tokens if usage else 0,
    )


def _input[Out: BaseModel](request: ModelRequest[Out]) -> str | ResponseInputParam:
    if not request.images:
        return request.handoff.model_dump_json()
    before, after = request.handoff.around_images()
    content: ResponseInputMessageContentListParam = [{"type": "input_text", "text": before}]
    for image in request.images:
        content += [{"type": "input_text", "text": image.label}, _image_part(image)]
    if after is not None:
        content.append({"type": "input_text", "text": after})
    return [{"role": "user", "content": content}]


def _image_part(image: LoadedImage) -> ResponseInputImageParam:
    # "high" detail shows the model the picture as sent (the gateway has already shrunk it to
    # fit MAX_IMAGE_SIDE), so small print and look-alike products can be told apart. It costs
    # more than "low" per picture; decided for every call alike in #1 step 1.
    return {"type": "input_image", "image_url": _data_url(image), "detail": "high"}


def _output_image(image: LoadedImage) -> ResponseInputImageContentParam:
    # As sharp as `_image_part`'s, for the same reasons.
    return {"type": "input_image", "image_url": _data_url(image), "detail": "high"}


def _data_url(image: LoadedImage) -> str:
    return f"data:{image.media_type};base64,{base64.b64encode(image.data).decode('ascii')}"


def _conversation(request: TurnRequest) -> ResponseInputParam:
    """The agent's conversation as OpenAI takes it: each tool it called is the call, then
    what the tool handed back, paired by the call's id: its words, then each picture after
    its label. What the system tells it, such as that background work finished, comes from
    the developer, above the user, and so does the snapshot it is given last."""
    images = iter(request.images)
    items: ResponseInputParam = []
    for each in request.handoff.conversation:
        if isinstance(each, Said):
            items.append(
                {"role": "user" if each.by == "user" else "assistant", "content": each.text}
            )
            continue
        if isinstance(each, StepFinished):
            items.append({"role": "developer", "content": each.text})
            continue
        items.append(
            {
                "type": "function_call",
                "call_id": each.call_id,
                "name": each.tool,
                "arguments": json.dumps(each.arguments),
            }
        )
        output: str | ResponseFunctionCallOutputItemListParam = each.result
        if each.pictures:
            output = [{"type": "input_text", "text": each.result}]
            for picture in each.pictures:
                image = next(images)
                assert (image.label, image.key) == (picture.label, picture.key)
                output += [{"type": "input_text", "text": image.label}, _output_image(image)]
        items.append({"type": "function_call_output", "call_id": each.call_id, "output": output})
    # The snapshot of what the agent works on comes last, after everything that happened.
    if request.handoff.snapshot:
        items.append({"role": "developer", "content": request.handoff.snapshot})
    return items


def _reasoning() -> dict[str, Any]:
    """The summary of its reasoning an agent's model is asked for, as settings say."""
    summary = settings.OPENAI_REASONING_SUMMARY
    return {"reasoning": {"summary": summary}} if summary else {}


def _tool(spec: ToolSpec) -> FunctionToolParam:
    # Strict, so the arguments the model sends always have the tool's shape.
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": to_strict_json_schema(spec.arguments),
        "strict": True,
    }


def _blocked_or(error: openai.BadRequestError) -> Exception:
    """The safety filter's refusal as such, or the request's own error. OpenAI's picture
    filter says which it refused: what it was sent, or the picture once drawn ("output")."""
    if error.code not in _BLOCKED_CODES:
        return error
    details = error.body.get("moderation_details") if isinstance(error.body, dict) else None
    stage = details.get("moderation_stage") if isinstance(details, dict) else None
    return BlockedBySafetyFilter(str(error), of_what_was_made=stage == "output")


def _raise_if_filtered(
    answer: dict[str, Any], model: str, purpose: str, input_tokens: int, output_tokens: int
) -> None:
    """Raise the safety filter's refusal of what the model wrote, billed as it was, when the
    filter stopped the `answer` before the end."""
    details = answer.get("incomplete_details") or {}
    if answer.get("status") == "incomplete" and details.get("reason") == _FILTERED:
        raise BlockedBySafetyFilter(
            f"the safety filter stopped {model}'s answer for {purpose} before the end "
            f"({_FILTERED})",
            of_what_was_made=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
