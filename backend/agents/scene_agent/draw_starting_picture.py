"""Draw from the agent's explicit picks, then check the exact input set."""

import json
from typing import ClassVar

from django.db import transaction
from django.db.models import Max
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
from jobs import cut_and_send, library_items, notices, product_only, scene_references, work
from jobs.library import LibraryEntry
from jobs.models import ProducedItem, Scene, SceneAgentRun
from jobs.recolour import recolour

from .photos import SelectedPhoto
from .picture_check import check


class Pick(BaseModel):
    item: int = Field(description="A library photo number you have looked at.")
    take: str = Field(description="One line on what to take from this photo.")


class DrawStartingPicture(PictureForm, Tool):
    """Draw a starting picture from selected photos and the shared room/person picture.
    Each box is a checklist; write not needed here for a box that does not apply.
    The picture and the separate check's two answers come back for your next turn."""

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
    if window.pictures >= 6:
        raise Refused("All six starting pictures have been drawn; use one already drawn.")
    scene = call.agent_fix.step.scene
    originals = {item.number: item for item in library_items.originals(scene.job)}
    photos: list[SelectedPhoto] = []
    if len(picks) > 15:
        raise Refused(f"15 at most, {len(picks) - 15} left to remove from your picks.")
    if scene_references.excluded_among(scene, (pick.item for pick in picks)):
        raise Refused("An owner-excluded photo cannot be picked; choose another photo.")
    if any(pick.item not in originals or not pick.take.strip() for pick in picks):
        raise Refused("Pick a library photo and say what to take from it.")
    hard_list.refuse_part_picture_elsewhere(scene, [pick.item for pick in picks])
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
    if editing is None:
        drawn, shared = work.scene_place(scene, scene.person_shown)
        if shared is None:
            raise Refused("The shared picture must be drawn first.")
        photos.append(
            SelectedPhoto(
                file=shared.file,
                line=f"Image {len(photos) + 1} shows the "
                + ("person and room" if drawn == "person_in_room" else "room alone")
                + "; keep the same look and light.",
            )
        )
    colour = cut_and_send.planned_colour(scene)
    jobs = [photo.line for photo in photos]
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
    ruling = check(scene.job, key, photos, colour=colour)
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
            ruling=ruling,
            sent={
                "run": window.pk,
                "edited_from": editing.pk if editing else None,
                "call": str(call.pk),
                "photos": [photo.model_dump(exclude_none=True) for photo in photos],
                "form": form.model_dump(),
                "scene": {
                    field: getattr(scene, field)
                    for field in ("line", "shows", "person_shown", "place_id")
                },
            },
        )
        SceneAgentRun.objects.filter(pk=window.pk).update(pictures=window.pictures + 1)
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


def current_picture(call: ToolCall) -> ProducedItem | None:
    assert call.agent_fix is not None
    return (
        call.agent_fix.step.scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE)
        .order_by("-created_at", "-pk")
        .first()
    )


def result(made: ProducedItem) -> ToolResult:
    return ToolResult(
        text=f"Starting picture {made.pk}: {json.dumps(made.ruling)}",
        pictures=[Image(label=f"Starting picture {made.pk}", key=made.file)],
    )


def swapped(scene: Scene, originals: list[LibraryEntry], original: LibraryEntry) -> LibraryEntry:
    """Use the same product-only and colour helpers as the established clip sender."""
    item = product_only.to_send(scene.job, original)
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
    if item.number == reference:
        return item
    return recolour(scene.job, item.number, colour, reference=reference, scene=scene.number).picture
