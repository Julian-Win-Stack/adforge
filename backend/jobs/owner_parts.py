"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner. The scene's clip isn't
made until the shop owner says yes to it in the scene's starting picture."""

from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, field_validator

from .models import ReferencePhoto, Scene


class OwnerPart(BaseModel):
    """An owner part as the scene keeps it."""

    what: str
    source: Literal["owner's words", "invented"]
    looks: str

    @field_validator("what", "looks")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


def owner_part(scene: Scene) -> OwnerPart | None:
    """The scene's owner part, or None when it has none."""
    return None if scene.owner_part is None else OwnerPart.model_validate(scene.owner_part)


def is_approved(scene: Scene) -> bool:
    """Whether the shop owner said yes to the scene's owner part in its starting picture: a
    yes given before the current picture was drawn doesn't count for it."""
    approved_at = scene.owner_part_approved_at
    if approved_at is None:
        return False
    picture = scene.current_picture()
    return picture is None or picture.created_at <= approved_at


# What T3 needs of T2 and T4 until their tickets merge: T4 owns `approve` (it also copies the
# part to every scene showing it) and T2 owns the two corrections (T2 also draws the photo's
# clean picture). At merge their versions replace these.


def approve(scene: Scene) -> None:
    """The shop owner said yes to the scene's owner part in its current starting picture."""
    scene.owner_part_approved_at = timezone.now()
    scene.save(update_fields=["owner_part_approved_at"])


def words_correction(scene: Scene, words: str) -> None:
    """The shop owner said how the part should look: it is drawn again from their words,
    and their earlier yes no longer counts."""
    part = owner_part(scene)
    assert part is not None, "only a scene with an owner part is corrected"
    scene.owner_part = part.model_copy(update={"looks": words}).model_dump()
    scene.owner_part_approved_at = None
    scene.save(update_fields=["owner_part", "owner_part_approved_at"])


def photo_correction(scene: Scene, file: str, words: str = "") -> None:
    """The shop owner sent a photo of the part, and maybe words: the part is drawn again
    from the photo, kept as a reference photo of it, and from their words when given."""
    part = owner_part(scene)
    assert part is not None, "only a scene with an owner part is corrected"
    ReferencePhoto.objects.create(
        scene=scene, file=file, thing=part.what, use=ReferencePhoto.Use.PICTURE
    )
    if words:
        words_correction(scene, words)
    else:
        scene.owner_part_approved_at = None
        scene.save(update_fields=["owner_part_approved_at"])
