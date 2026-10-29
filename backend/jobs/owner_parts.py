"""The owner part (CONTEXT.md): a part of the product a B-roll scene shows that no photo
shows, drawn from the shop owner's words or invented by the planner (spec #210 point 20).

The part gets a picture of its own: drawn from its sentence beside a product photo, or the
shop owner's photo of it cleaned (changed as their words say, when they said anything). It
enters the job's library as an OwnerPart item, labelled, one item per part, and is shown to
the shop owner. The clip of a scene holding the part isn't made until they say yes to that
picture; a correction draws the one item again, for every scene holding the part with no
clip yet; a yes holds while the part and its photos are the same.

- `draw_part_picture(scene)`: draws the part's picture when it isn't the part's as it
  stands, and shows it; raises PartPictureFailed, said in the chat.
- `part_picture(scene)`: the library item of the part as it stands, for a sender.
- `is_approved(scene)`, `approve(scene)`, `words_correction`, `photo_correction`: the gate."""

import logging
import re
from collections.abc import Iterable
from typing import Any, Literal, Self

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field, model_validator

from adforge.retry import OutsideServiceDown
from chat import messages
from chat.models import Attachment, Message
from gateway.gateway import UnreadableImage, call_model
from gateway.types import (
    AnswerTookTooLong,
    BlockedBySafetyFilter,
    Handoff,
    Image,
    RefusedForMoney,
    UnusableReply,
)

from . import cut_out, library_items, reference_photos
from .job_locks import LIBRARY_LOCK, PART_PICTURE_LOCK, one_job_at_a_time
from .library import (
    LibraryEntry,
    OwnerPhoto,
    PagePhoto,
    PictureLabel,
    Recoloured,
    add_to_library,
    entry,
)
from .library import OwnerPart as PartSource
from .models import Job, LibraryItem, ProducedItem, ReferencePhoto, Scene, SceneStep
from .notices import post_notice, post_refused_by_the_filter
from .picture_check import StartingPictureCheckHandoff
from .planning import OwnerPart as OwnerPart  # the planner plans it; scenes keep it as is

logger = logging.getLogger(__name__)


def owner_part(scene: Scene | SceneStep) -> OwnerPart | None:
    """The owner part of the scene, or of the scene as the step started, or None when it has
    none."""
    return None if scene.owner_part is None else OwnerPart.model_validate(scene.owner_part)


def is_approved(scene: Scene) -> bool:
    """Whether the shop owner said yes to the scene's owner part as it stands: to the part's
    picture, drawn with the same part and the same photos of it as the scene has now. A part
    changed since, in its looks or its photos, needs a new yes."""
    return scene.owner_part_approved_at is not None and picture_is_current(scene)


def picture_is_current(scene: Scene) -> bool:
    """Whether the scene's owner part has its picture in the library, drawn from the part
    and its photos as the scene has them now: the library item still holds the drawing the
    scene was given, not one drawn since for another sentence."""
    part = owner_part(scene)
    drawn = scene.owner_part_drawn
    return (
        part is not None
        and scene.owner_part_picture is not None
        and drawn is not None
        and drawn.get("part") == scene.owner_part
        and drawn.get("photos") == _photo_list(scene.photos_in_use(), part.what)
        and drawn.get("file") == scene.owner_part_picture.file
    )


def part_picture(scene: Scene) -> LibraryEntry | None:
    """The library item of the scene's owner part as it stands, for a sender to send as it
    sends any other library picture of the scene; None when it has none."""
    if not picture_is_current(scene):
        return None
    assert scene.owner_part_picture is not None
    return entry(scene.owner_part_picture)


def _photo_list(rows: Iterable[ReferencePhoto], what: str) -> list[list[str]]:
    """The photos of the part `what` among `rows`, as kept in what a picture was drawn from."""
    return [list(photo) for photo in sorted(_photos_of_the_part(rows, what))]


def photo_drawn_without(scene: Scene, picture: ProducedItem) -> str | None:
    """The scene's owner part, by name, when `picture` was drawn without a photo of it the
    scene uses now, such as one kept for it by another scene's yes; else None."""
    part = owner_part(scene)
    if part is None or picture.step is None:
        return None
    drawn_with = _photos_of_the_part(picture.step.reference_photos.all(), part.what)
    missing = _photos_of_the_part(scene.photos_in_use(), part.what) - drawn_with
    return reference_photos.name_of(part.what) if missing else None


