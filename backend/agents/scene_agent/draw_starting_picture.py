"""Draw from the agent's explicit picks, then check the exact input set."""

import json
from typing import ClassVar

from django.db import transaction
from django.db.models import Max
from pydantic import BaseModel, Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import PictureForm, picture_prompt
from gateway.gateway import edit_picture
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter, Image
from jobs import library_items, notices, work
from jobs.models import ProducedItem, SceneAgentRun

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
        photos: list[dict[str, str]] = []
        for number, pick in enumerate(self.picks, 1):
            if pick.item not in originals or not pick.take.strip():
                raise Refused("Pick a library photo and say what to take from it.")
            item = originals[pick.item]
            photos.append(
                {
                    "file": item.file,
                    "line": f"Image {number}: {pick.take.strip()}",
                    "library_number": str(item.number),
                    "original_file": item.file,
                }
            )
        drawn, shared = work.scene_place(scene, call.agent_fix.step.person_shown)
        if shared is None:
            raise Refused("The shared picture must be drawn first.")
        photos.append(
            {
                "file": shared.file,
                "line": f"Image {len(photos) + 1} shows the "
                + ("person and room" if drawn == "person_in_room" else "room alone")
                + "; keep the same look and light.",
                "library_number": "",
            }
        )
        prompt = picture_prompt(self, [p["line"] for p in photos])
        keys = [p["file"] for p in photos]
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
        ruling = check(scene.job, key, photos)
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
                    "call": str(call.pk),
                    "photos": photos,
                    "form": self.model_dump(),
                    "scene": {
                        field: getattr(scene, field)
                        for field in ("line", "shows", "person_shown", "place_id")
                    },
                },
            )
            SceneAgentRun.objects.filter(pk=window.pk).update(pictures=window.pictures + 1)
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
