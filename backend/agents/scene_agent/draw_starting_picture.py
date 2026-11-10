"""Draw from explicit picks, then check against the picture's inherited evidence."""

import json
from typing import ClassVar

from django.db import transaction
from django.db.models import F, Max
from pydantic import BaseModel, Field

from agents import hard_list
from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import EditForm, PictureForm, edit_prompt, picture_prompt
from chat import messages
from chat.models import Attachment, Message
from gateway.gateway import edit_picture
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Image
from jobs import (
    cut_and_send,
    face_swap,
    library_items,
    notices,
    scene_references,
    side_photos,
    work,
)
from jobs.library import LibraryEntry
from jobs.models import ProducedItem, Scene, SceneAgentRun
from jobs.plan_rules import BOX_STATES, STATE_LOOKS, STYLE_LOOKS
from jobs.recolour import recolour

from . import lifecycle, media
from .photos import MOST_EDIT_PHOTOS, SelectedPhoto, edit_photos, evidence_photos
from .picture_check import check


class Pick(BaseModel):
    item: int = Field(description="A library photo number you have looked at.")
    take: str = Field(description="One line on what to take from this photo.")


class DrawStartingPicture(PictureForm, Tool):
    """Draw a starting picture from selected photos and the shared room/person picture.
    Each box is a checklist; write not needed here for a box that does not apply.
    The picture and the separate check's answers come back for your next turn."""

    name: ClassVar[str] = "draw_starting_picture"
    picks: list[Pick]

    def run(self, call: ToolCall) -> ToolResult:
        return make_picture(call, self, self.picks)


