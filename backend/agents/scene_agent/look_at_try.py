from agents import fixing_agent
from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall


class LookAtTry(Tool):
    """See an earlier try again, its starting picture, clip and judge's words."""

    name = "look_at_try"
    number: int

    def run(self, call: ToolCall) -> ToolResult:
        fixing = call.agent_fix
        assert fixing is not None
        if self.number == 0 and fixing.first_clip_id is None:
            raise Refused("this scene has no earlier clip to show as try 0.")
        return fixing_agent.LookAtTry(number=self.number).run(call)
