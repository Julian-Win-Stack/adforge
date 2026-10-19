"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner. The scene's clip isn't
made until the shop owner says yes to it in the scene's starting picture."""

import re
from collections.abc import Iterable
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, field_validator

from . import reference_photos
from .models import ProducedItem, ReferencePhoto, Scene, SceneStep


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
    """Whether the shop owner said yes to the scene's owner part: to the part as it stands,
    with the same photos of it, in a starting picture of the ad drawn by the time of the yes.
    A later picture of the part as approved, such as a fix's or another scene's, needs no
    new yes; one whose part changed since, in its looks or its photos, does."""
    approved_at = scene.owner_part_approved_at
    part = owner_part(scene)
    if approved_at is None or part is None:
        return False
    in_use = _photos_of_the_part(scene.photos_in_use(), part.what)
    seen = ProducedItem.objects.filter(
        job=scene.job,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        step__kind=SceneStep.Kind.STARTING_PICTURE,
        step__status=SceneStep.Status.FINISHED,
        step__owner_part=scene.owner_part,
        created_at__lte=approved_at,
    ).select_related("step")
    return any(
        picture.step is not None
        and _photos_of_the_part(picture.step.reference_photos.all(), part.what) == in_use
        for picture in seen
    )


def photo_drawn_without(scene: Scene, picture: ProducedItem) -> str | None:
    """The scene's owner part, by name, when `picture` was drawn without a photo of it the
    scene uses now, such as one kept for it by another scene's yes; else None."""
    part = owner_part(scene)
    if part is None or picture.step is None:
        return None
    drawn_with = _photos_of_the_part(picture.step.reference_photos.all(), part.what)
    missing = _photos_of_the_part(scene.photos_in_use(), part.what) - drawn_with
    return reference_photos.name_of(part.what) if missing else None


def approve(scene: Scene) -> None:
    """The shop owner's yes to the scene's owner part, kept for the whole ad: every other
    scene whose plan shows the same part gets it, approved, with the scene's photos of it, so
    its pictures and later fixes are drawn with it and the shop owner isn't asked again. A
    scene with a different owner part, or a product photo that shows the part, keeps its
    own."""
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part")
    now = timezone.now()
    with transaction.atomic():
        scene.owner_part_approved_at = now
        scene.save(update_fields=["owner_part_approved_at"])
        photos = [
            row
            for row in scene.photos_in_use()
            if reference_photos.same_thing(row.thing, part.what)
        ]
        for other in scene.job.scenes.exclude(pk=scene.pk).exclude(shows=""):
            if not _shows(other, part):
                continue
            other.change_line(other.line, broll={"owner_part": scene.owner_part})
            other.owner_part_approved_at = now
            other.save(update_fields=["owner_part", "owner_part_approved_at", "status"])
            _keep_photos(other, photos, part.what)


def _shows(scene: Scene, part: OwnerPart) -> bool:
    """Whether the scene's plan shows `part`: its own owner part is the same thing, or, with
    none, what it shows or how it uses the product names it and no product photo shows it."""
    own = owner_part(scene)
    if own is not None:
        return reference_photos.same_thing(own.what, part.what)
    if any(reference_photos.same_thing(str(need["what"]), part.what) for need in scene.needs):
        return False
    name = re.compile(rf"\b{re.escape(reference_photos.name_of(part.what))}\b", re.IGNORECASE)
    return bool(name.search(scene.shows) or name.search(scene.usage))


def _keep_photos(scene: Scene, photos: list[ReferencePhoto], what: str) -> None:
    """Have the scene use `photos` of the part `what`, each as a row of its own with the
    clean picture already drawn, and set aside any other photo of the part it used."""
    wanted = _photos_of_the_part(photos, what)
    for row in scene.photos_in_use():
        if reference_photos.same_thing(row.thing, what) and (row.file, row.use) not in wanted:
            row.set_aside_at = timezone.now()
            row.save(update_fields=["set_aside_at"])
    kept = _photos_of_the_part(scene.photos_in_use(), what)
    for row in photos:
        if (row.file, row.use) not in kept:
            ReferencePhoto.objects.create(
                scene=scene, file=row.file, thing=row.thing, use=row.use, drawn=row.drawn
            )


def _photos_of_the_part(rows: Iterable[ReferencePhoto], what: str) -> set[tuple[str, str]]:
    """The (file, use) of each photo in `rows` of the part `what`."""
    return {(row.file, row.use) for row in rows if reference_photos.same_thing(row.thing, what)}
