"""Change one thing in the current picture, preserving its reference context."""

from typing import ClassVar

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import EditForm

from .draw_starting_picture import Pick, current_picture, make_picture


class EditStartingPicture(EditForm, Tool):
    """Edit image 1, keeping your list and changing one thing. Selected references follow
    it. The shared look stays in image 1. The edited picture is checked again and shown back."""

    name: ClassVar[str] = "edit_starting_picture"
    picks: list[Pick]

    def run(self, call: ToolCall) -> ToolResult:
        picture = current_picture(call)
        if picture is None:
            raise Refused("There is no starting picture yet; draw one first.")
        if not self.change.strip() or self.change.strip().lower() == "not needed here":
            raise Refused("Write the one change to the picture.")
        return make_picture(call, self, self.picks, editing=picture)
