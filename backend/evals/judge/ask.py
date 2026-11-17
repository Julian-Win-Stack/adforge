"""Ask one fixed eval question and checkpoint every answer before asking again."""

import hashlib
import io
import json
import math
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from PIL import Image as PilImage
from pydantic import BaseModel, Field

from evals.regression.questions import Question
from gateway.types import Handoff, Image, LoadedImage, ModelRequest

from . import prompts
from .inputs import IMAGE_SUFFIXES, JudgeInput
from .records import (
    FailedAsk,
    HandRow,
    JudgeAnswer,
    JudgeAsk,
    JudgeVerdict,
    NoteAnswer,
    NoteAsk,
    NoteVerdict,
    PhotoSide,
    ShownSide,
)

type Verdict = JudgeVerdict | NoteVerdict
type AnyAnswer = JudgeAnswer | NoteAnswer

MOST_PAGE_PHOTOS = 10
CACHED_SHARE = Decimal("0.1")
# Changing how inputs are shown is a new prompt version, just as changing the words is.
INPUT_POLICY = "v1: labelled page photos first, joined side by side; item next; question last"
CLIP_INPUT_POLICY = "v1: every frame at question fps; consecutive frames joined to 50 minus photos"
NOTE_INPUT_POLICY = (
    "v1: labelled page photos; note; first ad then new ad, every scene in order, every frame at "
    "question fps, consecutive frames of a scene joined to 50 minus photos; question last"
)


class Answer(BaseModel):
    sees: str = Field(description="What you see: the problem, or what you checked and found right.")
    when: str = Field(description="The seconds or picture where it happens; empty for no problem.")
    on_what: Literal["product", "hand", "target", "presenter", "background", "nothing"]
    verdict: JudgeVerdict


class ProductForm(BaseModel):
    photo_sides: list[PhotoSide]
    shown_sides: list[ShownSide]
    sees: str = Field(description=Answer.model_fields["sees"].description)
    when: str = Field(description=Answer.model_fields["when"].description)
    on_what: Literal["product", "hand", "target", "presenter", "background", "nothing"]
    verdict: JudgeVerdict


class HandForm(BaseModel):
    rows: list[HandRow]
    sees: str
    when: str
    verdict: JudgeVerdict


class NoteSceneForm(BaseModel):
    scene: int = Field(description="The scene number in the first ad.")
    first_ad: str = Field(description="What this scene of the first ad shows that the owner meant.")
    new_ad: str = Field(description="What the same scene of the new ad shows there now.")
    still_shows: bool = Field(description="Whether the new ad still shows the problem there.")


class NoteForm(BaseModel):
    unhappy_about: str = Field(description="Step 1: what the owner is unhappy about.")
    note_scenes: list[NoteSceneForm] = Field(
        description="Steps 1 and 2: every scene of the first ad that shows it, and whether the "
        "new ad still shows it."
    )
    sees: str = Field(
        pattern=r"\S",
        description="Step 3: what you see in the new ad that decides the answer, and where.",
    )
    verdict: NoteVerdict


FORMS: dict[str, type[BaseModel]] = {
    "P1": ProductForm,
    "C5": ProductForm,
    "C13": HandForm,
    "N1": NoteForm,
}


def answer_shape(question: Question) -> type[BaseModel]:
    return FORMS.get(question.id, Answer)


class JudgeHandoff(Handoff):
    AFTER_IMAGES: ClassVar[frozenset[str]] = frozenset({"question"})

    product_name: str
    product_description: str
    shown: str
    spoken_line: str
    scene_shows: str
    question: str


class NoteHandoff(Handoff):
    AFTER_IMAGES: ClassVar[frozenset[str]] = frozenset({"question"})

    product_name: str
    owner_note: str
    shown: str
    question: str


@dataclass(frozen=True)
class Shown:
    images: tuple[LoadedImage, ...]
    said: str


def purpose(question: Question) -> str:
    return f"eval_judge_{question.id}"


