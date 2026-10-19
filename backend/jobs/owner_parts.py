"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner. The scene's clip isn't
made until the shop owner says yes to it in the scene's starting picture."""

from typing import Literal

from pydantic import BaseModel, field_validator

from .models import Scene


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
