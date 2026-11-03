from typing import ClassVar

from pydantic import Field

from agents.loop import Refused, Tool
from agents.models import ToolCall


class GiveUp(Tool):
    """End this run when the scene cannot be made, explaining why to the producer."""

    name: ClassVar[str] = "give_up"
    reason: str = Field(description="The plain reason this scene cannot be made.")

    def run(self, call: ToolCall) -> str:
        from . import finish

        if not self.reason.strip():
            raise Refused("say why the scene cannot be made.")
        assert call.agent_fix is not None
        finish(call.agent_fix, self.reason.strip())
        return f"The scene's run gave up: {self.reason.strip()}"
