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
from jobs.models import AgentFixTry, ProducedItem, SceneAgentRun

from .draw_starting_picture import current_picture
from .photos import evidence_photos
from .picture_check import check


class MakeEndPicture(EditForm, Tool):
    """After a failed product check, when the action's result looks wrong (what the product
    leaves behind or turns into), edit the starting picture into an end picture of that result."""

    name: ClassVar[str] = "make_end_picture"
    keep: list[str] = Field(
        description="Everything else stays exactly as in the starting picture; name what stays. "
        "The product or hand stays on the spot that changes."
    )
    change: str = Field(
        description="Change only the result of the action, at the spot the product touched; "
        "say what that spot becomes."
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
        if not AgentFixTry.objects.filter(
            agent_fix__step__scene=scene, clip__ruling__checks__product="fail"
        ).exists():
            raise Refused(
                "No try on this scene has failed the product check yet; film a try first "
                "and read its product check before making an end picture."
            )
        if not self.change.strip() or self.change.strip().lower() == "not needed here":
            raise Refused("Write the action's result to change in the end picture.")
        window = current_run(call.agent_fix)
        if window.pictures >= 6:
            raise Refused("All six picture slots have been used; use a picture already made.")
        photos = evidence_photos(picture)
        prompt = edit_prompt(self, [])
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
                pictures=[picture.file],
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
                sent={"call": str(call.pk), "run": window.pk, "form": self.model_dump()},
            )
            SceneAgentRun.objects.filter(pk=window.pk).update(pictures=F("pictures") + 1)
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
            "Say whether the product or hand sits on the spot that changed, and whether "
            "only that spot changed. If either fails, make the end picture again from the "
            "starting picture; each redo uses one of the six picture slots."
        ),
        pictures=[
            Image(label=f"Starting picture {picture.pk}", key=picture.file),
            Image(label=f"End picture {made.pk}", key=made.file),
        ],
    )