def judge_id(question: Question) -> str:
    """One id per prompt version, shared by every item so its score can accumulate.

    Inputs here means the construction policy and schema, not a particular item's
    bytes or words. Saved run inputs are immutable; a changed question is a new id.
    """
    from gateway import catalog

    note = question.stage == "note"
    policy: str | list[str] = INPUT_POLICY
    if question.stage == "clips":
        policy = [INPUT_POLICY, CLIP_INPUT_POLICY]
    elif note:
        policy = NOTE_INPUT_POLICY
    shaped = {
        "shared": prompts.note_instructions() if note else prompts.shared_instructions(),
        "question": prompts.question_text(question.id),
        "answer": answer_shape(question).model_json_schema(),
        "handoff": (NoteHandoff if note else JudgeHandoff).model_json_schema(),
        "model": catalog.MODEL_FOR_PURPOSE[purpose(question)],
        "inputs": [
            policy,
            MOST_PAGE_PHOTOS,
            question.frames_per_second,
            question.page_photos,
        ],
    }
    digest = hashlib.sha256(json.dumps(shaped, sort_keys=True).encode()).hexdigest()[:12]
    return f"{question.id}:{digest}"


def _image(label: str, path: Path, data: bytes | None = None) -> LoadedImage:
    from gateway.gateway import shrunk_image

    return shrunk_image(
        Image(label=label, key=str(path)), path.read_bytes() if data is None else data
    )