def approve(scene: Scene, said_by: Message | None = None) -> None:
    """The shop owner's yes to the scene's owner part in its picture, kept against
    `said_by`, the shop owner's message that said it (spec #247 point 12). The part is one thing
    for the whole ad: every other scene whose plan shows it and has no clip yet gets it,
    approved, with the scene's photos of it and its picture, so the shop owner isn't asked
    again. A scene with a different owner part, a product photo that shows the part, or a
    clip already made keeps its own. Raises ValueError for a scene with no owner part, or no
    picture of the part as it stands."""
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part")
    if not picture_is_current(scene):
        raise ValueError(f"scene {scene.number} has no picture of its part as it is")
    now = timezone.now()
    with transaction.atomic():
        scene.owner_part_approved_at = now
        scene.owner_part_approved_by = said_by
        scene.save(update_fields=["owner_part_approved_at", "owner_part_approved_by"])
        photos = _part_photos(scene, part.what)
        for other in _sharing(scene, part):
            _give(other, scene.owner_part, photos, part.what)
            other.owner_part_picture = scene.owner_part_picture
            other.owner_part_drawn = scene.owner_part_drawn
            other.owner_part_approved_at = now
            other.owner_part_approved_by = said_by
            other.save(update_fields=[*_PICTURE_FIELDS, *_YES_FIELDS])


# The scene's fields holding its owner part's picture, and the shop owner's yes to it.
_PICTURE_FIELDS = ("owner_part_picture", "owner_part_drawn")
_YES_FIELDS = ("owner_part_approved_at", "owner_part_approved_by")


def _no_yes(scene: Scene) -> None:
    """Clear the shop owner's yes to the scene's owner part, and the message that said it."""
    scene.owner_part_approved_at = None
    scene.owner_part_approved_by = None


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
    plans the scene again, so its picture of the part is drawn again before its clip."""
    scene.change_line(scene.line, broll={"owner_part": part})
    _no_yes(scene)
    scene.save(update_fields=["owner_part", "status", *_YES_FIELDS])
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
    the part's picture is drawn again before their clips. Their yes to the last picture no
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
    and a clean picture of only the part is drawn from it now: the part's picture from then on
    (changed as their words say), which every later starting picture of it is drawn with
    first too. Their words, when given, are the part's sentence from now on. The part is one
    thing for the whole ad, so every scene showing it without a clip yet takes the photo, and
    the part's picture is drawn again before their clips; their yes to the last one no
    longer counts. Raises ValueError for a scene
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
    other scene showing the part without a clip yet, and have the part's picture drawn again
    and approved again before their clips."""
    corrected = OwnerPart(what=part.what, source=part.source, looks=looks).model_dump()
    with transaction.atomic():
        for each in [scene, *_sharing(scene, part)]:
            _give(each, corrected, photos, part.what)
            each.status = Scene.Status.PLANNED
            each.save(update_fields=["status"])


# --- The part's own picture ------------------------------------------------------------------

# Its examples are made up, never from a test product (CODING_STANDARDS.md).
PART_PICTURE_CHECK = """\
You check a picture of one part of a product, drawn for a video ad. No photo of the product \
shows this part: it was drawn from one sentence that says how it looks. You are told the \
part and the sentence, and shown the drawn picture first and the picture it was drawn from \
second: a photo of the product, which shows what product the part belongs to but not the \
part, or a picture of the part itself that the sentence says how to change.

Work in two steps.
1. Look before you judge. Write everything the drawn picture shows: the part as it is drawn, \
its shape, colours, materials and any marks, and anything else in it, such as another part \
of the product, the whole product, another object, a hand or a scene behind it.
2. Then judge. Decide "pass" only when both are true: the part is the only thing in the \
picture, on a plain background; and it is as the sentence says: every look the sentence \
gives it is there, and nothing in it goes against the sentence or could not belong to the \
product in the second picture. Decide "fail" for anything else, such as the whole product \
or another part drawn with it, another object, a scene behind it, a look the sentence gives \
missing or changed, or a colour, shape or finish it gives drawn another way. Small \
differences in light, sharpness or size are not a fail."""

PART_PICTURES_SHOWN = "The drawn picture is first; the picture it was drawn from is second."


class PartPictureCheckHandoff(Handoff):
    """The part, the sentence it was drawn from, and which picture is which."""

    part: str
    looks: str
    pictures: str = PART_PICTURES_SHOWN


