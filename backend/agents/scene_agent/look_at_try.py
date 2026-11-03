from typing import ClassVar

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall

from . import media


class LookAtTry(Tool):
    """See a try again: what its clip was sent (the pictures with their labels and the video
    pieces' frames), then its clip's frames and the judge's words. Try 0 is the scene as it
    failed in the ad."""

    name: ClassVar[str] = "look_at_try"
    number: int

    def run(self, call: ToolCall) -> ToolResult:
        fixing = call.agent_fix
        assert fixing is not None
        if self.number == 0 and fixing.first_clip_id is None:
            raise Refused("this scene has no earlier clip to show as try 0.")
        parts = media.try_parts(fixing, self.number)
        if parts is None:
            raise Refused(f"tries so far: 0 to {fixing.tries.count()}.")
        clip, ruling = parts
        label = f"Try {self.number}"
        sent, shown = media.try_shown(label, clip)
        said = (
            f"{label}: what its clip was sent, then the clip's frames. {sent}"
            if sent
            else f"{label}."
        )
        return ToolResult(f"{said} {media.judge_words(ruling)}", shown)
