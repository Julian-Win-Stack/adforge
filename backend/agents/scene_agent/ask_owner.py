"""A question never stops the scene's work; each run has one question allowance."""

from typing import Literal

from django.db import transaction
from pydantic import Field

from agents.loop import Refused, Tool
from agents.models import SystemNote, ToolCall
from chat.models import Message
from jobs import notices
from jobs.models import OwnerQuestion, SceneAgentRun
from jobs.notices import post_notice


class AskOwner(Tool):
    """Ask once per run for a missing photo or video, then keep working while it waits."""

    name = "ask_owner"
    question: str = Field(min_length=1, description="The plain question for the shop owner.")
    wants: Literal["photo", "video"]

    def run(self, call: ToolCall) -> str:
        from . import current_run

        fixing = call.agent_fix
        assert fixing is not None
        if not self.question.strip():
            raise Refused("write the question for the shop owner.")
        with transaction.atomic():
            run = SceneAgentRun.objects.select_for_update().get(pk=current_run(fixing).pk)
            if OwnerQuestion.objects.filter(tool_call=call).exists():
                return "The question was asked; keep working while the answer waits."
            if run.asked:
                raise Refused("you asked once in this run already; carry on with what you have.")
            question = OwnerQuestion.objects.create(
                scene=fixing.step.scene,
                agent_fix=fixing,
                tool_call=call,
                question=self.question.strip(),
                wants=self.wants,
            )
            run.asked = True
            run.save(update_fields=["asked"])
            post_notice(
                fixing.step.scene.job,
                notices.AGENT_ASKED,
                f"A question about scene {fixing.step.scene.number}, while I keep working: "
                f"{question.question} Send it here if you have one; the ad goes on meanwhile.",
                Message.Level.INFO,
                owner_question=question.pk,
            )
            SystemNote.objects.create(
                session=call.session,
                kind=SystemNote.Kind.AGENT_ASKED,
                text=f"Scene {fixing.step.scene.number}'s agent asked: {question.question} "
                "The scene keeps working; do not ask again or wait. Hand a photo or video "
                "answer back with the scene note tool.",
            )
        return "The question was asked; keep working while the answer waits."
