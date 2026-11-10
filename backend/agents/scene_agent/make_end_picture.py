"""Keep an action's result linked to the starting picture it was edited from."""

import json
from typing import ClassVar

from django.db import transaction
from django.db.models import F, Max
from pydantic import Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import EditForm, edit_prompt
from gateway.gateway import edit_picture
from gateway.models import ModelCall
from gateway.types import Image
from jobs import cut_and_send
from jobs.models import ProducedItem, SceneAgentRun

from . import lifecycle
from .draw_starting_picture import current_picture
from .photos import edit_photos, evidence_photos
from .picture_check import check


class MakeEndPicture(EditForm, Tool):
    """Edit the starting picture into an end picture: the clip's last frame, sent to H3 with
    the starting picture. Use it, from the first try on, when the clip must end on a result
    where the product touched; when a push-in must end on a crop of the real label; when the
    clip drifts or ends in the wrong place; or when the action must stop at a set point, such
    as a press that stops part way down. These are examples, not a limit.
    Each run has six end-picture tries, separate from its six starting-picture tries."""

    name: ClassVar[str] = "make_end_picture"
    keep: list[str] = Field(
        description="Everything else stays exactly as in the starting picture; name what stays. "
        "For a result, the product or hand stays on the spot that changes."
    )
    change: str = Field(
        description="Change only what the clip's last frame must show: the result at the spot "
        "the product touched, the crop of the real label, where the clip ends, or where the "
        "action stops; say what it becomes."
    )

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run

        assert call.agent_fix is not None
        scene = call.agent_fix.step.scene
        old = scene.produced.filter(
            kind=ProducedItem.Kind.END_PICTURE, sent__call=str(call.pk)
        ).first()
        if old is not None:
            return result(old)
        picture = current_picture(call)
        if picture is None:
            raise Refused("There is no starting picture yet; draw one first.")
        if not self.change.strip() or self.change.strip().lower() == "not needed here":
            raise Refused("Write the action's result to change in the end picture.")
        window = current_run(call.agent_fix)
        if window.end_pictures >= window.end_picture_limit:
            raise Refused(lifecycle.picture_tries_used_up(scene, window, "end"))
        photos = evidence_photos(picture)
        sent = edit_photos(picture, photos, making="end picture")
        prompt = edit_prompt(self, [photo.line for photo in sent])
        previous = ModelCall.objects.filter(
            tool_call=call, purpose="make_end_picture", outcome=ModelCall.Outcome.SUCCEEDED
        ).first()
        # A failed edit is recorded for the developer by the loop, with the scene.
        key = (
            str(previous.output["file"])
            if previous and previous.output
            else edit_picture(
                job=scene.job,
                purpose="make_end_picture",
                prompt=prompt,
                pictures=[picture.file, *(photo.file for photo in sent)],
            )
        )
        ruling = check(
            scene.job,
            key,
            photos,
            scene=scene.number,
            colour=cut_and_send.planned_colour(scene),
            for_owner=False,
        )
        with transaction.atomic():
            version = (
                scene.produced.filter(kind=ProducedItem.Kind.END_PICTURE).aggregate(
                    last=Max("version")
                )["last"]
                or 0
            ) + 1
            made = ProducedItem.objects.create(
                job=scene.job,
                scene=scene,
                step=call.agent_fix.step,
                kind=ProducedItem.Kind.END_PICTURE,
                made_from=picture,
                file=key,
                version=version,
                text=prompt,
                ruling=ruling,
                sent={
                    "call": str(call.pk),
                    "run": window.pk,
                    "form": self.model_dump(),
                    "photos": [photo.model_dump(exclude_none=True) for photo in sent],
                },
            )
            SceneAgentRun.objects.filter(pk=window.pk).update(end_pictures=F("end_pictures") + 1)
        return result(made)


def newest_for(picture: ProducedItem) -> ProducedItem | None:
    """A starting picture's newest end picture; other starting pictures never match."""
    return (
        ProducedItem.objects.filter(kind=ProducedItem.Kind.END_PICTURE, made_from=picture)
        .order_by("-created_at", "-pk")
        .first()
    )


def result(made: ProducedItem) -> ToolResult:
    picture = made.made_from
    assert picture is not None
    return ToolResult(
        text=(
            f"End picture {made.pk}: {json.dumps(made.ruling)}. "
            "Say whether the end picture shows the ending you wanted, and whether only that "
            "changed; for a result, whether the product or hand sits on the spot that changed. "
            "If not, make the end picture again from the starting picture; each redo uses one "
            "of the six end-picture tries, separate from the six starting-picture tries."
            + lifecycle.picture_words(made)
        ),
        pictures=[
            Image(label=f"Starting picture {picture.pk}", key=picture.file),
            Image(label=f"End picture {made.pk}", key=made.file),
        ],
    )