def make_picture(
    call: ToolCall,
    form: PictureForm | EditForm,
    picks: list[Pick],
    editing: ProducedItem | None = None,
) -> ToolResult:
    from . import current_run

    assert call.agent_fix is not None
    window = current_run(call.agent_fix)
    old = ProducedItem.objects.filter(
        step=call.agent_fix.step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        sent__call=str(call.pk),
    ).first()
    if old is not None:
        return result(old)
    if window.starting_pictures >= lifecycle.MOST_PICTURES:
        raise Refused(
            "All six starting-picture tries have been used; use one already drawn. "
            "End pictures have their own six tries."
        )
    scene = call.agent_fix.step.scene
    previous_picture = editing or current_picture(call)
    originals = {item.number: item for item in library_items.originals(scene.job)}
    photos: list[SelectedPhoto] = []
    if len(picks) > MOST_EDIT_PHOTOS:
        raise Refused(
            f"{MOST_EDIT_PHOTOS} at most, {len(picks) - MOST_EDIT_PHOTOS} left to remove "
            "from your picks."
        )
    if scene_references.excluded_among(scene, (pick.item for pick in picks)):
        raise Refused("An owner-excluded photo cannot be picked; choose another photo.")
    if any(pick.item not in originals or not pick.take.strip() for pick in picks):
        raise Refused("Pick a library photo and say what to take from it.")
    hard_list.refuse_part_picture_elsewhere(scene, [pick.item for pick in picks])
    if editing is None:
        _refuse_without_a_product_photo(scene, originals, picks)
    packaging = _packaging_photo(scene, originals, picks)
    if packaging is not None and len(picks) >= MOST_EDIT_PHOTOS:
        raise Refused(
            f"{MOST_EDIT_PHOTOS} at most, counting the packaging photo code adds; "
            "remove one of your picks."
        )
    talking = work.talking_portrait_and_room(scene) if editing is None else None
    if talking is not None and len(picks) + (packaging is not None) > MOST_EDIT_PHOTOS - 1:
        raise Refused(
            f"{MOST_EDIT_PHOTOS - 1} at most for her talking, counting the packaging photo and "
            "her portrait and the room code adds; remove one of your picks."
        )
    for number, pick in enumerate(picks, 2 if editing else 1):
        original = originals[pick.item]
        item = swapped(scene, list(originals.values()), original)
        photos.append(
            SelectedPhoto(
                file=item.file,
                line=f"Image {number}: {pick.take.strip()}",
                library_number=original.number,
                original_file=original.file,
            )
        )
    if packaging is not None:
        photos.append(
            SelectedPhoto(
                file=swapped(scene, list(originals.values()), packaging).file,
                line=f"Image {len(photos) + (2 if editing else 1)}: the product's packaging; "
                "draw its box as this photo shows it.",
                library_number=packaging.number,
                original_file=packaging.file,
            )
        )
    if editing is None:
        drawn, shared = work.scene_place(scene, scene.person_shown)
        if shared is None:
            raise Refused("The shared picture must be drawn first.")
        if talking is not None:
            portrait, room = talking
            photos.append(
                SelectedPhoto(file=portrait.file, line=f"Image {len(photos) + 1}: {PORTRAIT_JOB}")
            )
            photos.append(
                SelectedPhoto(file=room.file, line=f"Image {len(photos) + 1}: {ROOM_JOB}")
            )
        else:
            photos.append(
                SelectedPhoto(
                    file=shared.file,
                    line=f"Image {len(photos) + 1} shows the "
                    + (
                        "person and room; keep the same look and light, except that a worn "
                        "product's outfit comes from its page photo."
                        if drawn == "person_in_room"
                        else "room alone; keep the same look and light."
                    ),
                )
            )
    evidence = photos
    if editing is not None:
        evidence = evidence_photos(editing, adding=photos)
        # An edit sends its chain's earlier product photos again (image 1 has the room).
        photos = edit_photos(editing, evidence, adding=photos, making="edited picture")
    colour = cut_and_send.planned_colour(scene)
    # A talking scene's camera setup is said first, before the photos' jobs.
    jobs = [*talking_jobs(scene), *(photo.line for photo in photos)]
    if scene.piece is not None:
        jobs.append(f"Draw the product in the page video's colour: {colour}.")
    prompt = edit_prompt(form, jobs) if isinstance(form, EditForm) else picture_prompt(form, jobs)
    keys = ([editing.file] if editing else []) + [p.file for p in photos]
    previous = (
        ModelCall.objects.filter(
            tool_call=call, purpose="draw_scene_picture", outcome=ModelCall.Outcome.SUCCEEDED
        )
        .order_by("created_at", "pk")
        .first()
    )
    try:
        key = (
            str(previous.output["file"])
            if previous and previous.output
            else edit_picture(
                job=scene.job, purpose="draw_scene_picture", prompt=prompt, pictures=keys
            )
        )
        if isinstance(key, BlockedBySafetyFilter):
            raise key
    except BlockedBySafetyFilter:
        notices.post_refused_by_the_filter(
            scene.job, "the scene's selected photos", "no starting picture was drawn"
        )
        raise Refused(
            "The picture model's safety filter refused these photos or this picture."
        ) from None
    ruling = check(scene.job, key, evidence, scene=scene.number, colour=colour, starting=scene)
    with transaction.atomic():
        version = (
            scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE).aggregate(
                last=Max("version")
            )["last"]
            or 0
        ) + 1
        made = ProducedItem.objects.create(
            job=scene.job,
            scene=scene,
            step=call.agent_fix.step,
            kind=ProducedItem.Kind.STARTING_PICTURE,
            file=key,
            version=version,
            text=prompt,
            change=window.instruction if version > 1 else "",
            ruling=ruling,
            sent={
                "run": window.pk,
                "planned_colour": colour,
                "edited_from": editing.pk if editing else None,
                "compared_with": previous_picture.pk if previous_picture else None,
                "call": str(call.pk),
                "end_picture_dropped": bool(
                    previous_picture
                    and ProducedItem.objects.filter(
                        kind=ProducedItem.Kind.END_PICTURE, made_from=previous_picture
                    ).exists()
                ),
                "photos": [photo.model_dump(exclude_none=True) for photo in photos],
                "form": form.model_dump(),
                "scene": {
                    field: getattr(scene, field)
                    for field in ("line", "shows", "person_shown", "place_id")
                },
            },
        )
        SceneAgentRun.objects.filter(pk=window.pk).update(
            starting_pictures=F("starting_pictures") + 1
        )
        if not scene.produced.filter(kind=ProducedItem.Kind.CLIP).exists():
            messages.add(
                call.session,
                role=Message.Role.AGENT,
                carrying=[
                    messages.AttachedFile(
                        Attachment.Kind.PICTURE,
                        made.file,
                        scene.number,
                        work.version_label(made),
                    )
                ],
            )
    return result(made)


# A talking scene's starting picture takes her face from the portrait and the room from the
# room alone (work.talking_portrait_and_room), and its camera, framing and pose only from
# the words.
PORTRAIT_JOB = (
    "the person, the same person with the same face; take only the face and hair from it, "
    "everything else comes from these words."
)
ROOM_JOB = (
    "the room, empty; draw the person in this room with its look and its light, seen from "
    "where these words put the camera."
)


def talking_jobs(scene: Scene) -> list[str]:
    """What a talking scene's starting picture is drawn in: its talking style and its product
    state, each as it looks (jobs.plan_rules)."""
    if scene.kind != Scene.Kind.TALKING:
        return []
    return [
        f"{what}: {looks[value]}."
        for what, value, looks in (
            ("How she is filmed", scene.talking_style, STYLE_LOOKS),
            ("Where the product is", scene.product_state, STATE_LOOKS),
        )
        if value in looks
    ]


