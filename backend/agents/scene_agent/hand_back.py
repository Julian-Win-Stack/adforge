"""A no-render hand-back to the producer, independent of the failed-try ladder."""

from typing import ClassVar

from django.db import transaction
from django.utils import timezone
from pydantic import Field

from agents.loop import Refused, Tool
from agents.models import ToolCall
from chat.models import Message
from jobs import notices

HANDED_BACK = "Handed back:"


class HandBack(Tool):
    """End before drawing or filming for a documented video-model hard limit.
    Tell the producer why and what can be done instead: it sets an overlay or tells the owner.
    """

    name: ClassVar[str] = "hand_back"
    reason: str = Field(min_length=1, description="Plain can't, because reason.")
    alternative: str = Field(min_length=1, description="What can be done instead.")

    def run(self, call: ToolCall) -> str:
        from . import current_run, lifecycle

        assert call.agent_fix is not None
        if not self.reason.strip() or not self.alternative.strip():
            raise Refused("give a plain reason and an alternative for the producer.")
        with transaction.atomic():
            fixing = call.agent_fix
            run = current_run(fixing)
            if run.finished_at is not None:
                raise Refused("this run already ended.")
            if run.starting_pictures or run.end_pictures or run.clips:
                raise Refused(
                    "this early hand-back is only before drawing or filming; "
                    "use give_up after the ladder."
                )
            lifecycle._put_back(fixing.step.scene, run.scene_before)
            run.reason = (
                f"{HANDED_BACK} {self.reason.strip()} Alternative: {self.alternative.strip()}"
            )
            run.finished_at = timezone.now()
            run.save(update_fields=["reason", "finished_at"])
            fixing.kept = fixing.first_clip
            fixing.finished_at = run.finished_at
            fixing.save(update_fields=["kept", "finished_at"])
        # Each one is a note the producer could have answered itself.
        notices.post_notice(
            fixing.step.scene.job,
            notices.SCENE_HANDED_BACK,
            f"Scene {fixing.step.scene.number}: {run.reason}",
            Message.Level.PROBLEM,
            scene=fixing.step.scene.number,
        )
        return run.reason
