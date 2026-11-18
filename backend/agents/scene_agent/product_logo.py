"""Describe the logo the scene can see and crop it from the photo sent to the image model."""

import io
import math
from collections.abc import Sequence
from hashlib import sha256
from typing import Literal

import PIL.Image
import PIL.ImageOps
from pydantic import BaseModel, Field

from adforge import file_store
from agents.models import ToolCall
from agents.scene_forms import PRODUCT_DESIGN_LINE
from chat.models import Message
from gateway.gateway import call_model
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Handoff, Image
from jobs import notices
from jobs.models import Scene

from .page_views import MOST_PICTURES
from .photos import SelectedPhoto

PURPOSE = "describe_product_logo"
INSTRUCTIONS = (
    "You help write a picture prompt for one scene of a video ad. You get the scene's picture "
    "prompt and the product photos it names. Describe the product's printed logo and brand text "
    "exactly, so an image model draws it in the right place, size and colour, and find it in the "
    "photos. If the side the camera sees has no printed logo or brand text, set shown to false "
    "and return an empty description, image 0 and an empty box."
)
LOGO_SENTENCE = (
    "The product's printed logo and text, as in its photos: {description} "
    "Draw them there on the product, in that colour and at that size."
)
CROP_LINE = (
    "Image {number}: a close-up of that logo and text from the product's page photo; "
    "copy it onto the product."
)
MIN_CROP_SIDE = 1024


class Logo(BaseModel):
    shown: bool = Field(
        description="Whether the product's side that faces the camera in this scene carries "
        "a printed logo or brand text."
    )
    description: str = Field(
        description="One or two sentences for the image model: the product's printed logo "
        "and brand text on the side that faces the camera in this scene: what it is (emblem, "
        "wordmark), where exactly it sits on the product, its size compared with the product, "
        "its colour and finish, and the letters it spells."
    )
    image: int = Field(
        description="The number of the product photo that shows that logo most clearly."
    )
    box: list[int] = Field(
        description="That logo's box in that photo as [left, top, right, bottom], each 0-1000 "
        "of the photo's width or height, tight around the logo and its text."
    )


class LogoHandoff(Handoff):
    prompt: str


LogoStatus = Literal[
    "none",
    "found",
    "call_failed",
    "refused",
    "bad_description",
    "bad_photo",
    "bad_box",
    "picture_limit",
    "crop_failed",
]


class LogoSent(BaseModel):
    status: LogoStatus
    sentence: str = ""
    image: int | None = None
    source_file: str | None = None
    crop_file: str | None = None

    def add_to(self, prompt: str, pictures: list[str]) -> str:
        """Put the logo's words just before the final product-design line and its crop last."""
        if not self.sentence:
            return prompt
        lines = [self.sentence]
        if self.crop_file is not None:
            pictures.append(self.crop_file)
            lines.append(CROP_LINE.format(number=len(pictures)))
        before, ending, after = prompt.rpartition(PRODUCT_DESIGN_LINE)
        assert ending
        return before + " ".join([*lines, ending]) + after


def describe(
    call: ToolCall,
    scene: Scene,
    prompt: str,
    photos: Sequence[SelectedPhoto],
    *,
    first: int,
    picture_count: int,
) -> LogoSent:
    """Ask about the visible side using only the product references in this drawing request."""
    images = {number: photo for number, photo in enumerate(photos, first)}
    # A refusal or unusable answer can be billed too. The same tool falls back again.
    failed = call.model_calls.filter(purpose=PURPOSE, outcome=ModelCall.Outcome.FAILED).last()
    if failed is not None:
        return _vision_failed(scene, failed.error)
    try:
        answer = call_model(
            job=scene.job,
            purpose=PURPOSE,
            instructions=INSTRUCTIONS,
            handoff=LogoHandoff(prompt=prompt),
            output=Logo,
            images=[Image(label=photo.line, key=photo.file) for photo in photos],
            pay_once="tool",
            single_attempt=True,
        )
    except Exception as error:
        return _vision_failed(scene, f"{type(error).__name__}: {error}")

    if not answer.shown:
        return LogoSent(status="none")
    description = answer.description.strip()
    if not description:
        return _fallback(
            scene, LogoSent(status="bad_description"), "the logo description was empty"
        )
    sentence = LOGO_SENTENCE.format(description=description)
    photo = images.get(answer.image)
    result = LogoSent(
        status="found",
        sentence=sentence,
        image=answer.image,
        source_file=photo.file if photo else None,
    )
    if photo is None:
        result.status = "bad_photo"
        return _fallback(scene, result, f"Image {answer.image} was not a product photo sent")
    if picture_count >= MOST_PICTURES:
        result.status = "picture_limit"
        return _fallback(scene, result, f"the image model already had {MOST_PICTURES} pictures")
    try:
        # The source and box identify these pixels even if the tool is resumed before drawing.
        crop_id = sha256(f"{photo.file}:{answer.box}".encode()).hexdigest()
        result.crop_file = file_store.save_once(
            f"jobs/{scene.job_id}/logo-crops/{crop_id}.png",
            lambda: crop_logo(file_store.read(photo.file), answer.box),
            whole=file_store.whole_picture,
        )
    except BadLogoBox:
        result.status = "bad_box"
        return _fallback(
            scene, result, f"Image {answer.image} had an unusable logo box {answer.box}"
        )
    except Exception as error:
        result.status = "crop_failed"
        return _fallback(scene, result, f"the logo crop from Image {answer.image} failed ({error})")
    return result


def _vision_failed(scene: Scene, error: str) -> LogoSent:
    if error.startswith(f"{BlockedBySafetyFilter.__name__}:"):
        return _fallback(scene, LogoSent(status="refused"), "the vision safety filter refused it")
    return _fallback(scene, LogoSent(status="call_failed"), f"the vision call failed ({error})")


def _fallback(scene: Scene, result: LogoSent, why: str) -> LogoSent:
    kept = "only the logo description" if result.sentence else "no logo additions"
    notices.post_notice(
        scene.job,
        notices.STARTING_PICTURE_LOGO,
        f"Scene {scene.number}'s starting picture gets {kept}: {why}.",
        Message.Level.PROBLEM,
        scene=scene.number,
    )
    return result


class BadLogoBox(ValueError):
    """The model's box does not identify a patch of the photo."""


def crop_logo(data: bytes, box: Sequence[int]) -> bytes:
    """Cut the padded box from the photo's upright pixels and enlarge small crops."""
    if len(box) != 4:
        raise BadLogoBox
    left, top, right, bottom = box
    if not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000):
        raise BadLogoBox
    x_margin, y_margin = (right - left) / 4, (bottom - top) / 4
    with PIL.Image.open(io.BytesIO(data)) as opened:
        photo = PIL.ImageOps.exif_transpose(opened)
        width, height = photo.size
        crop = photo.crop(
            (
                math.floor(max(0, left - x_margin) * width / 1000),
                math.floor(max(0, top - y_margin) * height / 1000),
                math.ceil(min(1000, right + x_margin) * width / 1000),
                math.ceil(min(1000, bottom + y_margin) * height / 1000),
            )
        )
    # Convert before resizing so indexed and 1-bit photos also use Lanczos.
    crop = crop.convert("RGBA" if crop.has_transparency_data else "RGB")
    long_side = max(crop.size)
    if long_side < MIN_CROP_SIDE:
        crop = crop.resize(
            tuple(max(1, round(side * MIN_CROP_SIDE / long_side)) for side in crop.size),
            PIL.Image.Resampling.LANCZOS,
        )
    saved = io.BytesIO()
    crop.save(saved, "PNG")
    return saved.getvalue()
