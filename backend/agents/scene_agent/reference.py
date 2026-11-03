"""The planned page-video moment, after the scene agent has watched it."""

from agents.loop import Refused
from agents.models import ToolCall
from agents.scene_forms import NOT_NEEDED, VideoForm
from jobs import cut_and_send
from jobs.library import SentPiece
from jobs.planning import ScenePiece


def pieces_for(call: ToolCall, form: VideoForm) -> list[SentPiece]:
    assert call.agent_fix is not None
    scene = call.agent_fix.step.scene
    if scene.piece is None:
        return []
    piece = ScenePiece.model_validate(scene.piece)
    if not form.video_for.strip() or NOT_NEEDED.fullmatch(form.video_for):
        raise Refused(
            "Say what to take from the page video in video_for after looking at its frames."
        )
    looked = ToolCall.objects.filter(
        agent_fix__is_scene_agent=True,
        agent_fix__step__scene=scene,
        tool="look_at_library",
        finished_at__isnull=False,
        arguments__video=piece.video,
        arguments__moment=piece.moment,
    ).exclude(pictures=[])
    if not looked.exists():
        raise Refused("Look at the planned page-video moment's frames before filming from it.")
    pieces, _, _, _ = cut_and_send._pieces(scene, [piece])
    return pieces