def _refuse_without_a_product_photo(
    scene: Scene, originals: dict[int, LibraryEntry], picks: list[Pick]
) -> None:
    """Refused when a new starting picture picks none of the plan's product photos (the one
    exact product, every scene the same), naming them. Photos the owner excluded for the
    scene, or that the library no longer holds, don't count; when none is left, the picture
    is drawn without one, with a developer notice."""
    planned = scene.job.product_photos
    if not planned:
        return
    excluded = set(scene_references.excluded_among(scene, planned))
    allowed = [number for number in planned if number in originals and number not in excluded]
    if not allowed:
        notices.post_notice(
            scene.job,
            notices.NO_PRODUCT_PHOTO_TO_PICK,
            f"Scene {scene.number}'s starting picture was drawn without the plan's product "
            f"photos ({', '.join(str(number) for number in planned)}): the owner excluded "
            "them for this scene or the library no longer holds them.",
            Message.Level.PROBLEM,
        )
        return
    if not any(pick.item in allowed for pick in picks):
        raise Refused(
            "Pick at least one of the product photos, "
            + media.items(allowed)
            + ": every scene draws the product from them, so it is the same product at the "
            "same size in every scene. Other photos can be picked beside them."
        )


def _packaging_photo(
    scene: Scene, originals: dict[int, LibraryEntry], picks: list[Pick]
) -> LibraryEntry | None:
    """The talking scene's packaging photo, for a box state, when it isn't picked already. One
    the owner excluded or the library doesn't hold is left out, and a box state naming none
    draws an unseen box, each with a developer notice."""
    number = scene.packaging_photo
    if scene.kind != Scene.Kind.TALKING or scene.product_state not in BOX_STATES:
        return None
    if number is None:
        # The box rule is only told since Nov 9 2026, so a plan may name a box state with no
        # packaging photo: the box is drawn unseen.
        notices.post_notice(
            scene.job,
            notices.PACKAGING_PHOTO_NOT_SENT,
            f'Scene {scene.number} is "{scene.product_state}" but names no packaging photo, '
            "so its starting picture draws a box no photo shows.",
            Message.Level.PROBLEM,
        )
        return None
    if any(pick.item == number for pick in picks):
        return None
    why = (
        "the owner excluded it"
        if scene_references.excluded_among(scene, [number])
        else None
        if number in originals
        else "the library has no such picture"
    )
    if why is not None:
        notices.post_notice(
            scene.job,
            notices.PACKAGING_PHOTO_NOT_SENT,
            f"Scene {scene.number}'s packaging photo, picture {number}, was left out of its "
            f'starting picture ("{scene.product_state}"): {why}.',
            Message.Level.PROBLEM,
        )
        return None
    return originals[number]


def current_picture(call: ToolCall) -> ProducedItem | None:
    """The working picture: a draw, or a picture selected for filming after that draw."""
    assert call.agent_fix is not None
    picture = (
        call.agent_fix.step.scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE)
        .order_by("-created_at", "-pk")
        .first()
    )
    filmed = (
        call.agent_fix.tries.filter(picture__isnull=False)
        .select_related("picture")
        .order_by("-created_at", "-pk")
        .first()
    )
    if filmed is not None and (picture is None or filmed.created_at > picture.created_at):
        return filmed.picture
    return picture


def result(made: ProducedItem) -> ToolResult:
    assert made.scene_id is not None
    previous_id = (made.sent or {}).get("compared_with")
    previous = (
        ProducedItem.objects.filter(pk=previous_id, scene_id=made.scene_id).first()
        if previous_id
        else None
    )
    pictures = [Image(label=f"Starting picture {made.pk}", key=made.file)]
    comparison = ""
    dropped = (
        " The old end picture was dropped because the starting picture changed."
        if (made.sent or {}).get("end_picture_dropped")
        else ""
    )
    if previous is not None:
        pictures.insert(
            0, Image(label=f"Previous starting picture {previous.pk}", key=previous.file)
        )
        comparison = (
            f" Compared with starting picture {previous.pk}: {json.dumps(previous.ruling)}."
            " Say whether the change you wanted actually shows before choosing a picture"
            " to film, or draw again without filming."
        )
    return ToolResult(
        text=f"Starting picture {made.pk}: {json.dumps(made.ruling)}{comparison}{dropped}",
        pictures=pictures,
    )


def swapped(scene: Scene, originals: list[LibraryEntry], original: LibraryEntry) -> LibraryEntry:
    """Crop to the product first (its true crop, else the redrawn cut-out when the side
    picker failed), swap any remaining stranger, then match the planned colour."""
    item = side_photos.for_a_pick(scene.job, original, scene=scene.number)
    item = face_swap.for_clip(scene.job, item, scene=scene.number)
    colour = cut_and_send.planned_colour(scene)
    ad = cut_and_send.ad_colour(scene.job)
    own = cut_and_send.in_their_own_colour(scene.job, originals)
    if item.colour in (colour, "none") or original.number in own:
        return item
    video = scene.piece.get("video") if scene.piece else None
    reference = cut_and_send.colour_reference(
        scene.job, originals, video, in_ad_colour=colour == ad
    )
    if reference is None:
        notices.post_notice(
            scene.job,
            notices.NO_COLOUR_REFERENCE,
            f"Scene {scene.number}: library item {original.number} could not be recoloured "
            "because no colour reference was available; its original colour was kept.",
            Message.Level.PROBLEM,
        )
        return item
    if reference in (original.number, item.number):
        return item
    return recolour(scene.job, item.number, colour, reference=reference, scene=scene.number).picture
