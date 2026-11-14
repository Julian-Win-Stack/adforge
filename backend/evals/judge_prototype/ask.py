"""One eval question asked of one item: what the judge is shown, the call, and what it cost.

Pictures and clip frames go to Azure through the gateway's OpenAI provider (its client, its
input building and its refusal handling), called here directly so the reply's cached-token
count can be read: `ask_unrecorded` keeps no cost. The presenter question goes to Gemini as
video with sound through the watcher's path (`gateway._watchable`, `VideoRequest`), at its own
frame rate."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from evals.judge_prototype.cases import Item
from evals.judge_prototype.questions import (
    CLIP_FPS,
    MOST_PAGE_PHOTOS,
    PRESENTER_FPS,
    Question,
    shared,
)
from gateway.types import Handoff

# Share of the input price Azure charges for cached input tokens. ASSUMED 10% (spec #644
# cost line: "a 90% cached-input discount, not yet checked against Azure's price page").
CACHED_SHARE = Decimal("0.1")


class Answer(BaseModel):
    """What the judge sees first, then its verdict."""

    sees: str = Field(
        description="What you see that bears on the question, in plain words: the problem, "
        "or, when there is none, what you checked and found right."
    )
    when: str = Field(
        description="Where the problem is: the second(s) of the clip or which picture. "
        "Empty when there is no problem."
    )
    on_what: Literal["product", "hand", "target", "presenter", "background", "nothing"] = Field(
        description="What the problem is on: the product, the hand using it, what it is used "
        "on (target), the presenter, or only the background. 'nothing' when there is no problem."
    )
    verdict: Literal["pass", "fail", "doesnt_apply"]


class PhotoSide(BaseModel):
    photo: str = Field(description="Which shop photo, e.g. 'photo 3'.")
    side: str = Field(
        description="Which side of the product it shows, e.g. 'front', 'back', 'top'."
    )
    parts: str = Field(description="Every part on that side and where it sits.")


class ClipSide(BaseModel):
    side: str = Field(description="A side of the product the clip or picture shows.")
    when: str = Field(description="The second(s) of the clip it shows, or 'the picture'.")
    parts: str = Field(description="Every part on that side and where it sits, as shown.")
    same_side_photo: str = Field(
        description="The shop photo that shows this same side, or 'none' when no photo shows it."
    )
    differences: str = Field(
        description="Part by part against that photo: each part added, missing or different. "
        "'none' when every part matches; 'no photo of this side' when there is none."
    )


class ProductForm(BaseModel):
    """The side-by-side form for the two "matches the shop photos" questions: the photos'
    sides and parts, the shown sides and parts, the differences, then the shared answer."""

    photo_sides: list[PhotoSide] = Field(
        description="Each side of the product the shop photos show, with its parts."
    )
    shown_sides: list[ClipSide] = Field(
        description="Each side of the product the clip or picture shows, compared with the "
        "shop photo of that same side."
    )
    sees: str = Field(description=Answer.model_fields["sees"].description)
    when: str = Field(description=Answer.model_fields["when"].description)
    on_what: Literal["product", "hand", "target", "presenter", "background", "nothing"] = Field(
        description=Answer.model_fields["on_what"].description
    )
    verdict: Literal["pass", "fail", "doesnt_apply"]


# Questions answered on a form of their own; the rest answer on `Answer`.
FORMS: dict[str, type[BaseModel]] = {"C5": ProductForm, "P1": ProductForm}


def answer_shape(question: Question) -> type[BaseModel]:
    return FORMS.get(question.id, Answer)


class JudgeHandoff(Handoff):
    """What the judge is handed: the scene's line and what it is shown before the pictures,
    the question after them, so every question of one item shares the same start."""

    AFTER_IMAGES: ClassVar[frozenset[str]] = frozenset({"question"})

    product_name: str
    product_description: str
    shown: str
    spoken_line: str
    question: str


@dataclass
class Asked:
    answer: Any
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    pictures: int = 0
    error: str = ""


@dataclass
class Shown:
    """The pictures one item's calls all get, in the same order, and how they're described."""

    images: list[Any] = field(default_factory=list)
    said: str = ""


def _photo(label: str, path: Path) -> Any:
    from gateway.gateway import shrunk_image
    from gateway.types import Image

    return shrunk_image(Image(label=label, key=str(path)), path.read_bytes())


def _joined(paths: list[Path]) -> bytes:
    """Photos side by side at one height, left to right, as one JPEG."""
    import io

    from PIL import Image as Pil

    pictures = [Pil.open(p).convert("RGB") for p in paths]
    height = min(800, max(p.height for p in pictures))
    pictures = [p.resize((max(1, round(p.width * height / p.height)), height)) for p in pictures]
    gap = 12
    sheet = Pil.new(
        "RGB", (sum(p.width for p in pictures) + gap * (len(pictures) - 1), height), "white"
    )
    x = 0
    for p in pictures:
        sheet.paste(p, (x, 0))
        x += p.width + gap
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=90)
    return out.getvalue()


def _page_photos(item: Item) -> list[Any]:
    """Every page photo: one to a picture up to MOST_PAGE_PHOTOS, past that joined side by
    side (two, or three past twice the cap) so they fit in MOST_PAGE_PHOTOS pictures."""
    from gateway.gateway import shrunk_image
    from gateway.types import Image

    paths = item.page_photos
    per = 1 if len(paths) <= MOST_PAGE_PHOTOS else -(-len(paths) // MOST_PAGE_PHOTOS)
    if per == 1:
        return [
            _photo(f"Real product photo {n}, from the shop's product page", path)
            for n, path in enumerate(paths, start=1)
        ]
    pictures = []
    for start in range(0, len(paths), per):
        group = paths[start : start + per]
        first, last = start + 1, start + len(group)
        label = (
            f"Real product photos {first} to {last}, side by side left to right, from the "
            "shop's product page"
            if len(group) > 1
            else f"Real product photo {first}, from the shop's product page"
        )
        data = _joined(group) if len(group) > 1 else group[0].read_bytes()
        pictures.append(shrunk_image(Image(label=label, key=f"photos-{first}-{last}"), data))
    return pictures


def _photo_count(item: Item) -> int:
    return len(item.page_photos)


def shown_for(item: Item) -> Shown:
    """What every Azure call on `item` is shown. Clips are cut into frames once per item."""
    from adforge import file_store

    if item.level == "picture":
        assert item.media is not None
        photos = _page_photos(item)
        return Shown(
            images=[*photos, _photo("The starting picture being judged", item.media)],
            said=f"{_photo_count(item)} real product photos from the shop's page, then the starting "
            "picture of one scene of a video ad (the first frame the video model animates).",
        )
    if item.level == "ad":
        photos = _page_photos(item)
        stills = [
            _photo(f"Still from scene {n} of the finished ad", path)
            for n, path in enumerate(item.ad_stills, start=1)
        ]
        return Shown(
            images=[*photos, *stills],
            said=f"{_photo_count(item)} real product photos from the shop's page, then one still "
            f"from each of the {len(stills)} scenes of the finished video ad, in order.",
        )
    assert item.media is not None
    key = file_store.save("judge-clip.mp4", item.media.read_bytes())
    from gateway.gateway import _load
    from gateway.types import Image
    from jobs.clip_judge import cut_pictures, frame_label

    photos = _page_photos(item)
    frames = cut_pictures(key, beside=len(photos), sets=("sides",))["sides"]
    images = [
        *photos,
        *(_load(Image(label=f"Clip {frame_label(f['times'])}", key=f["key"])) for f in frames),
    ]
    count = sum(len(f["times"]) for f in frames)
    joined = (
        " Some pictures hold several frames side by side, left to right."
        if count > len(frames)
        else ""
    )
    return Shown(
        images=images,
        said=f"{_photo_count(item)} real product photos from the shop's page, then every frame of one "
        f"clip of a video ad at {CLIP_FPS} frames a second ({count} frames), each labelled with "
        f"its time.{joined}"
        + (" The presenter talks on camera in this clip." if item.talking else ""),
    )


def _azure_cost(model: str, input_tokens: int, cached: int, output_tokens: int) -> Decimal:
    from gateway.catalog import PRICE_PER_MILLION_TOKENS

    price_in, price_out = PRICE_PER_MILLION_TOKENS[model]
    return (
        (input_tokens - cached) * price_in
        + cached * price_in * CACHED_SHARE
        + output_tokens * price_out
    ) / 1_000_000


def ask(question: Question, item: Item, shown: Shown) -> Asked:
    """Ask `question` of `item`, once. A refused or failed call comes back with `error` set
    and no answer: it is recorded so, never passed."""
    from gateway import catalog

    model = catalog.MODEL_FOR_PURPOSE[question.purpose]
    try:
        if question.purpose == "eval_judge_presenter":
            return _presenter(question, item, model)
        handoff = JudgeHandoff(
            product_name=item.product_name,
            product_description=item.product_text,
            shown=shown.said,
            spoken_line=item.line,
            question=question.prompt(),
        )
        return _complete(question, handoff, shown, model)
    except Exception as error:  # noqa: BLE001 - recorded and shown, never a pass
        tokens = (getattr(error, "input_tokens", 0) or 0, getattr(error, "output_tokens", 0) or 0)
        return Asked(
            answer=None,
            model=model,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cost_usd=_azure_cost(model, tokens[0], 0, tokens[1]),
            pictures=len(shown.images),
            error=f"{type(error).__name__}: {str(error)[:500]}",
        )


def _complete(question: Question, handoff: JudgeHandoff, shown: Shown, model: str) -> Asked:
    from adforge.retry import OutsideServiceDown, with_retries
    from gateway import catalog, openai_adapter
    from gateway.gateway import _provider
    from gateway.types import AnswerTookTooLong, ModelRequest, UnusableReply

    request = ModelRequest(
        purpose=question.purpose,
        model=model,
        instructions=shared(),
        handoff=handoff,
        output=answer_shape(question),
        images=tuple(shown.images),
    )
    provider = _provider()
    if not isinstance(provider, openai_adapter.OpenAIProvider):  # the fake
        reply = provider.complete(request)
        return Asked(
            answer=reply.output,
            model=model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_usd=_azure_cost(model, reply.input_tokens, 0, reply.output_tokens),
            pictures=len(shown.images),
        )

    def attempt() -> Asked:
        import openai

        try:
            raw = (
                provider._text_client(False)
                .with_options(
                    timeout=catalog.SLOW_ANSWER_SECONDS.get(
                        question.purpose, catalog.ANSWER_SECONDS
                    )
                )
                .responses.with_raw_response.parse(
                    model=model,
                    instructions=request.instructions,
                    input=openai_adapter._input(request),
                    text_format=answer_shape(question),
                )
            )
        except openai.APITimeoutError as error:
            raise AnswerTookTooLong(str(error)) from error
        except openai_adapter._WORTH_RETRYING as error:
            raise OutsideServiceDown(
                str(error), retry_after=openai_adapter._retry_after(error)
            ) from error
        except openai.BadRequestError as error:
            raise openai_adapter._blocked_or(error) from error
        said = json.loads(raw.text)
        usage = said.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        details = usage.get("input_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        written = int(details.get("cache_write_tokens") or 0)
        openai_adapter._raise_if_filtered(
            said, model, question.purpose, input_tokens, output_tokens
        )
        output = raw.parse().output_parsed
        if output is None:
            raise UnusableReply(
                f"{model} gave no usable answer",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return Asked(
            answer=output,
            model=model,
            input_tokens=input_tokens,
            cached_tokens=cached,
            cache_write_tokens=written,
            output_tokens=output_tokens,
            cost_usd=_azure_cost(model, input_tokens, cached, output_tokens),
            pictures=len(shown.images),
        )

    return with_retries(attempt)


PRESENTER_SHOWN = (
    f"One talking clip of a video ad, as video with its sound, looked at {PRESENTER_FPS:g} "
    "frames a second. The presenter talks on camera."
)


def _presenter(question: Question, item: Item, model: str) -> Asked:
    """A12: each talking clip of the ad watched on its own; the ad fails when any clip fails,
    doesn't apply when every clip doesn't, and passes otherwise."""
    from adforge import file_store

    answers: list[tuple[int, Answer]] = []
    total = Asked(answer=None, model=model)
    for n, clip in enumerate(item.talking_clips, start=1):
        line = item.talking_lines[n - 1] if n - 1 < len(item.talking_lines) else ""
        handoff = JudgeHandoff(
            product_name=item.product_name,
            product_description=item.product_text,
            shown=PRESENTER_SHOWN,
            spoken_line=line,
            question=question.prompt(),
        )
        key = file_store.save("judge-talking-clip.mp4", clip.read_bytes())
        one = _watch(question, handoff, key, model)
        total.input_tokens += one.input_tokens
        total.output_tokens += one.output_tokens
        total.cost_usd += one.cost_usd
        assert one.answer is not None
        answers.append((n, one.answer))
    verdicts = [a.verdict for _, a in answers]
    failed = [(n, a) for n, a in answers if a.verdict == "fail"]
    shown_answers = failed or answers
    total.answer = Answer(
        sees=" | ".join(f"talking clip {n}: {a.sees}" for n, a in shown_answers),
        when=" | ".join(f"talking clip {n}: {a.when}" for n, a in shown_answers if a.when),
        on_what=failed[0][1].on_what if failed else "nothing",
        verdict="fail"
        if failed
        else ("doesnt_apply" if set(verdicts) == {"doesnt_apply"} else "pass"),
    )
    return total


def _watch(question: Question, handoff: JudgeHandoff, video_key: str, model: str) -> Asked:
    from adforge import file_store
    from adforge.retry import with_retries
    from gateway.catalog import cost_usd
    from gateway.gateway import _watchable, _watchers
    from gateway.types import LoadedVideo, VideoRequest

    sent = _watchable(video_key)
    request = VideoRequest(
        purpose=question.purpose,
        model=model,
        instructions=shared(),
        handoff=handoff,
        output=Answer,
        video=LoadedVideo(key=sent, media_type="video/mp4", data=file_store.read(sent)),
        frames_per_second=PRESENTER_FPS,
    )
    reply = with_retries(lambda: _watchers().watch(request))
    return Asked(
        answer=reply.output,
        model=model,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost_usd=cost_usd(model, reply.input_tokens, reply.output_tokens),
    )
