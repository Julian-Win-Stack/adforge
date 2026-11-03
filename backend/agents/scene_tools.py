"""The producer's doors into the scene agent; expanded beside today's B-roll path."""

from pydantic import Field

from agents import hard_list
from agents.loop import Refused, Tool
from agents.models import ToolCall
from jobs import owner_parts, work
from jobs.models import ProducedItem, SceneStep


class StartSceneAgent(Tool):
    """Start one B-roll scene's agent in the background, with the producer's instruction.
    The scene must have a checked line, a presenter and audio already made and heard."""

    name = "start_scene_agent"
    scene: int
    instruction: str = Field(description="What this scene must do, from the producer.")

    def run(self, call: ToolCall) -> str:
        from . import producer

        scene = producer._a_checked_scene(call, self.scene)
        if not scene.shows:
            raise Refused("a talking scene uses make_clip; scene agents own B-roll scenes.")
        if not self.instruction.strip():
            raise Refused("say what this scene must do in the producer's instruction.")
        if work.latest(scene.job, ProducedItem.Kind.PORTRAIT) is None:
            raise Refused("the presenter hasn't been made yet. Create the person first.")
        for kind in (
            SceneStep.Kind.STARTING_PICTURE,
            SceneStep.Kind.LINE_AUDIO,
            SceneStep.Kind.TRANSCRIPT,
            SceneStep.Kind.CLIP,
        ):
            if scene.steps.filter(kind=kind, status=SceneStep.Status.RUNNING).exists():
                raise Refused(
                    f"scene {scene.number}'s {producer.STEP_NAMES[kind]} is still being made."
                )
        part = hard_list.waiting_owner_part(scene)
        if part is not None:
            call.asked_about = "the owner part"
            if not owner_parts.picture_is_current(scene):
                return producer._part_drawn(scene, part, "Its scene agent hasn't started")
            hard_list.refuse_clip_before_the_owners_yes(
                scene, part, producer._library_picture(scene)
            )
        audio = producer._current_audio(scene)
        if not scene.produced.filter(
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
            made_from=audio,
        ).exists():
            raise Refused("the scene's audio hasn't been transcribed yet. Transcribe it first.")
        producer._start_step(
            call,
            scene,
            SceneStep.Kind.SCENE_AGENT,
            busy=f"scene {scene.number}'s agent is already working.",
            note=self.instruction.strip(),
            made_from=audio,
        )
        return (
            f"Started scene {scene.number}'s agent in the background; "
            "you will be told how it ended and what it cost."
        )