def page_photos(item: JudgeInput) -> list[LoadedImage]:
    """Keep all photos, in at most ten images as in the approved prototype."""
    per_image = max(1, -(-len(item.page_photos) // MOST_PAGE_PHOTOS))
    images = []
    for first in range(0, len(item.page_photos), per_image):
        paths = item.page_photos[first : first + per_image]
        labels = item.page_photo_labels[first : first + per_image]
        label = "Real product photos from the shop's page: " + "; ".join(
            f"Photo {first + n}: {words or 'no description saved'}"
            for n, words in enumerate(labels, 1)
        )
        if len(paths) == 1:
            images.append(_image(label, paths[0]))
            continue
        pieces = []
        for path in paths:
            with PilImage.open(path) as opened:
                pieces.append(opened.convert("RGB"))
        height = min(800, max(piece.height for piece in pieces))
        pieces = [
            piece.resize((max(1, round(piece.width * height / piece.height)), height))
            for piece in pieces
        ]
        gap = 12
        sheet = PilImage.new(
            "RGB", (sum(p.width for p in pieces) + gap * (len(pieces) - 1), height), "white"
        )
        x = 0
        for piece in pieces:
            sheet.paste(piece, (x, 0))
            x += piece.width + gap
        encoded = io.BytesIO()
        sheet.save(encoded, "JPEG", quality=90)
        images.append(_image(label + " (left to right)", paths[0], encoded.getvalue()))
    return images


def _still(path: Path) -> bytes:
    from django.conf import settings

    if path.suffix.lower() in IMAGE_SUFFIXES:
        return path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="eval-ad-still-") as temporary:
        target = Path(temporary) / "still.png"
        subprocess.run(
            [settings.FFMPEG, "-v", "error", "-i", str(path), "-frames:v", "1", str(target)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return target.read_bytes()


def frame_images(frames: list[tuple[Path, float]], together: int, label: str) -> list[LoadedImage]:
    """`frames` in order, `together` consecutive ones joined side by side per picture."""
    from jobs.clip_judge import frame_label, joined

    images = []
    for first in range(0, len(frames), together):
        group = frames[first : first + together]
        paths, times = zip(*group, strict=True)
        if len(group) == 1:
            data = paths[0].read_bytes()
        else:
            pieces = []
            for path in paths:
                with PilImage.open(path) as opened:
                    pieces.append(opened.convert("RGB"))
            data = joined(pieces)
        images.append(_image(f"{label} {frame_label(list(times))}", paths[0], data))
    return images


def _clip_images(
    item: JudgeInput, question: Question, photos: list[LoadedImage]
) -> tuple[list[LoadedImage], str]:
    from jobs.clip_judge import MOST_PICTURES, cut_frames

    if len(item.files) != 1 or question.frames_per_second is None:
        raise ValueError("A clip question needs one kept clip and a frame rate")
    room = MOST_PICTURES - len(photos)
    with tempfile.TemporaryDirectory(prefix="eval-clip-") as temporary:
        frames = cut_frames(item.files[0], Path(temporary) / "frames", question.frames_per_second)
        if not frames:
            raise ValueError("The kept clip has no readable frames")
        together = math.ceil(len(frames) / room)
        images = frame_images(frames, together, "Clip")
    description = (
        f"Every frame of the kept clip at {question.frames_per_second} frames a second "
        f"({len(frames)} frames), in time order."
    )
    if together > 1:
        description += " Consecutive frames are side by side, left to right."
    if photos:
        description = f"{len(item.page_photos)} shop photos, then " + description
    return [*photos, *images], description


def show(item: JudgeInput, question: Question) -> Shown:
    """Prepare an item's images once; all its questions with these inputs reuse them."""
    photos = page_photos(item) if question.page_photos else []
    if item.stage == "pictures":
        if len(item.files) != 1:
            raise ValueError("A starting-picture question needs one picture")
        images = [*photos, _image("The starting picture being judged", item.files[0])]
        said = f"{len(item.page_photos)} shop photos, then the scene's starting picture."
    elif item.stage == "clips":
        images, said = _clip_images(item, question, photos)
    elif item.stage == "ad":
        if not item.files:
            raise ValueError("A11 needs one saved clip or still from every scene")
        images = [
            *photos,
            *(
                _image(f"Still from scene {number} of the finished ad", path, _still(path))
                for number, path in enumerate(item.files, 1)
            ),
        ]
        said = f"{len(item.page_photos)} shop photos, then one still per scene, in scene order."
    else:
        raise ValueError(f"Judging {item.stage} is not implemented yet")
    return Shown(tuple(images), said)


def token_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    from gateway.catalog import PRICE_PER_MILLION_TOKENS

    price_in, price_out = PRICE_PER_MILLION_TOKENS[model]
    return float(
        (
            (input_tokens - cached_tokens) * price_in
            + cached_tokens * price_in * CACHED_SHARE
            + output_tokens * price_out
        )
        / 1_000_000
    )


# A negative verdict outvoted by its re-asks counts as the other answer.
OVERTURNED: dict[str, Verdict] = {"fail": "pass", "not_made": "made"}


def negative(answer: AnyAnswer) -> str:
    """The verdict that is asked twice more and counts only at 2 of 3."""
    return "not_made" if isinstance(answer, NoteAnswer) else "fail"


def complete(answer: AnyAnswer) -> bool:
    flaw = negative(answer)
    return (
        bool(answer.asks)
        and (answer.asks[0].verdict != flaw or len(answer.asks) >= 3)
        and answer.verdict is not None
        and not answer.error
    )


def start_answer(question: Question, previous: JudgeAnswer | None = None) -> JudgeAnswer:
    identifier = judge_id(question)
    if previous is not None and previous.judge == identifier:
        return previous
    answer = JudgeAnswer(judge=identifier, asks=[])
    if previous is not None:
        answer.history = [*previous.history, previous.model_copy(update={"history": []})]
    return answer


def start_note_answer(question: Question, previous: NoteAnswer | None = None) -> NoteAnswer:
    identifier = judge_id(question)
    if previous is not None and previous.judge == identifier:
        return previous
    answer = NoteAnswer(judge=identifier, asks=[])
    if previous is not None:
        answer.history = [*previous.history, previous.model_copy(update={"history": []})]
    return answer


def ask_question(
    question: Question,
    item: JudgeInput,
    *,
    previous: JudgeAnswer | None = None,
    shown: Shown | Callable[[], Shown] | None = None,
    save: Callable[[JudgeAnswer], None] = lambda answer: None,
) -> JudgeAnswer:
    """Ask once, re-asking only an initial fail, and durably save each ask immediately."""
    from gateway import catalog

    def request() -> ModelRequest[BaseModel]:
        nonlocal shown
        if shown is None:
            shown = show(item, question)
        elif callable(shown):
            shown = shown()
        return ModelRequest(
            purpose=purpose(question),
            model=catalog.MODEL_FOR_PURPOSE[purpose(question)],
            instructions=prompts.shared_instructions(),
            handoff=JudgeHandoff(
                product_name=item.product_name,
                product_description=item.product_text,
                shown=shown.said,
                spoken_line=item.line,
                scene_shows=item.shows,
                question=prompts.question_text(question.id),
            ),
            output=answer_shape(question),
            images=shown.images,
        )

    def recorded(raw: dict[str, Any]) -> dict[str, Any]:
        raw.setdefault("on_what", "hand" if question.id == "C13" else "nothing")
        return raw

    answer = start_answer(question, previous)
    ask_with_reasks(question, answer, request, recorded, save=save)
    return answer


def ask_with_reasks[A: (JudgeAnswer, NoteAnswer)](
    question: Question,
    answer: A,
    request: Callable[[], ModelRequest[BaseModel]],
    recorded: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    save: Callable[[A], None] = lambda answer: None,
) -> None:
    """Finish `answer` in place: ask once, re-ask only an initial negative verdict, and
    save each ask before the next.

    `request` is built only when a paid ask is needed, so a finished or recovered answer
    never reopens media. `recorded` turns the model's answer into a saved ask's fields.
    """
    from gateway.gateway import ask_measured

    if complete(answer):
        return
    answer.error = None
    answer.verdict = None
    flaw = negative(answer)
    ask_type = NoteAsk if isinstance(answer, NoteAnswer) else JudgeAsk
    asks: list[Any] = answer.asks
    # A crash can land after an ask is saved but before its counted verdict is.
    # Recover that decision from the saved asks without reopening media or paying.
    counted = _counted(asks, flaw)
    if counted is not None:
        _count(answer, counted)
        save(answer)
        return
    save(answer)
    try:
        asking = request()
        while not answer.asks or (answer.asks[0].verdict == flaw and len(answer.asks) < 3):
            reply = ask_measured(asking, azure_only=True)
            asks.append(
                ask_type(
                    **recorded(reply.output.model_dump()),
                    model=asking.model,
                    at=datetime.now(UTC).isoformat(),
                    input_tokens=reply.input_tokens,
                    cached_tokens=reply.cached_tokens,
                    cache_write_tokens=reply.cache_write_tokens,
                    output_tokens=reply.output_tokens,
                    cost_usd=token_cost(
                        asking.model, reply.input_tokens, reply.cached_tokens, reply.output_tokens
                    ),
                )
            )
            save(answer)
        _count(answer, _counted(asks, flaw))
    except Exception as error:
        failed_answer(question, answer, error)
    save(answer)


def _count(answer: AnyAnswer, verdict: Verdict | None) -> None:
    # Each answer type takes only its own verdicts, which counted_verdict keeps.
    answer.verdict = verdict


def counted_verdict(asks: Sequence[JudgeAsk]) -> JudgeVerdict | None:
    """A clip, picture or ad question's counted verdict: a fail counts at 2 of 3."""
    return cast(JudgeVerdict | None, _counted(asks, "fail"))


def _counted(asks: Sequence[JudgeAsk | NoteAsk], flaw: str) -> Verdict | None:
    if not asks:
        return None
    if asks[0].verdict != flaw:
        return asks[0].verdict
    if len(asks) < 3:
        return None
    return asks[0].verdict if sum(ask.verdict == flaw for ask in asks) >= 2 else OVERTURNED[flaw]


def failed_answer(question: Question, answer: AnyAnswer, error: Exception) -> None:
    """Keep a failure and any bill, and post its developer notice. Never a pass."""
    from adforge import monitoring
    from gateway import catalog
    from jobs import notices

    answer.error = f"{type(error).__name__}: {error}"
    answer.verdict = None
    model = catalog.MODEL_FOR_PURPOSE[purpose(question)]
    inputs = getattr(error, "input_tokens", 0) or 0
    outputs = getattr(error, "output_tokens", 0) or 0
    cached = getattr(error, "cached_tokens", 0) or 0
    answer.failed_asks.append(
        FailedAsk(
            error=answer.error,
            model=model,
            at=datetime.now(UTC).isoformat(),
            input_tokens=inputs,
            cached_tokens=cached,
            cache_write_tokens=getattr(error, "cache_write_tokens", 0) or 0,
            output_tokens=outputs,
            cost_usd=token_cost(model, inputs, cached, outputs),
        )
    )
    monitoring.failed(notices.EVAL_JUDGE_UNAVAILABLE.name, answer.error, question=question.id)
