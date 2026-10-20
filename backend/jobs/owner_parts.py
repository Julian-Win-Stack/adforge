"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner. The scene's clip isn't
made until the shop owner says yes to it in the scene's starting picture."""

import re
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from gateway.types import Handoff

from . import reference_photos
from .models import ProducedItem, ReferencePhoto, Scene, SceneStep
from .picture_check import StartingPictureCheckHandoff
from .planning import OwnerPart as OwnerPart  # the planner plans it; scenes keep it as is


def owner_part(scene: Scene | SceneStep) -> OwnerPart | None:
    """The owner part of the scene, or of the scene as the step started, or None when it has
    none."""
    return None if scene.owner_part is None else OwnerPart.model_validate(scene.owner_part)


def is_approved(scene: Scene) -> bool:
    """Whether the shop owner said yes to the scene's owner part as it stands: the picture they
    said yes to, of this scene or another showing the part, was drawn with the same part and
    the same photos of it as the scene has now. A later picture of the part as approved, such
    as a fix's or another scene's, needs no new yes; one whose part changed since, in its looks
    or its photos, does, even when an older picture drawn the same way exists."""
    part = owner_part(scene)
    picture = scene.owner_part_approved_picture
    if part is None or scene.owner_part_approved_at is None or picture is None:
        return False
    step = picture.step
    return (
        step is not None
        and step.owner_part == scene.owner_part
        and _photos_of_the_part(step.reference_photos.all(), part.what)
        == _photos_of_the_part(scene.photos_in_use(), part.what)
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
    """The shop owner's yes to the scene's owner part in its current starting picture. The part
    is one thing for the whole ad: every other scene whose plan shows it and has no clip yet
    gets it, approved, with the scene's photos of it, so its pictures and later fixes are drawn
    with it and the shop owner isn't asked again. A scene with a different owner part, a
    product photo that shows the part, or a clip already made keeps its own. Raises ValueError
    for a scene with no owner part, or no starting picture of the part as it stands."""
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part")
    picture = scene.current_picture()
    if picture is None:
        raise ValueError(f"scene {scene.number} has no starting picture of its part as it is")
    now = timezone.now()
    with transaction.atomic():
        scene.owner_part_approved_at = now
        scene.owner_part_approved_picture = picture
        scene.save(update_fields=["owner_part_approved_at", "owner_part_approved_picture"])
        photos = _part_photos(scene, part.what)
        for other in _sharing(scene, part):
            _give(other, scene.owner_part, photos, part.what)
            other.owner_part_approved_at = now
            other.owner_part_approved_picture = picture
            other.save(update_fields=["owner_part_approved_at", "owner_part_approved_picture"])


def _sharing(scene: Scene, part: OwnerPart) -> list[Scene]:
    """Every other scene of the ad whose plan shows `part` and whose clip isn't made yet: the
    part is one thing for the whole ad, so a yes or a correction on one is on them all. A scene
    whose clip is made keeps its clip and its part."""
    return [
        other
        for other in scene.job.scenes.exclude(pk=scene.pk).exclude(shows="")
        if _shows(other, part) and not other.produced.filter(kind=ProducedItem.Kind.CLIP).exists()
    ]


def _give(
    scene: Scene, part: dict[str, object] | None, photos: list[ReferencePhoto], what: str
) -> None:
    """Give the scene the owner part `part` and `photos` of it, unapproved: a changed part
    plans the scene again, so its starting picture is drawn again before its clip."""
    scene.change_line(scene.line, broll={"owner_part": part})
    scene.owner_part_approved_at = None
    scene.owner_part_approved_picture = None
    scene.save(
        update_fields=[
            "owner_part",
            "owner_part_approved_at",
            "owner_part_approved_picture",
            "status",
        ]
    )
    _keep_photos(scene, photos, what)


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


def _part_photos(scene: Scene, what: str) -> list[ReferencePhoto]:
    """The scene's photos of the part `what` in use."""
    return [row for row in scene.photos_in_use() if reference_photos.same_thing(row.thing, what)]


def _keep_photos(scene: Scene, photos: list[ReferencePhoto], what: str) -> None:
    """Have the scene use `photos` of the part `what`, each as a row of its own with the
    clean picture already drawn, and set aside any other photo of the part it used."""
    wanted = _photos_of_the_part(photos, what)
    for row in scene.photos_in_use():
        if reference_photos.same_thing(row.thing, what) and _photo(row) not in wanted:
            row.set_aside_at = timezone.now()
            row.save(update_fields=["set_aside_at"])
    kept = _photos_of_the_part(scene.photos_in_use(), what)
    for row in photos:
        if _photo(row) not in kept:
            ReferencePhoto.objects.create(
                scene=scene,
                file=row.file,
                thing=row.thing,
                use=row.use,
                drawn=row.drawn,
                words=row.words,
            )


def _photo(row: ReferencePhoto) -> tuple[str, str, str]:
    return (row.file, row.use, row.words)


def _photos_of_the_part(rows: Iterable[ReferencePhoto], what: str) -> set[tuple[str, str, str]]:
    """The (file, use, words) of each photo in `rows` of the part `what`."""
    return {_photo(row) for row in rows if reference_photos.same_thing(row.thing, what)}


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


def photo_words(step: SceneStep) -> tuple[OwnerPart, str] | None:
    """The owner part the step's starting picture draws from the shop owner's photo of it, and
    what they said to change in that photo, when they said anything: their words go with the
    photo into every picture of the part."""
    part = owner_part(step)
    if part is None:
        return None
    words = [
        row.words
        for row in step.reference_photos.all()
        if row.use == ReferencePhoto.Use.PICTURE
        and row.words
        and reference_photos.same_thing(row.thing, part.what)
    ]
    return (part, words[-1]) if words else None


def photo_words_rule(part: OwnerPart, words: str) -> str:
    """Added to what the model writing a B-roll scene's prompts is told when the shop owner
    sent a photo of the scene's owner part and said what to change in it."""
    return (
        f'One part of the product this scene shows, "{part.what}", comes from the shop owner\'s '
        f'own photo of it, changed as they said: "{words}" Draw it as their photo shows it, '
        "with exactly that change and nothing else changed: write the change into the picture "
        "prompt. Code puts their words before your picture prompt too."
    )


def photo_words_label(part: OwnerPart, words: str) -> str:
    """Put by code before the starting picture's prompt, so the picture model always gets the
    shop owner's change to their photo of the part as given."""
    return (
        f"Change the {reference_photos.name_of(part.what)} from the shop owner's own picture of "
        f'it exactly as this says, and nothing else about it: "{words}"'
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
    now on, and, when a photo of it is in use, what to change in the photo. The part is one
    thing for the whole ad, so every scene showing it without a clip yet takes the change, and
    its starting picture is drawn again before the clip. Their yes to the last picture no
    longer counts. Raises ValueError for a scene with no owner part."""
    part = _the_part(scene)
    photos = [
        ReferencePhoto.objects.create(
            scene=scene, file=row.file, thing=row.thing, use=row.use, drawn=row.drawn, words=words
        )
        if row.use == ReferencePhoto.Use.PICTURE
        else row
        for row in _part_photos(scene, part.what)
    ]
    _corrected(scene, part, words, photos)


def photo_correction(scene: Scene, file: str, words: str = "") -> ReferencePhoto:
    """The shop owner sent a photo of their owner part, at `file`, and maybe words on what to
    change in it: it is kept as their reference photo of the part, in place of any older one,
    and a clean picture of only the part is drawn from it now, which every later starting
    picture of it is drawn with first, changed as their words say. Their words, when given, are
    the part's sentence from now on. The part is one thing for the whole ad, so every scene
    showing it without a clip yet takes the photo, and its starting picture is drawn again
    before the clip; their yes to the last one no longer counts. Raises ValueError for a scene
    with no owner part, and ReferencePhotoUnusable when the part can't be copied from the
    photo: then nothing is kept."""
    part = _the_part(scene)
    # work draws pictures with this module's rules, so it is imported here rather than above.
    from .work import clean_picture

    row = ReferencePhoto.objects.create(
        scene=scene,
        file=file,
        thing=reference_photos.name_of(part.what),
        use=ReferencePhoto.Use.PICTURE,
        words=words,
    )
    try:
        clean_picture(scene, row)
    except Exception:
        row.delete()
        raise
    row.save(update_fields=["drawn"])
    _corrected(scene, part, words or part.looks, [row])
    return row


def _the_part(scene: Scene) -> OwnerPart:
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part to correct.")
    return part


def _corrected(scene: Scene, part: OwnerPart, looks: str, photos: list[ReferencePhoto]) -> None:
    """Keep `looks` as the part's sentence and `photos` as its photos, on the scene and every
    other scene showing the part without a clip yet, and have each scene's starting picture
    drawn again and approved again before its clip."""
    corrected = OwnerPart(what=part.what, source=part.source, looks=looks).model_dump()
    with transaction.atomic():
        for each in [scene, *_sharing(scene, part)]:
            _give(each, corrected, photos, part.what)
            each.status = Scene.Status.PLANNED
            each.save(update_fields=["status"])