# Filled in from the top, so the check looks before it judges (vision-judges.md, rule 1). A
# comment, not a docstring: a docstring would be sent to the check in the answer's schema.
class PartPictureVerdict(BaseModel):
    picture_shows: str = Field(
        description="Everything the drawn picture shows: the part as drawn, and anything else."
    )
    verdict: Literal["pass", "fail"]
    problem: str | None = Field(
        description="When it fails: what is wrong, in one sentence. Null when it passes."
    )

    @model_validator(mode="after")
    def _a_failure_says_why(self) -> Self:
        if self.verdict == "fail" and not (self.problem or "").strip():
            raise ValueError("A picture that fails must say what is wrong.")
        return self


class PartPictureFailed(Exception):
    """The owner part's picture couldn't be had: why, in plain words. The chat was told.
    `retried` when nothing final went wrong, as an outage: it is tried again when the clip
    of a scene holding the part is next asked for."""

    def __init__(self, why: str, *, retried: bool = False) -> None:
        super().__init__(why)
        self.retried = retried


# What stops a picture being drawn this time only, in plain words: nothing paid is kept, so
# it is drawn when next asked for.
_THIS_TIME: dict[type[Exception], str] = {
    OutsideServiceDown: "the AI service didn't answer",
    RefusedForMoney: "the picture service refused it for the account's money",
    UnusableReply: "the AI model's answer couldn't be used",
    AnswerTookTooLong: "the AI model took too long",
    UnreadableImage: "a picture couldn't be read",
}


def drawn_by(part: OwnerPart) -> str:
    """Where the part's look came from, for the shop owner: "from your words", or "by us, as
    you asked"."""
    return "from your words" if part.source == "owner's words" else "by us, as you asked"


def drawn_from_its_sentence(part: OwnerPart) -> str:
    """The picture model's prompt for the part's picture drawn from its sentence, beside a
    photo of the product, which doesn't show the part."""
    name = reference_photos.name_of(part.what)
    return (
        f"A photo of only the {name} of the product in this photo. The photo doesn't show the "
        f'{name}: draw it exactly as this says, and add nothing to it: "{part.looks}" Give it '
        "no colour, shape, size, material, mark or detail the sentence doesn't, and don't take "
        "its look from another part of the product: the photo only shows which product it "
        f"belongs to. Only the {name}: no other part of the product, no other object, no "
        "person and no background. On a plain light grey background, in even, soft light."
    )


def changed_as_said(part: OwnerPart, words: str) -> str:
    """The picture model's prompt for the part's picture drawn from the clean picture of the
    shop owner's photo of it, changed as they said."""
    name = reference_photos.name_of(part.what)
    return (
        f"Change the {name} in this picture exactly as this says, and nothing else about it: "
        f'"{words}" Keep it the only thing in the picture, seen from the same side, on a plain '
        "light grey background, in even, soft light."
    )


def draw_part_picture(scene: Scene) -> LibraryEntry:
    """The picture of the scene's owner part as it stands, in the job's library, drawn when
    it isn't there yet: the shop owner's newest photo of the part cleaned (changed as their
    words with it say, when they said anything), else drawn from the part's sentence beside a
    product photo, and checked once each time it is drawn. It is labelled and kept as the
    part's library item, the same item drawn again in place when the part changed, and every
    scene holding the part as it stands, with no clip yet, takes it, its yes cleared. Shown to
    the shop owner in the chat. Raises PartPictureFailed when it can't be had, said in red.

    Run again, as after a worker stopped, it pays for nothing already paid for."""
    part = owner_part(scene)
    if part is None:
        raise ValueError(f"scene {scene.number} has no owner part")
    job = scene.job
    with one_job_at_a_time(PART_PICTURE_LOCK, job):
        scene.refresh_from_db()
        if picture_is_current(scene):
            assert scene.owner_part_picture is not None
            return entry(scene.owner_part_picture)
        name = reference_photos.name_of(part.what)
        # One part, one sentence: every scene holding it with no clip yet takes this one's.
        _one_sentence(scene, part)
        try:
            file, drawn_from = _drawn(scene, part)
        except PartPictureFailed as failed:
            _say_it_failed(scene, name, str(failed))
            raise
        except tuple(_THIS_TIME) as error:
            why = next(text for kind, text in _THIS_TIME.items() if isinstance(error, kind))
            _say_it_failed(scene, name, why, retried=True)
            raise PartPictureFailed(why, retried=True) from error
        label = library_items.label_photo(
            job,
            file,
            f"the picture of the owner part {name} at {file}",
            f"the {name}, one part of the product, alone",
            named=f"the picture of the {name}",
            then=f"Scene {scene.number}'s clip waits for it: it is tried again when the clip is "
            "next asked for.",
        )
        if label is None:
            raise PartPictureFailed(
                f"saying what the picture of the {name} shows failed", retried=True
            )
        item = _kept(job, part, file, _a_look(label))
        shown_at = timezone.now()
        drawn = {
            "part": scene.owner_part,
            "photos": _photo_list(scene.photos_in_use(), part.what),
            "file": file,
            "shown_at": shown_at.isoformat(),
        }
        for each in _holding(scene):
            each.owner_part_picture_id = item.pk
            each.owner_part_drawn = drawn
            _no_yes(each)
            each.save(update_fields=[*_PICTURE_FIELDS, *_YES_FIELDS])
        scene.refresh_from_db()
        post_notice(
            job,
            f"Scene {scene.number} shows the {name}, which none of the product's photos shows, "
            f"so we made this picture of it, {drawn_from} (library picture {item.number}). Every "
            f"scene showing it gets its clip once you say yes to it: say yes if the {name} "
            "looks right, or tell us what to change, or send a photo of it.",
            Message.Level.INFO,
            carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, file)],
        )
        return entry(item)


