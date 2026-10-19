"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner. The scene's clip isn't
made until the shop owner says yes to it in the scene's starting picture."""

import re
from collections.abc import Iterable
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, field_validator

from gateway.types import Handoff

from . import reference_photos
from .models import ProducedItem, ReferencePhoto, Scene, SceneStep
from .picture_check import StartingPictureCheckHandoff


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


def owner_part(scene: Scene | SceneStep) -> OwnerPart | None:
    """The owner part of the scene, or of the scene as the step started, or None when it has
    none."""
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


def drawn_from_words(step: SceneStep) -> OwnerPart | None:
    """The owner part the step's starting picture draws from its sentence: none when the scene
    has none, or when the shop owner sent a photo of it, which wins over any sentence and is
    drawn into the picture as their other reference photos are."""
    part = owner_part(step)
    if part is None or any(
        row.use == ReferencePhoto.Use.PICTURE and reference_photos.same_thing(row.thing, part.what)
        for row in step.reference_photos.all()
    ):
        return None
    return part


def writer_rule(part: OwnerPart) -> str:
    """Added to what the model writing a B-roll scene's prompts is told when the scene's owner
    part is drawn from its sentence, which it is given as said: no picture shows the part, so
    nothing but the sentence may say how it looks."""
    said = (
        "the shop owner's own words"
        if part.source == "owner's words"
        else "a sentence written for the shop owner, who sees the picture and approves it"
    )
    return (
        f'One part of the product this scene shows, "{part.what}", is in none of the pictures '
        f'sent: no photo shows it. How it looks comes only from {said}: "{part.looks}" '
        "Draw it exactly as that sentence says, nothing more: write every look the sentence "
        "gives it into the picture prompt, and never give it a colour, shape, size, material, "
        "mark or detail the sentence doesn't, or take its look from another part of the "
        "product. Code puts the sentence before your picture prompt too."
    )


def picture_label(part: OwnerPart) -> str:
    """Put by code before the starting picture's prompt, so the picture model always gets the
    owner part's sentence as given."""
    return (
        f"The {reference_photos.name_of(part.what)} is in none of the pictures: draw it exactly "
        f'as this says, and add nothing to it: "{part.looks}"'
    )


# Added after the picture check as tested (STARTING_PICTURE_CHECK, unchanged) when the picture
# draws an owner part from its sentence: the sentence stands in for the shop photo the check
# has of the rest of the product, for that part only.
CHECK_RULE = """\
One part of the product, named in "owner_part", is in no shop photo: it was drawn from the \
one sentence given there, which says how it looks. Use that sentence as you use the shop \
photo: only to know what that part is and how it is built. Fail that part only if it shows \
something a real one, built as the sentence says, can't have there."""


class OwnerPartToCheck(Handoff):
    what: str
    looks: str


class OwnerPartCheckHandoff(StartingPictureCheckHandoff):
    """The picture check's handoff for a picture that draws an owner part from its sentence:
    the part and the sentence."""

    owner_part: OwnerPartToCheck


def check_handoff(ask: int, part: OwnerPart | None) -> StartingPictureCheckHandoff:
    """The picture check's handoff for its ask number `ask`, with `part` when the picture draws
    it from its sentence."""
    if part is None:
        return StartingPictureCheckHandoff(ask=ask)
    return OwnerPartCheckHandoff(
        ask=ask, owner_part=OwnerPartToCheck(what=part.what, looks=part.looks)
    )


def words_correction(scene: Scene, words: str) -> None:
    """The shop owner said how their owner part should look: their words are its sentence from
    now on, and the scene's starting picture is drawn again from them before the clip. Their
    yes to the last picture no longer counts. Raises ValueError for a scene with no owner part,
    or no words."""
    _corrected(scene, _the_part(scene), words)


def photo_correction(scene: Scene, file: str, words: str = "") -> ReferencePhoto:
    """The shop owner sent a photo of their owner part, at `file`, and maybe words on it: it is
    kept as their reference photo of the part, in place of any older one, and a clean picture
    of only the part is drawn from it now, which every later starting picture of the scene is
    drawn with first. Their words, when given, are the part's sentence from now on. The scene's
    starting picture is drawn again before the clip, and their yes to the last one no longer
    counts. Raises ValueError for a scene with no owner part, and ReferencePhotoUnusable when
    the part can't be copied from the photo: then nothing is kept."""
    part = _the_part(scene)
    # work draws pictures with this module's rules, so it is imported here rather than above.
    from .work import clean_picture

    row = ReferencePhoto.objects.create(
        scene=scene,
        file=file,
        thing=reference_photos.name_of(part.what),
        use=ReferencePhoto.Use.PICTURE,
    )
    try:
        clean_picture(scene, row)
    except Exception:
        row.delete()
        raise
    row.save(update_fields=["drawn"])
    older = [
        other.pk
        for other in scene.photos_in_use().exclude(pk=row.pk)
        if reference_photos.same_thing(other.thing, part.what)
    ]
    scene.reference_photos.filter(pk__in=older).update(set_aside_at=timezone.now())
    _corrected(scene, part, words or part.looks)
    return row


def _the_part(scene: Scene) -> OwnerPart:
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part to correct.")
    return part


def _corrected(scene: Scene, part: OwnerPart, looks: str) -> None:
    """Keep `looks` as the part's sentence, and have the scene's starting picture drawn again
    and approved again before its clip."""
    scene.owner_part = OwnerPart(what=part.what, source=part.source, looks=looks).model_dump()
    scene.owner_part_approved_at = None
    scene.status = Scene.Status.PLANNED
    scene.save(update_fields=["owner_part", "owner_part_approved_at", "status"])