def _drawn(scene: Scene, part: OwnerPart) -> tuple[str, str]:
    """The part's picture, drawn, and what it was drawn from, in words for the shop owner."""
    job = scene.job
    photos = [
        row for row in _part_photos(scene, part.what) if row.use == ReferencePhoto.Use.PICTURE
    ]
    if photos:
        row = max(photos, key=lambda row: row.pk)
        # work draws pictures with this module's rules, so it is imported here.
        from .work import clean_picture

        try:
            clean = clean_picture(scene, row)
        except reference_photos.ReferencePhotoUnusable as unusable:
            raise PartPictureFailed(str(unusable)) from unusable
        if not row.words:
            return clean, "cleaned from your photo of it"
        return (
            _redrawn(job, part, changed_as_said(part, row.words), clean, row.words),
            "from your photo of it, changed as you said",
        )
    return (
        _redrawn(job, part, drawn_from_its_sentence(part), _product_photo(job), part.looks),
        f"drawn {drawn_by(part)}",
    )


def _redrawn(job: Job, part: OwnerPart, prompt: str, picture: str, looks: str) -> str:
    """The part's picture drawn from `picture` as `prompt` says, checked against the
    sentence `looks` and drawn again while its check fails."""
    try:
        return cut_out.redraw_with_check(
            job,
            purpose="make_part_picture",
            prompt=prompt,
            photo=picture,
            check=lambda file: check_part_picture(job, file, picture, part, looks),
            # The shop owner looks at it before any clip is made: they are its last check.
            unchecked_passes=True,
        )
    except cut_out.CutOutFailed as failed:
        raise PartPictureFailed(failed.why()) from failed


def check_part_picture(
    job: Job, file: str, drawn_from: str, part: OwnerPart, looks: str
) -> cut_out.Said:
    """What the part's picture check says of the picture at `file`, drawn from the picture
    at `drawn_from`: asked once, and answered from its record when asked the same again.
    A check that gives no answer leaves the picture unchecked, said in red: the shop owner
    still sees it before any clip is made."""
    name = reference_photos.name_of(part.what)
    named = f"the picture of the {name}"
    try:
        verdict = call_model(
            job=job,
            purpose="check_part_picture",
            instructions=PART_PICTURE_CHECK,
            handoff=PartPictureCheckHandoff(part=part.what, looks=looks),
            output=PartPictureVerdict,
            images=[
                Image(label="The drawn picture:", key=file),
                Image(label="The picture it was drawn from:", key=drawn_from),
            ],
            pay_once=True,
        )
    except BlockedBySafetyFilter as error:
        logger.warning("The check of %s was refused: %s", named, error)
        post_refused_by_the_filter(job, named, "it is shown to you unchecked: check it yourself")
        return cut_out.Unchecked("the AI model's safety filter refused to look at it", True)
    except (UnusableReply, AnswerTookTooLong, UnreadableImage, OutsideServiceDown) as error:
        logger.warning("The check of %s gave no answer: %s", named, error)
        post_notice(
            job,
            f"Checking {named} gave no answer, so it is shown to you unchecked: check it "
            "yourself before you say yes.",
            Message.Level.PROBLEM,
        )
        return cut_out.Unchecked("the AI model checking it gave no answer")
    if verdict.verdict == "pass":
        return None
    assert verdict.problem is not None, "a failing answer says why"
    return verdict.problem


def _product_photo(job: Job) -> str:
    """The product photo the part's picture is drawn beside: the library's front look photo,
    else its first look photo, else its first photo, as it is sent (its product-only version
    when it has one); for an ad kept before it had a library, its first product photo.
    Raises PartPictureFailed when it has no product photo."""
    photos = [
        library_items.to_send(job, item)
        for item in library_items.originals(job)
        if isinstance(item.source, PagePhoto | OwnerPhoto)
    ]
    looks = [item for item in photos if item.kind in library_items.LOOK_KINDS]
    fronts = [item for item in looks if item.side == "front"]
    chosen = fronts or looks or photos
    if chosen:
        return chosen[0].file
    # An ad whose photos were kept before it had a library.
    first = job.photos.filter(from_video=None).order_by("position").first()
    if first is None:
        raise PartPictureFailed("the ad has no product photo to draw it beside")
    return first.file


def _kept(job: Job, part: OwnerPart, file: str, label: PictureLabel) -> LibraryItem:
    """The part's library item, now `file` labelled `label`: the item it already has, drawn
    again in place, keeping its number, its recolours of the old drawing dropped (they show
    the part as it was); else a new item at the next number."""
    source = PartSource(part=reference_photos.name_of(part.what))
    with one_job_at_a_time(LIBRARY_LOCK, job):
        row = job.library.filter(source=source.model_dump()).order_by("number").first()
        if row is None:
            number = add_to_library(job, file, label, source).number
            return job.library.get(number=number)
        fields: dict[str, Any] = {"file": file, **label.model_dump()}
        for field, value in fields.items():
            setattr(row, field, value)
        # Read back through its schema, so a bad row is never kept.
        entry(row)
        with transaction.atomic():
            row.save(update_fields=list(fields))
            job.library.filter(
                source__came_from=Recoloured.model_fields["came_from"].default,
                source__of=row.number,
            ).delete()
        return row


def _a_look(label: PictureLabel) -> PictureLabel:
    """The part's label as a look picture (spec #210 point 20): the labelling call's kind
    when it is a look kind, else a side picture."""
    if label.kind in library_items.LOOK_KINDS:
        return label
    return PictureLabel.model_validate({**label.model_dump(), "kind": "side", "target": ""})


def _one_sentence(scene: Scene, part: OwnerPart) -> None:
    """Give every other scene whose own owner part is the same thing, with another sentence
    or other photos of it and no clip yet, the scene's part and photos of it: the part is one
    thing for the whole ad, drawn once, so a yes to its picture is a yes to one sentence."""
    photos = _part_photos(scene, part.what)
    wanted = _photo_list(photos, part.what)
    for other in scene.job.scenes.exclude(pk=scene.pk).filter(owner_part__isnull=False):
        own = owner_part(other)
        if (
            own is not None
            and reference_photos.same_thing(own.what, part.what)
            and (
                other.owner_part != scene.owner_part
                or _photo_list(other.photos_in_use(), part.what) != wanted
            )
            and not other.produced.filter(kind=ProducedItem.Kind.CLIP).exists()
        ):
            _give(other, scene.owner_part, photos, part.what)


def _holding(scene: Scene) -> list[Scene]:
    """The scene and every other scene of the ad holding its owner part as it stands, the
    same part and photos of it, with no clip yet: each takes the part's new picture."""
    part = owner_part(scene)
    assert part is not None
    photos = _photo_list(scene.photos_in_use(), part.what)
    return [scene] + [
        other
        for other in scene.job.scenes.exclude(pk=scene.pk).filter(owner_part__isnull=False)
        if other.owner_part == scene.owner_part
        and _photo_list(other.photos_in_use(), part.what) == photos
        and not other.produced.filter(kind=ProducedItem.Kind.CLIP).exists()
    ]


def _say_it_failed(scene: Scene, name: str, why: str, *, retried: bool = False) -> None:
    """Tell the chat, in red, once, that the part's picture couldn't be had, and why: when
    it is `retried`, that it is tried again."""
    then = (
        f"It is tried again when scene {scene.number}'s clip is next asked for."
        if retried
        else f"Tell us how the {name} looks, or send a photo of it."
    )
    text = (
        f"Making the picture of the {name} for scene {scene.number} failed: {why}. Scene "
        f"{scene.number}'s clip waits for a picture of it you said yes to. {then}"
    )
    logger.warning(text)
    # Said once: asked again with the same part, the same tries are read back from their
    # record, failing the same way for nothing.
    told = {warning["text"] for warning in Job.objects.get(pk=scene.job_id).warnings}
    if text not in told:
        post_notice(scene.job, text, Message.Level.PROBLEM)
